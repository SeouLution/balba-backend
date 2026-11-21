"""A01 온보딩: 가게 생성 → 판매 올리기 → 레시피 초안 → 핵심 재료 → 첫 홈."""

from datetime import date

from tests import helpers
from tests.conftest import kst

SALES = {"김밥": (50, 4000), "떡볶이": (20, 5000), "어묵탕": (10, 6000)}
RECIPES = {
    "김밥": [("계란", "개", 1), ("김", "장", 1), ("단무지", "g", 20)],
    "떡볶이": [("떡", "g", 200), ("어묵", "g", 50)],
    "어묵탕": [("어묵", "g", 150)],
}
PURCHASE = {"계란": ("판", 30), "김": ("속", 100), "어묵": ("봉", 1000), "떡": ("kg", 1000)}


async def onboard(api, deps, runner) -> dict:
    deps.providers.recipes.recipes = RECIPES
    deps.providers.recipes.purchase = PURCHASE
    store_id = (await api.call("createStore", 201, json=helpers.store_body())).json()["id"]
    content = helpers.sales_csv(SALES, date(2026, 9, 29), date(2026, 11, 9))
    import_id = await helpers.upload_sales(api, runner, store_id, content)
    candidates = (await api.call("listIngredientCandidates", 200, storeId=store_id)).json()
    ingredient_ids = {item["name"]: item["id"] for item in candidates["items"]}
    core = [ingredient_ids[name] for name in ("계란", "어묵", "떡")]
    await api.call("setCoreIngredients", 204, storeId=store_id, json={"ingredientIds": core})
    recipes = (await api.call("listRecipes", 200, storeId=store_id)).json()
    menu_ids = {item["menu"]["name"]: item["menu"]["id"] for item in recipes["items"]}
    return {"storeId": store_id, "importId": import_id, "ingredients": ingredient_ids, "menus": menu_ids}


async def test_onboarding_flow(api, deps, runner):
    deps.providers.recipes.recipes = RECIPES
    deps.providers.recipes.purchase = PURCHASE

    created = await api.call("createStore", 201, json=helpers.store_body())
    store_id = created.json()["id"]
    assert set(created.json()) == {"id", "name", "category", "address", "businessHours"}
    await api.call("putPushToken", 204, storeId=store_id, json={"token": helpers.PUSH_TOKEN})

    before = (await api.call("listIngredientCandidates", 200, storeId=store_id)).json()
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

    candidates = (await api.call("listIngredientCandidates", 200, storeId=store_id)).json()
    assert candidates["generationStatus"] == "READY"
    by_name = {item["name"]: item for item in candidates["items"]}
    assert candidates["items"][0]["name"] == "어묵"
    assert by_name["어묵"]["usedInMenuCount"] == 2
    assert [menu["name"] for menu in by_name["어묵"]["usedInMenus"]] == sorted(
        ["떡볶이", "어묵탕"],
        key=lambda name: next(menu["id"] for menu in by_name["어묵"]["usedInMenus"] if menu["name"] == name),
    )
    assert set(by_name["어묵"]["tags"]) == {"RECOMMENDED", "SHORT_SHELF_LIFE"}
    assert by_name["계란"]["tags"] == []

    core = [by_name[name]["id"] for name in ("계란", "어묵", "떡")]
    await api.call("setCoreIngredients", 204, storeId=store_id, json={"ingredientIds": core})

    recipes = (await api.call("listRecipes", 200, storeId=store_id)).json()["items"]
    gimbap = next(item for item in recipes if item["menu"]["name"] == "김밥")
    assert gimbap["unit"] == "줄"
    assert [(line["ingredient"]["name"], line["quantity"]) for line in gimbap["items"]] == [
        ("계란", {"value": 1, "unit": "개"})
    ]
    await api.call(
        "saveRecipes",
        204,
        storeId=store_id,
        json={
            "items": [
                {"menuId": gimbap["menu"]["id"], "items": [{"ingredientId": by_name["계란"]["id"], "quantity": 2}]}
            ]
        },
    )
    saved = (await api.call("listRecipes", 200, storeId=store_id)).json()["items"]
    assert next(item for item in saved if item["menu"]["name"] == "김밥")["items"][0]["quantity"]["value"] == 2
    kept = await helpers.fetch_all(
        deps, "SELECT count(*) AS n FROM recipe_item WHERE menu_id = :menu_id", menu_id=gimbap["menu"]["id"]
    )
    assert kept[0].n == 3

    await helpers.enqueue_context(deps, store_id, date(2026, 11, 11), "NIGHT")
    await runner.run_until_idle()
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert home["view"] == "TOMORROW"
    assert home["targetDate"] == "2026-11-11"
    assert home["storeName"] == "행복분식"
    assert [item["menu"]["name"] for item in home["forecast"]["items"]] == ["김밥", "떡볶이", "어묵탕"]
    assert home["forecast"]["totalMenuCount"] == 3
    first = home["forecast"]["items"][0]
    assert (first["recommended"], first["planned"], first["unit"]) == (50, 50, "줄")
    assert first["comparison"] == {"direction": "SIMILAR", "diff": 0}
    assert first["summaryText"] == "지난 4주 수요일 평균 50줄"
    assert first["previousRecommended"] is None and first["produced"] is None
    assert home["banners"] == [
        {"type": "FIRST_FORECAST", "title": "첫 예측이 준비됐어요", "message": "6주치 판매 기록으로 계산했어요"}
    ]
    assert home["order"] is None
    assert home["cautions"] == []
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
