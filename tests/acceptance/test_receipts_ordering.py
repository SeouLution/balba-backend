"""A02 영수증과 발주, A03 당일 아침과 할인, A05 화면 간 일치."""

from tests import helpers
from tests.acceptance.test_onboarding import onboard
from tests.conftest import kst
from tests.fakes import receipt_image

EGG = {"name": "계란 특란 30구", "unit": "판", "content": 30, "usageUnit": "개", "match": "계란", "kind": "exact"}
FISHCAKE = {"name": "부산어묵 1kg", "unit": "봉", "unitPrice": 30000, "match": "어묵", "kind": "similar"}
RICE_CAKE = {"name": "떡볶이떡 1kg", "unit": "kg", "unitPrice": 5000, "match": "떡", "kind": "exact"}

EGG_1108 = receipt_image(
    "대한식자재",
    "2026-11-08",
    [{**EGG, "quantity": 6, "unitPrice": 8900}, {**FISHCAKE, "quantity": 3}],
    phone="02-123-4567",
)
EGG_1027 = receipt_image(
    "대한식자재", "2026-10-27", [{**EGG, "quantity": 1, "unitPrice": 7900}, {"name": "얼룩", "quantity": None}]
)
EGG_1103 = receipt_image("대한식자재", "2026-11-03", [{**EGG, "quantity": 1, "unitPrice": 7900}])
FISHCAKE_1109 = receipt_image("대한 식자재", "2026-11-09", [{**FISHCAKE, "quantity": 10}])
RICE_CAKE_1109 = receipt_image("망원떡집", "2026-11-09", [{**RICE_CAKE, "quantity": 2}])
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
            "quantity": {"value": 6, "unit": "판"},
        },
        {"ingredient": {"id": None, "name": "부산어묵 1kg", "isNew": True}, "quantity": {"value": 3, "unit": "봉"}},
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
    assert "unitPrice" not in str(review) and "02-123" not in str(review)

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
    assert [group["supplier"]["name"] for group in plan["suppliers"]] == ["대한식자재"]
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
        "계란": ({"value": 1, "unit": "판"}, 8900, {"value": 1, "unit": "판"}, 0),
        "떡": ({"value": 4, "unit": "kg"}, 5000, {"value": 0, "unit": "kg"}, 0),
    }
    assert (plan["usualTotal"], plan["events"]) == (28900, [])
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert home["order"] == {"supplierCount": 2, "estimatedTotal": 28900}
    assert [alert["title"] for alert in home["cautions"]] == ["계란 단가가 올랐어요"]
    assert home["cautions"][0]["message"] == "계란 단가가 7,900원 → 8,900원으로 올랐어요 (+13%)"

    gimbap = ids["menus"]["김밥"]
    adjusted = await api.call(
        "adjustForecast", 200, storeId=store_id, date="2026-11-11", menuId=gimbap, json={"quantity": 80}
    )
    assert adjusted.json()["item"]["planned"] == 80 and adjusted.json()["item"]["recommended"] == 50
    assert adjusted.json()["order"] == {"supplierCount": 2, "estimatedTotal": 37800}
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert home["order"] == adjusted.json()["order"]
    assert home["forecast"]["items"][0]["planned"] == 80
    plan = (await api.call("getOrderPlan", 200, storeId=store_id, targetDate="2026-11-11")).json()
    assert plan_lines(plan)["계란"][0] == {"value": 2, "unit": "판"}
    assert plan["usualTotal"] == 28900

    stock_before = plan_lines(plan)
    order_body = {
        "targetDate": "2026-11-11",
        "suppliers": [
            {
                "supplierId": by_name["대한식자재"]["id"],
                "lines": [{"ingredientId": ids["ingredients"]["계란"], "quantity": 2}],
            },
            {
                "supplierId": by_name["망원떡집"]["id"],
                "lines": [{"ingredientId": ids["ingredients"]["떡"], "quantity": 4.5}],
            },
        ],
    }
    created = (await api.call("createPurchaseOrders", 201, storeId=store_id, json=order_body)).json()["items"]
    orders = {order["supplier"]["name"]: order for order in created}
    assert orders["대한식자재"]["messageText"] == (
        "[행복분식] 11/11(수) 발주\n계란 특란 30구 × 2판\n내일 오전 10시 전 도착 부탁드립니다."
    )
    assert orders["망원떡집"]["messageText"].splitlines()[1] == "떡볶이떡 1kg × 4.5kg"
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

    await helpers.execute(deps, "UPDATE receipt_line SET unit_price = 9900 WHERE unit_price = 8900")
    history = (await api.call("listPurchaseOrders", 200, storeId=store_id)).json()["items"]
    assert {item["supplier"]["name"]: item["totalAmount"] for item in history} == {
        "대한식자재": 17800,
        "망원떡집": 22500,
    }
    assert await helpers.count(deps, "receipt_line") == 6
    await helpers.execute(deps, "UPDATE receipt_line SET unit_price = 8900 WHERE unit_price = 9900")
    after = (await api.call("getOrderPlan", 200, storeId=store_id, targetDate="2026-11-11")).json()
    assert plan_lines(after) == stock_before


