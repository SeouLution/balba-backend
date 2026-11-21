"""엔드포인트 문서의 완료 조건 중 인수 흐름이 다루지 않는 경계."""

from datetime import date

import pytest

from tests import helpers
from tests.acceptance.test_onboarding import SALES, onboard
from tests.acceptance.test_receipts_ordering import with_receipts
from tests.conftest import kst
from tests.fakes import JPEG_HEAD, receipt_image


async def new_store(api, **overrides) -> str:
    return (await api.call("createStore", 201, json=helpers.store_body(**overrides))).json()["id"]


# ───────── createStore · putPushToken ─────────


async def test_create_store_normalizes_hours_and_registers_geocode(api, deps):
    hours = helpers.business_hours(open_time="18:00", close_time="02:00", closed_days=("MON",))
    hours[0] = {"day": "MON", "closed": True, "open": "09:00", "close": "10:00"}
    body = helpers.store_body(name="  심야분식  ", businessHours=list(reversed(hours)))
    created = (await api.call("createStore", 201, json=body)).json()
    assert created["name"] == "심야분식"
    assert [item["day"] for item in created["businessHours"]] == helpers.DAYS
    assert created["businessHours"][0] == {"day": "MON", "closed": True}
    assert created["businessHours"][1] == {"day": "TUE", "closed": False, "open": "18:00", "close": "02:00"}
    home = (await api.call("getHome", 200, storeId=created["id"])).json()
    assert home["businessHours"] == created["businessHours"]
    assert await helpers.job_states(deps) == {f"geocode:{created['id']}": "READY"}
    row = (await helpers.fetch_all(deps, "SELECT generation_status, latitude, push_token FROM store"))[0]
    assert tuple(row) == ("GENERATING", None, None)
    await api.call(
        "createStore", 201, json=helpers.store_body(businessHours=helpers.business_hours(closed_days=helpers.DAYS))
    )
    assert await helpers.count(deps, "store") == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "   "},
        {"name": "가" * 41},
        {"category": "CAFE"},
        {"address": {"roadAddress": "  "}},
        {"businessHours": helpers.business_hours()[:6]},
        {"businessHours": helpers.business_hours()[:6] + [helpers.business_hours()[0]]},
        {"businessHours": helpers.business_hours(open_time="10:00", close_time="10:00")},
        {"businessHours": helpers.business_hours(open_time="24:00")},
        {"businessHours": [{"day": day, "closed": False} for day in helpers.DAYS]},
        {"businessHours": [{"day": day, "closed": "no"} for day in helpers.DAYS]},
    ],
)
async def test_create_store_rejects_invalid_input(api, deps, overrides):
    await api.call("createStore", 422, json=helpers.store_body(**overrides))
    assert await helpers.count(deps, "store") == 0


async def test_update_store_replaces_fields_and_refreshes_location_or_forecast(api, deps):
    store_id = await new_store(api)
    await helpers.execute(deps, "UPDATE store SET latitude = 37.5, longitude = 126.9")
    moved = helpers.store_body(name="오늘애김밥 성수역점", address={"roadAddress": "서울 성동구 아차산로 126"})
    updated = (await api.call("updateStore", 200, storeId=store_id, json=moved)).json()
    assert (updated["id"], updated["name"], updated["address"]) == (store_id, "오늘애김밥 성수역점", moved["address"])
    assert tuple((await helpers.fetch_all(deps, "SELECT latitude, longitude FROM store"))[0]) == (None, None)
    jobs = await helpers.job_states(deps)
    assert sum(key.startswith(f"geocode:{store_id}:chg_") for key in jobs) == 1

    rehoured = {**moved, "businessHours": helpers.business_hours(closed_days=("SUN",))}
    assert (await api.call("updateStore", 200, storeId=store_id, json=rehoured)).json()["businessHours"][6] == {
        "day": "SUN",
        "closed": True,
    }
    assert sum(key.startswith(f"forecast:{store_id}:") for key in await helpers.job_states(deps)) == 2
    await api.call("updateStore", 422, storeId=store_id, json=helpers.store_body(name=" "))


