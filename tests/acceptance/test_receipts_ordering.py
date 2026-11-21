"""A02 영수증과 발주, A03 당일 아침과 재고 부족 안내."""

from datetime import date

from app.infrastructure.jobs import queue
from tests import helpers
from tests.acceptance.test_onboarding import onboard
from tests.conftest import kst
from tests.fakes import receipt_image

EGG_UNIT_PRICE = 296.666667


def egg(packs: int, pack_price: int) -> dict:
    return {
        "name": "계란 특란 30구",
        "amount": packs * 30,
        "unit": "개",
        "linePrice": packs * pack_price,
        "match": "계란",
        "kind": "exact",
    }


def fishcake(packs: int) -> dict:
    return {
        "name": "부산어묵 1kg",
        "amount": packs * 1000,
        "unit": "g",
        "linePrice": packs * 30000,
        "match": "어묵",
        "kind": "similar",
    }


def rice_cake(grams: int) -> dict:
    return {
        "name": "떡볶이떡 1kg",
        "amount": grams,
        "unit": "g",
        "linePrice": grams * 5,
        "match": "떡",
        "kind": "exact",
    }


EGG_1108 = receipt_image("대한식자재", "2026-11-08", [egg(2, 8900), fishcake(3)], phone="02-123-4567")
EGG_1027 = receipt_image("대한식자재", "2026-10-27", [egg(1, 7900), {"name": "얼룩", "amount": None}])
EGG_1103 = receipt_image("대한식자재", "2026-11-03", [egg(1, 7900)])
FISHCAKE_1109 = receipt_image("대한 식자재", "2026-11-09", [fishcake(10)])
RICE_CAKE_1109 = receipt_image("망원떡집", "2026-11-09", [rice_cake(6000)])
UNREADABLE = receipt_image(None, None, [], readable=False)
ALL_IMAGES = [EGG_1108, EGG_1108, UNREADABLE, EGG_1027, EGG_1103, RICE_CAKE_1109, FISHCAKE_1109]


async def with_receipts(api, deps, runner) -> dict:
    """온보딩 뒤 영수증을 확정하고 떡집의 주문 요일을 화요일로 확인한다."""
    ids = await onboard(api, deps, runner)
    scan_id = await helpers.scan_receipts(api, runner, ids["storeId"], ALL_IMAGES)
    scan = (await api.call("getReceiptScan", 200, storeId=ids["storeId"], scanId=scan_id)).json()
    answers = [{"questionId": question["id"], "answer": "SAME"} for question in scan["questions"]]
    await api.call("confirmReceiptScan", 204, storeId=ids["storeId"], scanId=scan_id, json={"answers": answers})
    suppliers = (await api.call("listSuppliers", 200, storeId=ids["storeId"])).json()["items"]
    ids["suppliers"] = {item["name"]: item["id"] for item in suppliers}
    ids["scanId"] = scan_id
    await api.call(
        "saveOrderDays",
        204,
        storeId=ids["storeId"],
        json={"items": [{"supplierId": ids["suppliers"]["망원떡집"], "orderDays": ["TUE"]}]},
    )
    return ids


def plan_lines(plan: dict) -> dict:
    return {
        line["ingredient"]["name"]: (line["quantity"], line["price"]["unitPrice"], line["stock"], line["eventDiff"])
        for group in plan["suppliers"]
        if group["supplier"]
        for line in group["lines"]
    }