async def test_a02_same_photo_in_two_scans_is_applied_once(api, deps, runner):
    ids = await with_receipts(api, deps, runner)
    store_id = ids["storeId"]
    other_item = receipt_image(
        "대한식자재",
        "2026-11-08",
        [{"name": "단무지 3kg", "quantity": 1, "unit": "봉", "match": "단무지", "kind": "exact"}],
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
            {**FISHCAKE, "quantity": 2},
            {"name": "계란 대란", "quantity": 1, "unit": "박스", "match": "계란", "kind": "exact"},
            {"name": "참기름 1.8L", "quantity": 1, "unit": "병", "unitPrice": 25000},
        ],
    )
    scan_id = await helpers.scan_receipts(api, runner, store_id, [different])
    review = (await api.call("getReceiptScan", 200, storeId=store_id, scanId=scan_id)).json()
    answers = {"answers": [{"questionId": review["questions"][0]["id"], "answer": "DIFFERENT"}]}
    await api.call("confirmReceiptScan", 204, storeId=store_id, scanId=scan_id, json=answers)
    confirmed = (await api.call("getReceiptScan", 200, storeId=store_id, scanId=scan_id)).json()
    names = [line["ingredient"] for line in confirmed["groups"][0]["receipts"][0]["lines"]]
    assert [(item["name"], item["isNew"]) for item in names] == [("부산어묵 1kg", True), ("참기름 1.8L", True)]
    assert confirmed["removedDuplicateTexts"] == ["11/9 새벽상회 영수증에서 1줄은 읽지 못해서 뺐어요"]
    created = await helpers.fetch_all(
        deps, "SELECT name, status, purchase_unit FROM ingredient WHERE name LIKE '참기름%'"
    )
    assert [tuple(row) for row in created] == [("참기름 1.8L", "LATER", "병")]
    egg = await helpers.fetch_all(deps, "SELECT purchase_unit, content_quantity FROM ingredient WHERE name = '계란'")
    assert tuple(egg[0]) == ("판", 30)

    listing = (await api.call("listIngredients", 200, storeId=store_id)).json()
    by_name = {item["name"]: item for item in listing["items"]}
    assert listing["counts"] == {"core": 3, "later": 4}
    assert by_name["참기름 1.8L"]["price"] == {"unitPrice": 25000}
    assert by_name["참기름 1.8L"]["purchaseUnit"] == {"label": "병", "contentQuantity": None}
    assert "usageUnit" not in by_name["참기름 1.8L"]
    assert by_name["부산어묵 1kg"]["price"] == {"unitPrice": 30000}
    assert by_name["계란"]["price"] is None and by_name["계란"]["supplier"] is None
    assert by_name["계란"]["purchaseUnit"] == {"label": "판", "contentQuantity": 30}
    assert by_name["단무지"]["purchaseUnit"] is None
    assert [item["status"] for item in listing["items"]][:3] == ["CORE"] * 3
    only_later = (await api.call("listIngredients", 200, storeId=store_id, params={"status": "LATER"})).json()
    assert only_later["counts"] == listing["counts"] and len(only_later["items"]) == 4
    await api.call("listIngredients", 422, storeId=store_id, params={"status": "ALL"})

    empty_scan = await helpers.scan_receipts(api, runner, store_id, [UNREADABLE])
    empty = (await api.call("getReceiptScan", 200, storeId=store_id, scanId=empty_scan)).json()
    assert (empty["status"], empty["groups"], empty["questions"]) == ("NEEDS_REVIEW", [], [])
    assert empty["removedDuplicateTexts"] == ["1번째 사진은 읽지 못해서 뺐어요"]
    lines_before = await helpers.count(deps, "receipt_line")
    await api.call("confirmReceiptScan", 204, storeId=store_id, scanId=empty_scan)
    assert await helpers.count(deps, "receipt_line") == lines_before


