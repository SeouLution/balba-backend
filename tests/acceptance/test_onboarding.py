"""A01 온보딩: 가게 생성 → 판매 올리기 → 레시피 초안 → 첫 홈."""

from datetime import date

from tests import helpers
from tests.conftest import kst

SALES = {"김밥": (50, 4000), "떡볶이": (20, 5000), "어묵탕": (10, 6000)}
RECIPES = {
    "김밥": [("계란", "개", 1), ("김", "개", 1), ("단무지", "g", 20)],
    "떡볶이": [("떡", "g", 200), ("어묵", "g", 50)],
    "어묵탕": [("어묵", "g", 150)],
}


async def onboard(api, deps, runner) -> dict:
    deps.providers.recipes.recipes = RECIPES
    store_id = (await api.call("createStore", 201, json=helpers.store_body())).json()["id"]
    content = helpers.sales_csv(SALES, date(2026, 9, 29), date(2026, 11, 9))
    import_id = await helpers.upload_sales(api, runner, store_id, content)
    recipes = (await api.call("listRecipes", 200, storeId=store_id)).json()
    menu_ids = {item["menu"]["name"]: item["menu"]["id"] for item in recipes["items"]}
    ingredient_ids = {
        line["ingredient"]["name"]: line["ingredient"]["id"] for item in recipes["items"] for line in item["items"]
    }
    return {"storeId": store_id, "importId": import_id, "ingredients": ingredient_ids, "menus": menu_ids}


async def test_onboarding_flow(api, deps, runner):
    deps.providers.recipes.recipes = RECIPES

    created = await api.call("createStore", 201, json=helpers.store_body())
    store_id = created.json()["id"]
    assert set(created.json()) == {"id", "name", "category", "address", "businessHours"}
    await api.call("putPushToken", 204, storeId=store_id, json={"token": helpers.PUSH_TOKEN})

    before = (await api.call("listRecipes", 200, storeId=store_id)).json()
    assert before == {"generationStatus": "GENERATING", "items": []}

    content = helpers.sales_csv(SALES, date(2026, 9, 29), date(2026, 11, 9))
    accepted = await api.call(
        "createSalesImport", 202, storeId=store_id, files={"file": ("pos.csv", content, "text/csv")}
    )
    import_id = accepted.json()["id"]
    received = (await api.call("getSalesImport", 200, storeId=store_id, importId=import_id)).json()
    assert received == {
        "id": import_id,
        "status": "RECEIVED",
        "posFormat": None,
        "detection": None,
        "period": None,
        "recordCount": None,
    }
    await api.call("confirmSalesImport", 422, storeId=store_id, importId=import_id)

    await runner.run_until_idle()
    parsed = (await api.call("getSalesImport", 200, storeId=store_id, importId=import_id)).json()
    assert parsed["status"] == "NEEDS_CONFIRMATION"
    assert parsed["posFormat"] == {"posSystem": "TOSS_POS"}
    assert parsed["detection"]["columnMapping"] == {"date": "주문일자", "menuName": "상품명", "quantity": "수량"}
    assert parsed["detection"]["menuCount"] == 3
    assert parsed["detection"]["preview"][0] == {"date": "2026-09-29", "menuName": "김밥", "quantity": 50}
    assert len(parsed["detection"]["preview"]) == 5
    assert parsed["period"] == {"from": "2026-09-29", "to": "2026-11-09"}
    assert parsed["recordCount"] == 42 * 3
    assert await helpers.count(deps, "daily_sales") == 0

    await api.call("confirmSalesImport", 202, storeId=store_id, importId=import_id)
    await api.call("confirmSalesImport", 202, storeId=store_id, importId=import_id)
    await runner.run_until_idle()
    completed = (await api.call("getSalesImport", 200, storeId=store_id, importId=import_id)).json()
    assert completed["status"] == "COMPLETED"
    assert completed["detection"] == parsed["detection"]
    sales_total = await helpers.fetch_all(deps, "SELECT sum(quantity) AS total FROM daily_sales")
    assert sales_total[0].total == 42 * 80
    await api.call("confirmSalesImport", 202, storeId=store_id, importId=import_id)
    await runner.run_until_idle()
    assert (await helpers.fetch_all(deps, "SELECT sum(quantity) AS total FROM daily_sales"))[0].total == 42 * 80
    states = await helpers.job_states(deps)
    assert [key for key in states if key.startswith("apply:")] == [f"apply:{import_id}"]
    assert set(states.values()) == {"SUCCEEDED"}

    listed = (await api.call("listRecipes", 200, storeId=store_id)).json()
    assert listed["generationStatus"] == "READY"
    gimbap = next(item for item in listed["items"] if item["menu"]["name"] == "김밥")
    assert gimbap["unit"] == "인분"
    assert sorted((line["ingredient"]["name"], line["quantity"]["value"]) for line in gimbap["items"]) == [
        ("계란", 1),
        ("김", 1),
        ("단무지", 20),
    ]
    egg = next(line for line in gimbap["items"] if line["ingredient"]["name"] == "계란")
    assert egg["quantity"] == {"value": 1, "unit": "개"}
    await api.call(
        "saveRecipes",
        204,
        storeId=store_id,
        json={
            "items": [
                {"menuId": gimbap["menu"]["id"], "items": [{"ingredientId": egg["ingredient"]["id"], "quantity": 2}]}
            ]
        },
    )
    saved = (await api.call("listRecipes", 200, storeId=store_id)).json()["items"]
    saved_gimbap = next(item for item in saved if item["menu"]["name"] == "김밥")
    assert [line["quantity"]["value"] for line in saved_gimbap["items"]] == [2]

    await helpers.enqueue_context(deps, store_id, date(2026, 11, 11), "NIGHT")
    await runner.run_until_idle()
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert home["view"] == "TOMORROW"
    assert home["targetDate"] == "2026-11-11"
    assert home["storeName"] == "행복분식"
    assert [item["menu"]["name"] for item in home["forecast"]["items"]] == ["김밥", "떡볶이", "어묵탕"]
    assert home["forecast"]["totalMenuCount"] == 3
    first = home["forecast"]["items"][0]
    assert (first["recommended"], first["planned"], first["unit"]) == (50, 50, "인분")
    assert first["comparison"] == {"direction": "SIMILAR", "diff": 0}
    assert first["summaryText"] == "평소 수요일 50인분"
    assert first["previousRecommended"] is None and first["produced"] is None
    assert home["banners"] == [
        {"type": "FIRST_FORECAST", "title": "첫 예측이 준비됐어요", "message": "6주치 판매 기록으로 계산했어요"}
    ]
    assert home["order"] is None
    assert "cautions" not in home
    assert home["tiles"] == {"events": []}


async def test_home_switches_view_at_20_and_midnight(api, deps, runner):
    ids = await onboard(api, deps, runner)
    expected = {
        "2026-11-10T19:59:00": ("TODAY", "2026-11-10"),
        "2026-11-10T20:00:00": ("TOMORROW", "2026-11-11"),
        "2026-11-10T23:59:00": ("TOMORROW", "2026-11-11"),
        "2026-11-11T00:00:00": ("TODAY", "2026-11-11"),
    }
    for moment, (view, target_date) in expected.items():
        deps.clock.fixed = kst(moment)
        home = (await api.call("getHome", 200, storeId=ids["storeId"])).json()
        assert (home["view"], home["targetDate"]) == (view, target_date)
        assert ("order" in home) == (view == "TOMORROW")