async def test_a02_receipts_then_order(api, deps, runner):
    ids = await onboard(api, deps, runner)
    store_id = ids["storeId"]
    files = [("images", (f"{idx}.jpg", image, "image/jpeg")) for idx, image in enumerate(ALL_IMAGES)]
    scan_id = (await api.call("createReceiptScan", 202, storeId=store_id, files=files)).json()["id"]
    processing = (await api.call("getReceiptScan", 200, storeId=store_id, scanId=scan_id)).json()
    assert processing == {"id": scan_id, "status": "PROCESSING"}
    await api.call("confirmReceiptScan", 422, storeId=store_id, scanId=scan_id)

    await runner.run_until_idle()
    review = (await api.call("getReceiptScan", 200, storeId=store_id, scanId=scan_id)).json()
    assert review["status"] == "NEEDS_REVIEW"
    assert await helpers.count(deps, "receipt_line") == 0
    assert [group["supplier"] for group in review["groups"]] == [
        {"id": None, "name": "대한식자재", "isNew": True},
        {"id": None, "name": "망원떡집", "isNew": True},
    ]
    daehan = review["groups"][0]["receipts"]
    assert [receipt["purchasedOn"] for receipt in daehan] == ["2026-10-27", "2026-11-03", "2026-11-08", "2026-11-09"]
    assert daehan[2]["lines"] == [
        {
            "ingredient": {"id": ids["ingredients"]["계란"], "name": "계란", "isNew": False},
            "quantity": {"value": 60, "unit": "개"},
        },
        {"ingredient": {"id": None, "name": "부산어묵 1kg", "isNew": True}, "quantity": {"value": 3000, "unit": "g"}},
    ]
    assert review["removedDuplicateTexts"] == [
        "11/8 대한식자재 영수증이 두 번 찍혀서 1장은 뺐어요",
        "3번째 사진은 읽지 못해서 뺐어요",
        "10/27 대한식자재 영수증에서 1줄은 읽지 못해서 뺐어요",
    ]
    [question] = review["questions"]
    assert question["rawName"] == "부산어묵 1kg"
    assert question["candidate"] == {"id": ids["ingredients"]["어묵"], "name": "어묵"}
    again = (await api.call("getReceiptScan", 200, storeId=store_id, scanId=scan_id)).json()
    assert again["questions"] == review["questions"]
    assert "linePrice" not in str(review) and "02-123" not in str(review)

    await api.call("confirmReceiptScan", 422, storeId=store_id, scanId=scan_id)
    wrong = {"answers": [{"questionId": "q_other", "answer": "SAME"}]}
    await api.call("confirmReceiptScan", 422, storeId=store_id, scanId=scan_id, json=wrong)
    assert await helpers.count(deps, "receipt_line") == 0 and await helpers.count(deps, "supplier") == 0

    answer = {"answers": [{"questionId": question["id"], "answer": "SAME"}]}
    await api.call("confirmReceiptScan", 204, storeId=store_id, scanId=scan_id, json=answer)
    assert await helpers.count(deps, "receipt_line") == 6
    await api.call("confirmReceiptScan", 204, storeId=store_id, scanId=scan_id, json=answer)
    await api.call("confirmReceiptScan", 204, storeId=store_id, scanId=scan_id)
    different = {"answers": [{"questionId": question["id"], "answer": "DIFFERENT"}]}
    await api.call("confirmReceiptScan", 422, storeId=store_id, scanId=scan_id, json=different)
    assert await helpers.count(deps, "receipt_line") == 6

    confirmed = (await api.call("getReceiptScan", 200, storeId=store_id, scanId=scan_id)).json()
    assert confirmed["status"] == "CONFIRMED" and confirmed["questions"] == []
    assert all(group["supplier"]["id"] for group in confirmed["groups"])
    fishcake_line = confirmed["groups"][0]["receipts"][2]["lines"][1]
    assert fishcake_line["ingredient"] == {"id": ids["ingredients"]["어묵"], "name": "어묵", "isNew": False}

    suppliers = (await api.call("listSuppliers", 200, storeId=store_id)).json()["items"]
    by_name = {item["name"]: item for item in suppliers}
    assert by_name["대한식자재"]["orderDays"] == ["MON", "TUE", "SUN"]
    assert (by_name["대한식자재"]["orderDaysStatus"], by_name["대한식자재"]["receiptCount"]) == ("ESTIMATED", 4)
    assert (by_name["망원떡집"]["orderDays"], by_name["망원떡집"]["orderDaysStatus"]) == ([], "UNKNOWN")
    assert by_name["망원떡집"]["receiptCount"] == 1
    assert all("phone" not in item for item in suppliers)

    plan = (await api.call("getOrderPlan", 200, storeId=store_id, targetDate="2026-11-11")).json()
    assert [group["supplier"]["name"] for group in plan["suppliers"] if group["supplier"]] == ["대한식자재"]
    await api.call(
        "saveOrderDays",
        422,
        storeId=store_id,
        json={"items": [{"supplierId": by_name["망원떡집"]["id"], "orderDays": ["TUE", "TUE"]}]},
    )
    days = {"items": [{"supplierId": by_name["망원떡집"]["id"], "orderDays": ["THU", "TUE"]}]}
    await api.call("saveOrderDays", 204, storeId=store_id, json=days)
    saved = (await api.call("listSuppliers", 200, storeId=store_id)).json()["items"]
    assert next(item for item in saved if item["name"] == "망원떡집")["orderDays"] == ["TUE", "THU"]

    plan = (await api.call("getOrderPlan", 200, storeId=store_id, targetDate="2026-11-11")).json()
    assert plan_lines(plan) == {
        "계란": ({"value": 50, "unit": "개"}, EGG_UNIT_PRICE, {"value": -580, "unit": "개"}, 0),
        "떡": ({"value": 2000, "unit": "g"}, 5, {"value": 2000, "unit": "g"}, 0),
    }
    assert (plan["usualTotal"], plan["events"]) == (24833, [])
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert home["order"] == {"supplierCount": 2, "estimatedTotal": 24833}

    gimbap = ids["menus"]["김밥"]
    adjusted = await api.call(
        "adjustForecast", 200, storeId=store_id, date="2026-11-11", menuId=gimbap, json={"quantity": 80}
    )
    assert adjusted.json()["item"]["planned"] == 80 and adjusted.json()["item"]["recommended"] == 50
    assert adjusted.json()["order"] == {"supplierCount": 2, "estimatedTotal": 33733}
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert home["order"] == adjusted.json()["order"]
    assert home["forecast"]["items"][0]["planned"] == 80
    plan = (await api.call("getOrderPlan", 200, storeId=store_id, targetDate="2026-11-11")).json()
    assert plan_lines(plan)["계란"][0] == {"value": 80, "unit": "개"}
    assert plan["usualTotal"] == 24833

    stock_before = plan_lines(plan)
    order_body = {
        "targetDate": "2026-11-11",
        "suppliers": [
            {
                "supplierId": by_name["대한식자재"]["id"],
                "lines": [{"ingredientId": ids["ingredients"]["계란"], "quantity": 60}],
            },
            {
                "supplierId": by_name["망원떡집"]["id"],
                "lines": [{"ingredientId": ids["ingredients"]["떡"], "quantity": 4500.5}],
            },
        ],
    }
    created = (await api.call("createPurchaseOrders", 201, storeId=store_id, json=order_body)).json()["items"]
    orders = {order["supplier"]["name"]: order for order in created}
    assert orders["대한식자재"]["messageText"] == (
        "[행복분식] 11/11(수) 발주\n계란 특란 30구 × 60개\n내일 오전 10시 전 도착 부탁드립니다."
    )
    assert orders["망원떡집"]["messageText"].splitlines()[1] == "떡볶이떡 1kg × 4500.5g"
    assert orders["대한식자재"]["supplier"]["phone"] == "02-123-4567"
    assert orders["대한식자재"]["supplier"]["phoneConfirmed"] is False
    assert orders["대한식자재"]["supplier"]["phoneSourceText"] == "11/8 영수증에서 읽었어요. 다음부터는 묻지 않아요"
    assert orders["망원떡집"]["supplier"]["phone"] is None and orders["망원떡집"]["supplier"]["phoneSourceText"] is None

    order_id = orders["대한식자재"]["id"]
    await api.call(
        "recordSent",
        204,
        storeId=store_id,
        purchaseOrderId=order_id,
        json={"channel": "SMS", "recipientPhone": "010-1234-5678"},
    )
    sent = await helpers.fetch_all(deps, "SELECT sent_channel, sent_at FROM purchase_order WHERE id = :id", id=order_id)
    deps.clock.fixed = kst("2026-11-10T20:45:00")
    await api.call(
        "recordSent",
        204,
        storeId=store_id,
        purchaseOrderId=order_id,
        json={"channel": "SMS", "recipientPhone": "01012345678"},
    )
    await api.call("recordSent", 204, storeId=store_id, purchaseOrderId=order_id, json={"channel": "SMS"})
    assert (
        await helpers.fetch_all(deps, "SELECT sent_channel, sent_at FROM purchase_order WHERE id = :id", id=order_id)
        == sent
    )
    await api.call("recordSent", 204, storeId=store_id, purchaseOrderId=order_id, json={"channel": "COPY"})
    resent = await helpers.fetch_all(
        deps, "SELECT sent_channel, sent_at FROM purchase_order WHERE id = :id", id=order_id
    )
    assert resent[0].sent_channel == "COPY" and resent[0].sent_at > sent[0].sent_at
    phone = await helpers.fetch_all(
        deps, "SELECT phone, phone_confirmed, phone_read_on FROM supplier WHERE name = '대한식자재'"
    )
    assert tuple(phone[0]) == ("01012345678", True, None)
    await api.call(
        "recordSent", 422, storeId=store_id, purchaseOrderId=order_id, json={"channel": "SMS", "recipientPhone": "  "}
    )

    await helpers.execute(deps, "UPDATE receipt_line SET unit_price = unit_price * 2")
    history = (await api.call("listPurchaseOrders", 200, storeId=store_id)).json()["items"]
    assert {item["supplier"]["name"]: item["totalAmount"] for item in history} == {
        "대한식자재": 17800,
        "망원떡집": 22503,
    }
    assert await helpers.count(deps, "receipt_line") == 6
    await helpers.execute(deps, "UPDATE receipt_line SET unit_price = unit_price / 2")
    after = (await api.call("getOrderPlan", 200, storeId=store_id, targetDate="2026-11-11")).json()
    assert plan_lines(after) == stock_before