async def test_a03_morning_and_discount(api, deps, runner):
    ids = await with_receipts(api, deps, runner)
    store_id = ids["storeId"]
    order_body = {
        "targetDate": "2026-11-11",
        "suppliers": [
            {
                "supplierId": ids["suppliers"]["대한식자재"],
                "lines": [{"ingredientId": ids["ingredients"]["계란"], "quantity": 1}],
            }
        ],
    }
    [order] = (await api.call("createPurchaseOrders", 201, storeId=store_id, json=order_body)).json()["items"]
    evening = (await api.call("getDiscountRecommendations", 200, storeId=store_id)).json()
    assert evening["recommendations"] == []
    assert [
        (item["ingredient"]["name"], item["quantity"], item["wasteCost"], item["mitigation"])
        for item in evening["surplusStock"]
    ] == [("어묵", {"value": 5500, "unit": "g"}, None, "ORDER_REDUCED")]

    deps.clock.fixed = kst("2026-11-11T07:40:00")
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert (home["view"], home["targetDate"]) == ("TODAY", "2026-11-11")
    assert "order" not in home
    assert [banner["type"] for banner in home["banners"]] == []
    await api.call("recordSent", 204, storeId=store_id, purchaseOrderId=order["id"], json={"channel": "KAKAOTALK"})
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert home["banners"] == [
        {"type": "DELIVERY_EXPECTED", "title": "어젯밤 발주한 재료가 오전에 들어와요", "message": "계란 1판"}
    ]

    gimbap = ids["menus"]["김밥"]
    sales_before = await helpers.count(deps, "daily_sales")
    await api.call("setProduction", 204, storeId=store_id, date="2026-11-11", menuId=gimbap, json={"quantity": 50})
    await api.call("setProduction", 204, storeId=store_id, date="2026-11-11", menuId=gimbap, json={"quantity": 50})
    await api.call("setProduction", 422, storeId=store_id, date="2026-11-10", menuId=gimbap, json={"quantity": 1})
    await api.call("setProduction", 404, storeId=store_id, date="2026-11-20", menuId=gimbap, json={"quantity": 1})
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert home["forecast"]["items"][0]["produced"] == 50
    assert await helpers.count(deps, "daily_sales") == sales_before

    expiry = {
        "id": f"expiry:{ids['ingredients']['어묵']}:2026-11-11",
        "type": "EXPIRY_IMMINENT",
        "title": "어묵 오늘까지",
    }
    expiry_plain = {**expiry, "message": "입고일 기준으로 계산한 예상 기한이에요", "actions": []}
    assert home["cautions"][0] == expiry_plain
    assert [alert["type"] for alert in home["cautions"]] == ["EXPIRY_IMMINENT", "PRICE_CHANGED"]

    deps.clock.fixed = kst("2026-11-11T16:59:00")
    early = (await api.call("getDiscountRecommendations", 200, storeId=store_id)).json()
    assert early == {"date": "2026-11-11", "baseTime": "17:00", "recommendations": [], "surplusStock": []}
    assert (await api.call("getHome", 200, storeId=store_id)).json()["cautions"][0] == expiry_plain

    deps.clock.fixed = kst("2026-11-11T17:00:00")
    counts_before = await helpers.row_counts(deps)
    result = (await api.call("getDiscountRecommendations", 200, storeId=store_id)).json()
    assert await helpers.row_counts(deps) == counts_before
    tang = ids["menus"]["어묵탕"]
    assert result["recommendations"] == [
        {
            "id": f"rec_2026-11-11_{tang}",
            "menu": {"id": tang, "name": "어묵탕"},
            "unit": "인분",
            "discount": {"type": "PERCENT", "value": 30},
            "startTime": "19:00",
            "quantity": 20,
            "reasonText": "어묵이 많이 들어가요",
            "expectedSavings": 54000,
        }
    ]
    assert result["surplusStock"] == [
        {
            "ingredient": {"id": ids["ingredients"]["어묵"], "name": "어묵"},
            "quantity": {"value": 3000, "unit": "g"},
            "wasteCost": 90000,
            "mitigation": "DISCOUNT_RECOMMENDED",
        }
    ]
    assert (await api.call("getDiscountRecommendations", 200, storeId=store_id)).json() == result
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert home["cautions"][0] == {
        **expiry,
        "title": "어묵 오늘까지 · 할인 추천이 있어요",
        "message": "입고일 기준으로 계산한 예상 기한이에요",
        "actions": [{"type": "OPEN_DISCOUNTS"}],
    }
    notices = (await api.call("listIngredients", 200, storeId=store_id)).json()["notices"]
    assert notices == home["cautions"]

    [card] = result["recommendations"]
    body = {
        "recommendationId": card["id"],
        "menuId": tang,
        "discount": card["discount"],
        "startTime": card["startTime"],
        "quantity": card["quantity"],
    }
    preview = (await api.call("createDiscount", 200, storeId=store_id, params={"dryRun": "true"}, json=body)).json()
    assert preview["id"].startswith("preview_")
    assert preview["wasteCostAvoided"] - preview["revenueReduction"] == card["expectedSavings"]
    assert (preview["originalPrice"], preview["discountedPrice"]) == (6000, 4200)
    assert preview["promoText"] == "갓 만든 어묵탕, 마감 전에 할인해요"
    assert [channel["app"] for channel in preview["channels"]] == ["BAEMIN", "COUPANG_EATS", "STORE_NOTICE"]
    assert preview["channels"][0] == {"app": "BAEMIN", "launchUrl": None, "supportsPaste": False}
    assert await helpers.count(deps, "discount") == 0
    assert (await api.call("listDiscounts", 200, storeId=store_id)).json() == {"items": []}

    created = (await api.call("createDiscount", 201, storeId=store_id, json=body)).json()
    assert {key: value for key, value in created.items() if key != "id"} == {
        key: value for key, value in preview.items() if key != "id"
    }
    history = (await api.call("listDiscounts", 200, storeId=store_id)).json()["items"]
    assert history == [created]
    assert (await api.call("getDiscountRecommendations", 200, storeId=store_id)).json() == result

    tteok = ids["menus"]["떡볶이"]
    manual = {"menuId": tteok, "discount": {"type": "AMOUNT", "value": 500}, "startTime": "20:00", "quantity": 5}
    manual_preview = (
        await api.call("createDiscount", 200, storeId=store_id, params={"dryRun": True}, json=manual)
    ).json()
    assert (manual_preview["discountedPrice"], manual_preview["revenueReduction"]) == (4500, 2500)
    assert manual_preview["wasteCostAvoided"] == 7500
    await helpers.execute(deps, "UPDATE receipt_line SET unit_price = NULL WHERE product_name = '부산어묵 1kg'")
    unknown_cost = (
        await api.call("createDiscount", 200, storeId=store_id, params={"dryRun": True}, json=manual)
    ).json()
    assert "wasteCostAvoided" not in unknown_cost and unknown_cost["revenueReduction"] == 2500
    assert (await api.call("getDiscountRecommendations", 200, storeId=store_id)).json()["recommendations"] == []

    for invalid in (
        {**manual, "discount": {"type": "PERCENT", "value": 0}},
        {**manual, "discount": {"type": "PERCENT", "value": 101}},
        {**manual, "discount": {"type": "AMOUNT", "value": 10.5}},
        {**manual, "discount": {"type": "AMOUNT", "value": 5001}},
        {**manual, "quantity": 0},
        {**manual, "startTime": "09:59"},
        {**manual, "startTime": "21:00"},
        {**manual, "startTime": "24:00"},
        {**manual, "recommendationId": f"rec_2026-11-10_{tteok}"},
    ):
        await api.call("createDiscount", 422, storeId=store_id, json=invalid)
    await api.call("createDiscount", 404, storeId=store_id, json={**manual, "menuId": "mn_missing"})
    assert await helpers.count(deps, "discount") == 1

    deps.clock.fixed = kst("2026-11-12T09:00:00")
    await helpers.execute(deps, "UPDATE menu SET price = 9000 WHERE id = :id", id=tang)
    assert (await api.call("listDiscounts", 200, storeId=store_id)).json() == {"items": []}
    week = (await api.call("listDiscounts", 200, storeId=store_id, params={"range": "THIS_WEEK"})).json()["items"]
    everything = (await api.call("listDiscounts", 200, storeId=store_id, params={"range": "ALL"})).json()["items"]
    assert week == everything == [created]