async def test_push_token_is_replaced_and_validated(api, deps):
    store_id = await new_store(api)
    await api.call("putPushToken", 204, storeId=store_id, json={"token": helpers.PUSH_TOKEN})
    await api.call("putPushToken", 204, storeId=store_id, json={"token": helpers.PUSH_TOKEN})
    other = helpers.PUSH_TOKEN.replace("abc", "other-device")
    await api.call("putPushToken", 204, storeId=store_id, json={"token": other})
    assert (await helpers.fetch_all(deps, "SELECT push_token FROM store"))[0].push_token == other
    for invalid in (
        "plain-token",
        '{"endpoint": "http://insecure", "keys": {"p256dh": "a", "auth": "b"}}',
        "x" * 4097,
        3,
    ):
        await api.call("putPushToken", 422, storeId=store_id, json={"token": invalid})
    assert (await helpers.fetch_all(deps, "SELECT push_token FROM store"))[0].push_token == other


# ───────── 업로드 ─────────


async def test_sales_upload_checks_extension_signature_and_size(api, deps):
    store_id = await new_store(api)
    cases = {
        "sales.txt": b"a,b\n1,2\n",
        "sales.csv": b"",
        "sales.xlsx": b"not a zip file",
        "sales.xls": b"PK\x03\x04 is xlsx, not xls",
        "binary.csv": b"\x00\x01\x02\x03",
    }
    for filename, content in cases.items():
        await api.call("createSalesImport", 422, storeId=store_id, files={"file": (filename, content, "text/csv")})
    too_big = b"a" * (deps.policy.sales_file_max_bytes + 1)
    await api.call("createSalesImport", 422, storeId=store_id, files={"file": ("big.csv", too_big, "text/csv")})
    missing_field = await api.client.post(
        f"/stores/{store_id}/sales-imports", files={"other": ("a.csv", b"a", "text/csv")}
    )
    assert (missing_field.status_code, missing_field.json()) == (422, {"code": "INVALID_REQUEST"})
    assert await helpers.count(deps, "sales_import") == 0
    assert not list(deps.files.root.rglob("*.csv"))

    await api.call(
        "createSalesImport", 202, storeId=store_id, files={"file": ("ok.CSV", "날짜\n".encode("cp949"), "text/csv")}
    )
    stored = (await helpers.fetch_all(deps, "SELECT file_path FROM sales_import"))[0].file_path
    assert stored.startswith(f"{store_id}/sales/") and "ok" not in stored


async def test_xlsx_file_is_parsed(api, deps, runner, tmp_path):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["주문일자", "상품명", "수량", "결제금액"])
    sheet.append([date(2026, 11, 9), "김밥", 3, 12000])
    sheet.append(["합계", None, 3, 12000])
    path = tmp_path / "pos.xlsx"
    workbook.save(path)
    store_id = await new_store(api)
    import_id = await helpers.upload_sales(api, runner, store_id, path.read_bytes(), filename="pos.xlsx")
    view = (await api.call("getSalesImport", 200, storeId=store_id, importId=import_id)).json()
    assert (view["status"], view["recordCount"], view["period"]) == (
        "COMPLETED",
        1,
        {"from": "2026-11-09", "to": "2026-11-09"},
    )


async def test_receipt_upload_limits(api, deps):
    store_id = await new_store(api)
    image = receipt_image("대한식자재", "2026-11-08", [])

    def files(count, content=image):
        return [("images", (f"{idx}.jpg", content, "image/jpeg")) for idx in range(count)]

    await api.call("createReceiptScan", 422, storeId=store_id, files=files(31))
    await api.call("createReceiptScan", 422, storeId=store_id, files=files(2)[:1] + files(1, b"\x89PNG not jpeg"))
    await api.call(
        "createReceiptScan",
        422,
        storeId=store_id,
        files=files(1, JPEG_HEAD + b"x" * deps.policy.receipt_image_max_bytes),
    )
    no_images = await api.client.post(
        f"/stores/{store_id}/receipt-scans", files={"other": ("a.jpg", image, "image/jpeg")}
    )
    assert (no_images.status_code, no_images.json()) == (422, {"code": "INVALID_REQUEST"})
    assert await helpers.count(deps, "receipt_scan") == 0
    assert not list(deps.files.root.rglob("*.jpg"))

    await api.call("createReceiptScan", 202, storeId=store_id, files=files(30))
    assert await helpers.count(deps, "receipt_line") == 0
    assert len((await helpers.fetch_all(deps, "SELECT images FROM receipt_scan"))[0].images) == 30


# ───────── 레시피 ─────────