async def test_a02_same_photo_in_two_scans_is_applied_once(api, deps, runner):
    ids = await with_receipts(api, deps, runner)
    store_id = ids["storeId"]
    other_item = receipt_image(
        "대한식자재",
        "2026-11-08",
        [{"name": "단무지 3kg", "amount": 3000, "unit": "g", "match": "단무지", "kind": "exact"}],
    )
    scan_a = await helpers.scan_receipts(api, runner, store_id, [other_item])
    scan_b = await helpers.scan_receipts(api, runner, store_id, [other_item, EGG_1108])
    review_b = (await api.call("getReceiptScan", 200, storeId=store_id, scanId=scan_b)).json()
    assert review_b["questions"] == []
    assert review_b["removedDuplicateTexts"] == ["11/8 대한식자재 영수증이 두 번 찍혀서 1장은 뺐어요"]
    assert review_b["groups"][0]["supplier"] == {
        "id": ids["suppliers"]["대한식자재"],
        "name": "대한식자재",
        "isNew": False,
    }

    lines_before = await helpers.count(deps, "receipt_line")
    await api.call("confirmReceiptScan", 204, storeId=store_id, scanId=scan_a)
    await api.call("confirmReceiptScan", 204, storeId=store_id, scanId=scan_b, json={})
    assert await helpers.count(deps, "receipt_line") == lines_before + 1
    confirmed_b = (await api.call("getReceiptScan", 200, storeId=store_id, scanId=scan_b)).json()
    assert confirmed_b["groups"] == []
    assert confirmed_b["removedDuplicateTexts"] == ["11/8 대한식자재 영수증이 두 번 찍혀서 1장은 뺐어요"] * 2
    receipts_on_day = await helpers.fetch_all(
        deps,
        "SELECT count(*) AS n FROM receipt_line WHERE purchased_on = '2026-11-08' AND supplier_id = :id",
        id=ids["suppliers"]["대한식자재"],
    )
    assert receipts_on_day[0].n == 3
    suppliers = (await api.call("listSuppliers", 200, storeId=store_id)).json()["items"]
    assert next(item for item in suppliers if item["name"] == "망원떡집")["orderDaysStatus"] == "CONFIRMED"

    linked = await helpers.fetch_all(
        deps, "SELECT DISTINCT ingredient_id FROM receipt_line WHERE product_name = '부산어묵 1kg'"
    )
    assert [row.ingredient_id for row in linked] == [ids["ingredients"]["어묵"]]