async def test_first_receipt_replaces_the_estimated_package_size(api, deps, runner):
    ids = await onboard(api, deps, runner)
    store_id = ids["storeId"]

    async def units(name):
        rows = await helpers.fetch_all(
            deps, "SELECT usage_unit, purchase_unit, content_quantity FROM ingredient WHERE name = :name", name=name
        )
        return tuple(rows[0])

    assert await units("떡") == ("g", "kg", 1000)
    assert await units("어묵") == ("g", "봉", 1000)
    assert await units("김") == ("장", "속", 100)
    first = receipt_image(
        "망원떡집",
        "2026-11-07",
        [
            {
                "name": "떡볶이떡 2kg",
                "quantity": 3,
                "unit": "봉",
                "content": 2,
                "usageUnit": "kg",
                "match": "떡",
                "kind": "exact",
            },
            {
                "name": "부산어묵 40장",
                "quantity": 1,
                "unit": "봉",
                "content": 40,
                "usageUnit": "장",
                "match": "어묵",
                "kind": "exact",
            },
            {"name": "김밥김", "quantity": 2, "unit": "속", "match": "김", "kind": "exact"},
        ],
    )
    scan_id = await helpers.scan_receipts(api, runner, store_id, [first])
    await api.call("confirmReceiptScan", 204, storeId=store_id, scanId=scan_id)
    assert await units("떡") == ("g", "봉", 2000)
    assert await units("어묵") == ("g", "봉", 1000)
    assert await units("김") == ("장", "속", 100)
    assert await helpers.count(deps, "receipt_line") == 3

    later = receipt_image(
        "망원떡집",
        "2026-11-09",
        [
            {
                "name": "떡볶이떡 1kg",
                "quantity": 1,
                "unit": "봉",
                "content": 1,
                "usageUnit": "kg",
                "match": "떡",
                "kind": "exact",
            }
        ],
    )
    scan_id = await helpers.scan_receipts(api, runner, store_id, [later])
    await api.call("confirmReceiptScan", 204, storeId=store_id, scanId=scan_id)
    assert await units("떡") == ("g", "봉", 2000)
    assert await helpers.count(deps, "receipt_line") == 4