async def test_save_recipes_replaces_whole_recipe_of_listed_menus(api, deps, runner):
    ids = await onboard(api, deps, runner)
    store_id, ingredients, menus = ids["storeId"], ids["ingredients"], ids["menus"]

    def body(menu_name, lines):
        items = [{"ingredientId": ingredients[name], "quantity": quantity} for name, quantity in lines]
        return {"items": [{"menuId": menus[menu_name], "items": items}]}

    async def recipe_rows():
        rows = await helpers.fetch_all(
            deps,
            "SELECT m.name AS menu, i.name AS ingredient, r.quantity FROM recipe_item r "
            "JOIN menu m ON m.id = r.menu_id JOIN ingredient i ON i.id = r.ingredient_id ORDER BY 1, 2",
        )
        return [(row.menu, row.ingredient, float(row.quantity)) for row in rows]

    before = await recipe_rows()
    await api.call("saveRecipes", 422, storeId=store_id, json={"items": []})
    await api.call("saveRecipes", 422, storeId=store_id, json=body("김밥", [("계란", 1), ("계란", 2)]))
    await api.call("saveRecipes", 422, storeId=store_id, json=body("김밥", [("계란", -1)]))
    mixed = {"items": body("떡볶이", [("어묵", 70)])["items"] + [{"menuId": "mn_missing", "items": []}]}
    await api.call("saveRecipes", 404, storeId=store_id, json=mixed)
    assert await recipe_rows() == before

    await api.call("saveRecipes", 204, storeId=store_id, json=body("떡볶이", [("어묵", 70.1234567), ("계란", 0)]))
    await api.call("saveRecipes", 204, storeId=store_id, json=body("떡볶이", [("어묵", 70.1234567), ("계란", 0)]))
    assert await recipe_rows() == [
        ("김밥", "계란", 1.0),
        ("김밥", "김", 1.0),
        ("김밥", "단무지", 20.0),
        ("떡볶이", "계란", 0.0),
        ("떡볶이", "어묵", 70.123457),
        ("어묵탕", "어묵", 150.0),
    ]
    await api.call("saveRecipes", 204, storeId=store_id, json=body("김밥", []))
    assert [row[0] for row in await recipe_rows()] == ["떡볶이", "떡볶이", "어묵탕"]

    await helpers.execute(deps, "UPDATE store SET generation_status = 'GENERATING' WHERE id = :id", id=store_id)
    await api.call("saveRecipes", 422, storeId=store_id, json=body("떡볶이", [("어묵", 1)]))
    generating = (await api.call("listRecipes", 200, storeId=store_id)).json()
    assert generating == {"generationStatus": "GENERATING", "items": []}


# ───────── 준비량 · 발주 ─────────


async def test_adjust_forecast_rules(api, deps, runner):
    ids = await onboard(api, deps, runner)
    store_id, gimbap = ids["storeId"], ids["menus"]["김밥"]
    other = await onboard(api, deps, runner)

    today = await api.call(
        "adjustForecast", 200, storeId=store_id, date="2026-11-10", menuId=gimbap, json={"quantity": 12.3456789}
    )
    assert today.json() == {
        "item": {
            "menu": {"id": gimbap, "name": "김밥"},
            "unit": "인분",
            "recommended": 50,
            "planned": 12.345679,
            "comparison": {"direction": "SIMILAR", "diff": 0},
            "eventDiff": 0,
            "previousRecommended": None,
            "produced": None,
            "summaryText": "평소 화요일 50인분",
        },
        "order": None,
    }
    zero = await api.call(
        "adjustForecast", 200, storeId=store_id, date="2026-11-11", menuId=gimbap, json={"quantity": 0}
    )
    assert zero.json()["item"]["planned"] == 0 and zero.json()["order"] is None
    await api.call("adjustForecast", 422, storeId=store_id, date="2026-11-12", menuId=gimbap, json={"quantity": 1})
    await api.call(
        "adjustForecast", 404, storeId=store_id, date="2026-11-11", menuId=other["menus"]["김밥"], json={"quantity": 1}
    )
    await helpers.execute(deps, "DELETE FROM forecast WHERE menu_id = :id AND target_date = '2026-11-10'", id=gimbap)
    await api.call("adjustForecast", 404, storeId=store_id, date="2026-11-10", menuId=gimbap, json={"quantity": 1})

    await helpers.execute(deps, "DELETE FROM background_job")
    await helpers.enqueue_context(deps, store_id, date(2026, 11, 11), "NIGHT")
    await runner.run_until_idle()
    kept = await helpers.fetch_all(
        deps, "SELECT adjusted, recommended FROM forecast WHERE menu_id = :id AND target_date = '2026-11-11'", id=gimbap
    )
    assert tuple(kept[0]) == (0, 50)