async def test_receipt_answers_units_and_empty_scan(api, deps, runner):
    ids = await onboard(api, deps, runner)
    store_id = ids["storeId"]
    different = receipt_image(
        "새벽상회",
        "2026-11-09",
        [
            fishcake(2),
            {"name": "계란 10kg", "amount": 10000, "unit": "g", "match": "계란", "kind": "exact"},
            {"name": "참기름 1.8L", "amount": 1800, "unit": "g", "linePrice": 25000},
            {"name": "양파 1망", "amount": 1, "unit": "망"},
            rice_cake(1000),
        ],
    )
    scan_id = await helpers.scan_receipts(api, runner, store_id, [different])
    review = (await api.call("getReceiptScan", 200, storeId=store_id, scanId=scan_id)).json()
    assert "참기름 1.8L" not in str(review["groups"])
    answers = {"answers": [{"questionId": review["questions"][0]["id"], "answer": "DIFFERENT"}]}
    await api.call("confirmReceiptScan", 204, storeId=store_id, scanId=scan_id, json=answers)
    confirmed = (await api.call("getReceiptScan", 200, storeId=store_id, scanId=scan_id)).json()
    names = [line["ingredient"] for line in confirmed["groups"][0]["receipts"][0]["lines"]]
    assert names == [{"id": ids["ingredients"]["떡"], "name": "떡", "isNew": False}]
    dropped_notices = [
        "11/9 새벽상회 영수증에서 1줄은 읽지 못해서 뺐어요",
        "11/9 새벽상회 영수증에서 재료 목록에 없는 1줄은 뺐어요",
    ]
    assert confirmed["removedDuplicateTexts"] == dropped_notices * 2

    listing = (await api.call("listIngredients", 200, storeId=store_id)).json()
    by_name = {item["name"]: item for item in listing["items"]}
    assert sorted(by_name) == ["계란", "김", "단무지", "떡", "어묵"]
    assert by_name["떡"]["price"] == {"unitPrice": 5}
    assert by_name["계란"]["price"] is None and by_name["어묵"]["price"] is None

    empty_scan = await helpers.scan_receipts(api, runner, store_id, [UNREADABLE])
    empty = (await api.call("getReceiptScan", 200, storeId=store_id, scanId=empty_scan)).json()
    assert (empty["status"], empty["groups"], empty["questions"]) == ("NEEDS_REVIEW", [], [])
    assert empty["removedDuplicateTexts"] == ["1번째 사진은 읽지 못해서 뺐어요"]
    lines_before = await helpers.count(deps, "receipt_line")
    await api.call("confirmReceiptScan", 204, storeId=store_id, scanId=empty_scan)
    assert await helpers.count(deps, "receipt_line") == lines_before


async def test_a03_morning_and_low_stock(api, deps, runner):
    ids = await with_receipts(api, deps, runner)
    store_id, ingredients, suppliers = ids["storeId"], ids["ingredients"], ids["suppliers"]
    order_message = "발주해 주세요 · 이미 발주했다면 영수증을 올려주세요"

    def low_stock(home):
        return [banner for banner in home["banners"] if banner["type"] == "LOW_STOCK"]

    counts_before = await helpers.row_counts(deps)
    evening = (await api.call("getHome", 200, storeId=store_id)).json()
    assert await helpers.row_counts(deps) == counts_before
    assert [banner["type"] for banner in evening["banners"]] == ["FIRST_FORECAST", "LOW_STOCK"]
    assert low_stock(evening) == [
        {"type": "LOW_STOCK", "title": "계란·떡 재고가 얼마 안 남았어요", "message": order_message}
    ]

    def order_body(supplier_name, ingredient_name, quantity):
        return {
            "targetDate": "2026-11-11",
            "suppliers": [
                {
                    "supplierId": suppliers[supplier_name],
                    "lines": [{"ingredientId": ingredients[ingredient_name], "quantity": quantity}],
                }
            ],
        }

    [egg_order] = (
        await api.call("createPurchaseOrders", 201, storeId=store_id, json=order_body("대한식자재", "계란", 50))
    ).json()["items"]
    [rice_order] = (
        await api.call("createPurchaseOrders", 201, storeId=store_id, json=order_body("망원떡집", "떡", 6000))
    ).json()["items"]

    await api.call("putPushToken", 204, storeId=store_id, json={"token": helpers.PUSH_TOKEN})
    await helpers.enqueue_context(deps, store_id, date(2026, 11, 11), "NIGHT")
    async with deps.db.write() as uow:
        await queue.enqueue_push(uow, store_id, date(2026, 11, 11), "NIGHT")
    await runner.run_until_idle()
    await helpers.release_postponed_jobs(deps)
    await runner.run_until_idle()
    assert deps.providers.push.sent[0][2] == "김밥 50인분 등 3개 메뉴 · 계란 외 1개 발주가 필요해요"

    deps.clock.fixed = kst("2026-11-11T07:40:00")
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert (home["view"], home["targetDate"]) == ("TODAY", "2026-11-11")
    assert "order" not in home
    assert [banner["type"] for banner in home["banners"]] == ["LOW_STOCK"]
    await api.call("recordSent", 204, storeId=store_id, purchaseOrderId=egg_order["id"], json={"channel": "KAKAOTALK"})
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert home["banners"][0] == {
        "type": "DELIVERY_EXPECTED",
        "title": "어젯밤 발주한 재료가 오전에 들어와요",
        "message": "계란 50개",
    }
    assert low_stock(home)[0]["message"] == order_message
    await api.call("recordSent", 204, storeId=store_id, purchaseOrderId=rice_order["id"], json={"channel": "COPY"})
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert low_stock(home)[0]["message"] == "이미 발주한 재료예요 · 받았다면 영수증을 올려주세요"

    gimbap = ids["menus"]["김밥"]
    sales_before = await helpers.count(deps, "daily_sales")
    await api.call("setProduction", 204, storeId=store_id, date="2026-11-11", menuId=gimbap, json={"quantity": 50})
    await api.call("setProduction", 204, storeId=store_id, date="2026-11-11", menuId=gimbap, json={"quantity": 50})
    await api.call("setProduction", 422, storeId=store_id, date="2026-11-10", menuId=gimbap, json={"quantity": 1})
    await api.call("setProduction", 404, storeId=store_id, date="2026-11-20", menuId=gimbap, json={"quantity": 1})
    produced = (await api.call("getHome", 200, storeId=store_id)).json()
    assert produced["forecast"]["items"][0]["produced"] == 50
    assert await helpers.count(deps, "daily_sales") == sales_before
    assert low_stock(produced) == low_stock(home)

    delivered = receipt_image("대한식자재", "2026-11-11", [egg(30, 8900)])
    scan_id = await helpers.scan_receipts(api, runner, store_id, [delivered])
    await api.call("confirmReceiptScan", 204, storeId=store_id, scanId=scan_id)
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert low_stock(home)[0]["title"] == "떡 재고가 얼마 안 남았어요"
    assert low_stock(home)[0]["message"] == "이미 발주한 재료예요 · 받았다면 영수증을 올려주세요"

    refilled = receipt_image("망원떡집", "2026-11-11", [rice_cake(6000)])
    scan_id = await helpers.scan_receipts(api, runner, store_id, [refilled])
    await api.call("confirmReceiptScan", 204, storeId=store_id, scanId=scan_id)
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert low_stock(home) == []