async def test_order_plan_and_purchase_order_rules(api, deps, runner):
    ids = await with_receipts(api, deps, runner)
    store_id, ingredients, suppliers = ids["storeId"], ids["ingredients"], ids["suppliers"]
    other = await with_receipts(api, deps, runner)

    await api.call("getOrderPlan", 422, storeId=store_id, targetDate="2026-11-12")
    await api.call("getOrderPlan", 422, storeId=store_id, targetDate="2026-11-09")

    def order(target_date="2026-11-11", **lines_by_supplier):
        return {
            "targetDate": target_date,
            "suppliers": [
                {
                    "supplierId": suppliers[name],
                    "lines": [
                        {"ingredientId": ingredients[ingredient], "quantity": quantity}
                        for ingredient, quantity in lines
                    ],
                }
                for name, lines in lines_by_supplier.items()
            ],
        }

    egg = [("계란", 60)]
    invalid_bodies = [
        {"targetDate": "2026-11-11", "suppliers": []},
        order(대한식자재=[("계란", 1), ("계란", 2)]),
        order(대한식자재=egg, 망원떡집=egg),
        order(대한식자재=[("계란", 0)]),
        order(대한식자재=egg, 망원떡집=[("떡", 0)]),
        order(대한식자재=[("떡", 1)]),
        order(대한식자재=[("김", 1)]),
        order(target_date="2026-11-12", 대한식자재=egg),
    ]
    for body in invalid_bodies:
        await api.call("createPurchaseOrders", 422, storeId=store_id, json=body)
    foreign_supplier = order(대한식자재=egg)
    foreign_supplier["suppliers"][0]["supplierId"] = other["suppliers"]["대한식자재"]
    await api.call("createPurchaseOrders", 404, storeId=store_id, json=foreign_supplier)
    foreign_ingredient = order(대한식자재=egg)
    foreign_ingredient["suppliers"][0]["lines"][0]["ingredientId"] = other["ingredients"]["계란"]
    await api.call("createPurchaseOrders", 404, storeId=store_id, json=foreign_ingredient)
    assert await helpers.count(deps, "purchase_order") == 0
    assert (await api.call("listPurchaseOrders", 200, storeId=store_id)).json() == {"items": []}

    await api.call("createPurchaseOrders", 201, storeId=store_id, json=order(대한식자재=[("계란", 60), ("어묵", 0)]))
    await api.call("createPurchaseOrders", 201, storeId=store_id, json=order(대한식자재=egg))
    lines = await helpers.fetch_all(deps, "SELECT quantity FROM purchase_order_line")
    assert [float(line.quantity) for line in lines] == [60.0, 60.0]
    listed = (await api.call("listPurchaseOrders", 200, storeId=store_id)).json()["items"]
    assert len(listed) == 2 and all(item["totalAmount"] == 17800 for item in listed)
    assert (await api.call("listPurchaseOrders", 200, storeId=other["storeId"])).json() == {"items": []}
    await api.call(
        "recordSent", 404, storeId=other["storeId"], purchaseOrderId=listed[0]["id"], json={"channel": "COPY"}
    )

    deps.clock.fixed = kst("2026-11-11T20:15:00")
    wednesday = (await api.call("getOrderPlan", 200, storeId=store_id, targetDate="2026-11-12")).json()
    assert wednesday["suppliers"] == []
    await api.call("createPurchaseOrders", 422, storeId=store_id, json=order(target_date="2026-11-12", 대한식자재=egg))


async def test_closed_target_day_has_no_forecast_or_order(api, deps, runner):
    hours = helpers.business_hours(closed_days=("WED",))
    deps.providers.recipes.recipes = {"김밥": [("계란", "개", 1)]}
    store_id = await new_store(api, businessHours=hours)
    await helpers.upload_sales(api, runner, store_id, helpers.sales_csv(SALES, date(2026, 9, 29), date(2026, 11, 9)))
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert (home["targetDate"], home["forecast"]) == ("2026-11-11", {"items": [], "totalMenuCount": 3})
    assert home["order"] is None and home["banners"] == []
    plan = (await api.call("getOrderPlan", 200, storeId=store_id, targetDate="2026-11-11")).json()
    assert plan == {"targetDate": "2026-11-11", "events": [], "usualTotal": None, "suppliers": []}
