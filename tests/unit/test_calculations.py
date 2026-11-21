"""validation.md의 계산 기준 fixture F01~F03과 문장 템플릿. DB·네트워크 없이 계산만 확인한다."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from app.infrastructure.providers.ports import DayWeather, HourlyWeather
from app.modules import catalog, discounts, forecasting, inventory, ordering, receipts, sales, stores, suppliers
from app.shared import texts
from app.shared.policy import load_policy
from app.shared.types import KST, DayHours, to_qty

POLICY = load_policy()
WED = date(2026, 11, 11)
OPEN_HOURS = tuple(
    DayHours(day, False, time(10), time(21)) for day in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
)


def make_store(category="BUNSIK", hours=OPEN_HOURS) -> stores.StoreSnapshot:
    return stores.StoreSnapshot(
        "st", "행복분식", category, "주소", hours, 37.55, 126.91, "READY", None, datetime.now(KST)
    )


def menu(menu_id="m1", name="김밥", unit="줄", price=4000) -> catalog.MenuSnapshot:
    return catalog.MenuSnapshot(menu_id, "st", name, unit, price)


def ingredient(ingredient_id="egg", name="계란", content=30, shelf=21, status="CORE", unit="개", purchase="판"):
    return catalog.IngredientSnapshot(
        ingredient_id, "st", name, status, unit, purchase, Decimal(content) if content else None, shelf, ()
    )


def weekly_sales(menu_id: str, target: date, quantities: list[int | None]) -> sales.SalesSnapshot:
    """quantities[n]은 n+1주 전 같은 요일 판매량. None이면 그 주는 판매 기록이 없다."""
    rows, ranges = [], []
    for weeks, quantity in enumerate(quantities, start=1):
        if quantity is not None:
            day = target - timedelta(weeks=weeks)
            rows.append((menu_id, day, Decimal(quantity)))
            ranges.append((day, day))
    return sales.SalesSnapshot(tuple(rows), tuple(ranges))


def event(start_hour=12, end_hour=18, day=WED) -> forecasting.NearbyEvent:
    start = datetime.combine(day, time(start_hour), KST)
    return forecasting.NearbyEvent(
        "ev1", "축제", None, 650, start, start.replace(hour=end_hour), None, None, None, None, None, 37.55, 126.91
    )


def weather(condition="RAIN") -> forecasting.WeatherSnapshot:
    return forecasting.WeatherSnapshot("st", WED, Decimal(7), condition, "비", None)


# ───────── F01 예측 ─────────


def test_f01_baseline_without_factors():
    [item] = forecasting.build(
        make_store(), WED, (menu(),), weekly_sales("m1", WED, [40, 60, 50, 50]), None, (), POLICY
    )
    assert (item.baseline, item.recommended, item.event_diff) == (50, 50, 0)
    assert forecasting.comparison(item, POLICY) == ("SIMILAR", 0)
    assert item.summary_text == "지난 4주 수요일 평균 50줄"


def test_f01_event_and_weather_factors():
    sold = weekly_sales("m1", WED, [40, 60, 50, 50])
    [with_event] = forecasting.build(make_store(), WED, (menu(),), sold, None, (event(),), POLICY)
    assert (with_event.recommended, with_event.event_diff) == (60, 10)
    assert forecasting.comparison(with_event, POLICY) == ("MORE", 10)
    [with_both] = forecasting.build(make_store(), WED, (menu(),), sold, weather("RAIN"), (event(),), POLICY)
    assert (with_both.recommended, with_both.event_diff) == (54, 9)
    assert with_both.summary_text == "지난 4주 수요일 평균 50줄 · 행사 +20% · 비 -10%"


def test_f01_fractional_baseline_rounds_half_up():
    saturday = date(2026, 11, 14)
    sold = weekly_sales("m1", saturday, [58, 58, 58, 59])
    [item] = forecasting.build(make_store(), saturday, (menu(),), sold, None, (event(day=saturday),), POLICY)
    assert item.baseline == Decimal("58.25")
    assert item.recommended == 70
    assert forecasting.comparison(item, POLICY) == ("MORE", 12)
    assert item.summary_text == "지난 4주 토요일 평균 58줄 · 행사 +20%"


def test_f01_weeks_without_record_leave_the_sample():
    [three] = forecasting.build(
        make_store(), WED, (menu(),), weekly_sales("m1", WED, [30, None, 60, 60]), None, (), POLICY
    )
    assert three.baseline == 50
    assert forecasting.build(make_store(), WED, (menu(),), weekly_sales("m1", WED, [None] * 4), None, (), POLICY) == []


def test_f01_day_inside_period_without_row_counts_as_zero():
    covered = sales.SalesSnapshot((("m1", WED - timedelta(weeks=1), Decimal(40)),), ((WED - timedelta(weeks=2), WED),))
    [item] = forecasting.build(make_store(), WED, (menu(),), covered, None, (), POLICY)
    assert item.baseline == 20


def test_closed_day_and_events_outside_business_hours():
    closed = tuple(DayHours(hours.day, hours.day == "WED", hours.open, hours.close) for hours in OPEN_HOURS)
    sold = weekly_sales("m1", WED, [50, 50, 50, 50])
    assert forecasting.build(make_store(hours=closed), WED, (menu(),), sold, None, (event(),), POLICY) == []
    [early] = forecasting.build(make_store(), WED, (menu(),), sold, None, (event(6, 9),), POLICY)
    assert early.event_diff == 0


def test_weather_factor_by_category_overrides_default():
    assert POLICY.weather_factor_for("GUKBAP", "RAIN") == Decimal("1.05")
    assert POLICY.weather_factor_for("BUNSIK", "RAIN") == Decimal("0.90")
    assert POLICY.weather_factor_for("BUNSIK", None) == 1


def test_haversine_uses_metre_rounding():
    assert forecasting.haversine_meters(37.5, 127.0, 37.5, 127.0) == 0
    assert forecasting.haversine_meters(37.0, 127.0, 38.0, 127.0) == 111195


def test_weather_summary_picks_longest_precipitation_and_start_hour():
    def hour(at, condition, temperature):
        return HourlyWeather(datetime.combine(WED, time(at), KST), Decimal(temperature), condition)

    window = (datetime.combine(WED, time(10), KST), datetime.combine(WED, time(21), KST))
    day = DayWeather(
        (hour(9, "SNOW", 1), hour(10, "CLOUDY", 5), hour(14, "RAIN", 7), hour(15, "RAIN", 6), hour(16, "SHOWER", 6)),
        None,
    )
    assert forecasting.summarize_weather(day, window) == (Decimal(7), "RAIN", "비 · 14시부터")
    dry = DayWeather((hour(10, "CLEAR", 3), hour(11, "CLEAR", 4), hour(12, "CLOUDY", 5)), Decimal(9))
    assert forecasting.summarize_weather(dry, window) == (Decimal(9), "CLEAR", "맑음")
    assert forecasting.summarize_weather(DayWeather((), None), window) is None


# ───────── F02 재고·발주 ─────────

EGG = ingredient()
GIMBAP = menu()
EGG_RECIPE = catalog.CatalogSnapshot("st", (GIMBAP,), (EGG,), (catalog.RecipeLine("m1", "egg", Decimal(1)),))


def lot(line_id=1, day=date(2026, 11, 8), packs=2, price=8900, ingredient_id="egg", supplier_id="s1"):
    return receipts.ReceiptLot(line_id, ingredient_id, supplier_id, day, Decimal(packs), price, "계란 특란 30구")


def recorded_sales() -> sales.SalesSnapshot:
    rows = (("m1", date(2026, 11, 8), Decimal(20)), ("m1", date(2026, 11, 9), Decimal(25)))
    return sales.SalesSnapshot(rows, ((date(2026, 11, 8), date(2026, 11, 9)),))


def stock_on(as_of: date, usage=(), sold=None, lots=None):
    return inventory.evaluate(EGG_RECIPE, sold or recorded_sales(), list(usage), lots or [lot()], OPEN_HOURS, as_of)[
        "egg"
    ]


def test_f02_stock_after_recorded_sales():
    snapshot = stock_on(date(2026, 11, 9))
    assert snapshot.stock == 15
    assert snapshot.remaining_lots[0].expires_on == date(2026, 11, 28)


def test_f02_day_without_sales_uses_produced_then_planned():
    tuesday = date(2026, 11, 10)
    planned = forecasting.UsageRow("m1", tuesday, None, Decimal(10))
    assert stock_on(tuesday, [planned]).stock == 5
    produced = forecasting.UsageRow("m1", tuesday, Decimal(12), Decimal(10))
    assert stock_on(tuesday, [produced]).stock == 3


def test_f02_day_without_forecast_uses_weekday_average_or_unknown():
    tuesday = date(2026, 11, 10)
    assert stock_on(tuesday).stock is None
    rows = (*recorded_sales().rows, ("m1", date(2026, 11, 3), Decimal(7)))
    with_history = sales.SalesSnapshot(rows, (*recorded_sales().covered_ranges, (date(2026, 11, 3), date(2026, 11, 3))))
    assert stock_on(tuesday, sold=with_history).stock == 8


def test_f02_usage_above_stock_floors_at_zero_and_expired_lot_leaves():
    heavy = sales.SalesSnapshot((("m1", date(2026, 11, 8), Decimal(100)),), ((date(2026, 11, 8), date(2026, 11, 9)),))
    assert stock_on(date(2026, 11, 9), sold=heavy).stock == 0
    short = catalog.CatalogSnapshot("st", (GIMBAP,), (ingredient(shelf=2),), EGG_RECIPE.recipe_lines)
    quiet = sales.SalesSnapshot((), ((date(2026, 11, 8), date(2026, 11, 12)),))
    assert inventory.evaluate(short, quiet, [], [lot()], OPEN_HOURS, date(2026, 11, 9))["egg"].stock == 60
    assert inventory.evaluate(short, quiet, [], [lot()], OPEN_HOURS, date(2026, 11, 10))["egg"].stock == 0


def test_stock_is_unknown_without_conversion_or_receipt():
    no_conversion = catalog.CatalogSnapshot("st", (GIMBAP,), (ingredient(content=None),), EGG_RECIPE.recipe_lines)
    assert (
        inventory.evaluate(no_conversion, recorded_sales(), [], [lot()], OPEN_HOURS, date(2026, 11, 9))["egg"].stock
        is None
    )
    assert inventory.evaluate(EGG_RECIPE, recorded_sales(), [], [], OPEN_HOURS, date(2026, 11, 9))["egg"].stock is None


def forecast_row(planned: int, baseline=60, event_diff=0, adjusted=None) -> forecasting.ForecastSnapshot:
    return forecasting.ForecastSnapshot(
        GIMBAP, WED, Decimal(baseline), Decimal(planned), adjusted, None, Decimal(event_diff), None, None
    )


def supplier(order_days=("TUE",), status="CONFIRMED") -> suppliers.SupplierSnapshot:
    return suppliers.SupplierSnapshot("s1", "st", "대한식자재", None, False, None, order_days, status)


def plan(forecasts, stock, target_supplier=None, order_day="TUE", price=8900):
    fact = receipts.LatestPurchaseFact("egg", "s1", "계란 특란 30구", price, date(2026, 11, 8), 1)
    target_supplier = target_supplier or supplier()
    return ordering.plan(
        EGG_RECIPE, forecasts, {"egg": stock}, {"egg": fact}, {"s1": target_supplier}, (), order_day, WED, POLICY
    )


def known_stock(remaining: int | None, expires_on=date(2026, 11, 28)) -> inventory.InventorySnapshot:
    if remaining is None:
        return inventory.InventorySnapshot("egg", date(2026, 11, 10), None, ())
    lots = (inventory.RemainingLot(lot(), Decimal(remaining), expires_on),) if remaining else ()
    return inventory.InventorySnapshot("egg", date(2026, 11, 10), Decimal(remaining), lots)


def test_f02_order_packs_round_up_and_event_diff():
    result = plan([forecast_row(75, baseline=60, event_diff=15)], known_stock(15))
    [line] = result.lines
    assert (line.quantity, line.unit, line.stock, line.event_diff, line.price) == (2, "판", Decimal("0.5"), 0, 8900)
    assert result.total == 17800
    assert result.usual_total == 17800


def test_f02_unknown_stock_orders_everything_and_keeps_null():
    [line] = plan([forecast_row(75)], known_stock(None)).lines
    assert (line.quantity, line.stock) == (3, None)
    [zero] = plan([forecast_row(75)], known_stock(0)).lines
    assert (zero.quantity, zero.stock) == (3, 0)


def test_adjusted_menu_has_no_event_increment():
    [line] = plan([forecast_row(60, event_diff=10, adjusted=Decimal(90))], known_stock(0)).lines
    assert (line.quantity, line.event_diff) == (3, 0)
    [increased] = plan([forecast_row(70, event_diff=40)], known_stock(0)).lines
    assert (increased.quantity, increased.event_diff) == (3, 2)


def test_supplier_must_order_today_with_known_days():
    assert plan([forecast_row(75)], known_stock(0), order_day="WED").lines == ()
    assert plan([forecast_row(75)], known_stock(0), supplier(status="UNKNOWN")).lines == ()
    assert plan([forecast_row(75)], known_stock(0), supplier(order_days=(), status="CONFIRMED")).lines == ()
    assert len(plan([forecast_row(75)], known_stock(0), supplier(status="ESTIMATED")).lines) == 1


def test_money_total_adds_only_known_prices():
    assert ordering.money_total([(Decimal(2), 8900), (Decimal(1), None), (Decimal(3), 1000)]) == 20800
    assert ordering.money_total([(Decimal(2), None)]) is None
    assert ordering.money_total([]) is None
    assert plan([forecast_row(75)], known_stock(15), price=None).total is None


@pytest.mark.parametrize(("expiring", "reduced"), [(1, False), (2, True)])
def test_a05_order_reduction_needs_fewer_packs(expiring, reduced):
    result = plan([forecast_row(32)], known_stock(expiring, expires_on=WED))
    assert (result.reduced_ingredient_ids == ("egg",)) is reduced
    assert plan([forecast_row(32)], known_stock(expiring, expires_on=WED), order_day="WED").reduced_ingredient_ids == ()


def test_reduction_counts_a_line_that_disappears():
    result = plan([forecast_row(30)], known_stock(30, expires_on=WED))
    assert result.lines == () and result.reduced_ingredient_ids == ("egg",)


# ───────── F03 할인 ─────────

TODAY = date(2026, 11, 11)
TTEOK = menu("m_tteok", "떡볶이", "인분", 5000)
TANG = menu("m_tang", "어묵탕", "인분", 6000)
FISHCAKE = ingredient("fish", "어묵", content=1000, shelf=3, unit="g", purchase="봉")


def stock_today(expiring=Decimal(1000), stock=None, price=9000, menus=(TTEOK, TANG), uses=None) -> discounts.StockToday:
    lines = uses or (
        catalog.RecipeLine("m_tteok", "fish", Decimal(100)),
        catalog.RecipeLine("m_tang", "fish", Decimal(200)),
    )
    fact = receipts.LatestPurchaseFact("fish", "s1", "부산어묵", price, TODAY, 1)
    return discounts.StockToday(
        catalog.CatalogSnapshot("st", menus, (FISHCAKE,), lines),
        {"fish": fact},
        {"fish": expiring if stock is None else stock},
        {"fish": expiring} if expiring else {},
    )


def test_f03_manual_discount_amounts():
    payload = discounts.DiscountInput("m_tteok", "PERCENT", Decimal(10), time(19), Decimal(5))
    result = discounts.evaluate(payload, TTEOK, stock_today(), TODAY)
    assert (result.discounted_price, result.revenue_reduction) == (4500, 2500)
    assert result.waste_cost_avoided == 4500
    assert result.promo_text == "갓 만든 떡볶이, 마감 전에 할인해요"
    amount = discounts.evaluate(
        discounts.DiscountInput("m_tteok", "AMOUNT", Decimal(700), time(19), Decimal(3)), TTEOK, stock_today(), TODAY
    )
    assert (amount.discounted_price, amount.revenue_reduction) == (4300, 2100)


def test_f03_unknown_price_omits_waste_cost_and_drops_recommendation():
    payload = discounts.DiscountInput("m_tteok", "PERCENT", Decimal(10), time(19), Decimal(5))
    assert discounts.evaluate(payload, TTEOK, stock_today(price=None), TODAY).waste_cost_avoided is None
    assert discounts.recommend(stock_today(price=None), time(19), TODAY, POLICY) == ([], set())


def test_f03_two_menus_share_expiring_stock_once():
    chosen, discounted = discounts.recommend(stock_today(expiring=Decimal(1000), price=90000), time(19), TODAY, POLICY)
    assert [(item.evaluation.menu.id, item.evaluation.quantity) for item in chosen] == [("m_tang", 5)]
    assert chosen[0].id == "rec_2026-11-11_m_tang"
    assert chosen[0].reason_text == "어묵이 많이 들어가요"
    assert chosen[0].expected_savings == 90000 - 9000
    assert discounted == {"fish"}

    base = stock_today(price=90000)
    limited = discounts.StockToday(
        catalog.CatalogSnapshot(
            "st",
            (TTEOK, TANG),
            (
                FISHCAKE,
                ingredient("rice", "떡", content=1000, shelf=None, unit="g", purchase="kg"),
                ingredient("radish", "무", content=1000, shelf=None, unit="g", purchase="kg"),
            ),
            (
                catalog.RecipeLine("m_tteok", "fish", Decimal(100)),
                catalog.RecipeLine("m_tteok", "rice", Decimal(200)),
                catalog.RecipeLine("m_tang", "fish", Decimal(200)),
                catalog.RecipeLine("m_tang", "radish", Decimal(200)),
            ),
        ),
        base.latest_purchase,
        {"fish": Decimal(1000), "rice": Decimal(600), "radish": Decimal(600)},
        {"fish": Decimal(1000)},
    )
    both, _ = discounts.recommend(limited, time(19), TODAY, POLICY)
    assert [(item.evaluation.menu.id, item.evaluation.quantity) for item in both] == [("m_tang", 3), ("m_tteok", 3)]
    assert [item.evaluation.waste_cost_avoided for item in both] == [54000, 27000]
    assert sum(item.evaluation.waste_cost_avoided for item in both) <= discounts.waste_cost(
        {"fish": Decimal(1000)}, limited
    )
    assert limited.expiring_today == {"fish": Decimal(1000)}


def test_f03_zero_usage_or_non_positive_savings_is_not_recommended():
    zero_use = stock_today(uses=(catalog.RecipeLine("m_tteok", "fish", Decimal(0)),), menus=(TTEOK,))
    assert discounts.recommend(zero_use, time(19), TODAY, POLICY)[0] == []
    cheap = stock_today(price=10)
    assert discounts.recommend(cheap, time(19), TODAY, POLICY)[0] == []


def test_f03_recommended_start_time():
    def window(open_hour, close_hour, next_day=False):
        start = datetime.combine(TODAY, time(open_hour), KST)
        end = datetime.combine(TODAY + timedelta(days=1 if next_day else 0), time(close_hour), KST)
        return start, end

    assert discounts.recommended_start(window(10, 21), TODAY, POLICY) == time(19)
    assert discounts.recommended_start(window(10, 16), TODAY, POLICY) is None
    assert discounts.recommended_start(window(18, 2, next_day=True), TODAY, POLICY) == time(0)
    assert discounts.recommended_start(window(10, 18), TODAY, POLICY) == time(17)


def test_discount_value_ranges():
    for discount_type, value in (
        ("PERCENT", Decimal(0)),
        ("PERCENT", Decimal("100.1")),
        ("AMOUNT", Decimal("10.5")),
        ("AMOUNT", Decimal(5001)),
    ):
        with pytest.raises(Exception):
            discounts.validate_discount_value(discount_type, value, 5000)
    discounts.validate_discount_value("PERCENT", Decimal(100), 5000)
    discounts.validate_discount_value("AMOUNT", Decimal(5000), 5000)
    assert discounts.discounted_price(4999, "PERCENT", Decimal(10)) == 4499
    assert discounts.discounted_price(4995, "PERCENT", Decimal(10)) == 4496


# ───────── 문장·정책·수량 ─────────


def test_templates():
    assert texts.josa("김밥", "을", "를") == "김밥을"
    assert texts.josa("떡볶이", "을", "를") == "떡볶이를"
    assert texts.overnight_change_title("김밥", Decimal(-2), "줄") == "밤사이 예보가 바뀌어 김밥을 2줄 줄였어요"
    assert texts.overnight_change_title(None, Decimal(0), "") == "밤사이 날씨 예보가 바뀌었어요"
    assert texts.price_changed_alert("계란", 7900, 8900) == (
        "계란 단가가 올랐어요",
        "계란 단가가 7,900원 → 8,900원으로 올랐어요 (+13%)",
    )
    assert texts.expiry_alert("어묵", True)[0] == "어묵 오늘까지 · 할인 추천이 있어요"
    assert texts.expiry_alert("어묵", False)[0] == "어묵 오늘까지"
    assert texts.delivery_expected_banner(
        [("계란", Decimal(3), "판"), ("김", Decimal(2), "속"), ("떡", Decimal(5), "kg"), ("무", Decimal(1), "개")]
    ) == (
        "어젯밤 발주한 재료가 오전에 들어와요",
        "계란 3판 · 김 2속 · 떡 5kg 외 1개",
    )
    assert texts.weather_change_text("비 · 14시부터", "비 · 13시부터") == "비 · 14시부터 → 비 · 13시부터"
    assert texts.weather_change_text("비", "비") is None and texts.weather_change_text(None, "비") is None
    assert texts.push_message("NIGHT", "김밥", Decimal(60), "줄", 12) == (
        "내일 준비량이 나왔어요",
        "김밥 60줄 등 12개 메뉴",
    )
    assert texts.push_message("MORNING", "김밥", Decimal(48), "줄", 12) == (
        "오늘 미리 만들어둘 양",
        "김밥 48줄 등 12개 메뉴",
    )
    assert (
        texts.duplicate_receipt_notice(date(2026, 11, 8), "대한식자재")
        == "11/8 대한식자재 영수증이 두 번 찍혀서 1장은 뺐어요"
    )
    message = texts.purchase_order_message(
        "행복분식",
        WED,
        date(2026, 11, 10),
        10,
        [("계란 특란 30구", Decimal(2), "판"), ("김밥김 100매", Decimal(1), "속")],
    )
    assert (
        message
        == "[행복분식] 11/11(수) 발주\n계란 특란 30구 × 2판\n김밥김 100매 × 1속\n내일 오전 10시 전 도착 부탁드립니다."
    )
    assert texts.purchase_order_message("행복분식", WED, WED, 10, []).endswith("11/11 오전 10시 전 도착 부탁드립니다.")
    title, body = texts.event_today_banner(
        datetime.combine(WED, time(12), KST), datetime.combine(WED, time(18), KST), 13, 16, "김밥"
    )
    assert title == "오늘 근처 행사 · 12:00 ~ 18:00"
    assert body == "13~16시가 가장 붐벼요 · 12시 전에 김밥 준비를 끝내두세요"


def test_policy_lookups():
    assert POLICY.menu_unit_for("참치김밥") == "줄"
    assert POLICY.menu_unit_for("아메리카노") == "개"
    assert POLICY.shelf_life_for("계란") == 21
    assert POLICY.shelf_life_for("국산 돼지고기 앞다리") == 4
    assert POLICY.shelf_life_for("참기름") is None
    assert POLICY.tags_for("어묵", 3, 1) == ["RECOMMENDED", "SHORT_SHELF_LIFE"]
    assert POLICY.tags_for("치즈", 30, 1) == ["RECOMMENDED", "EXPENSIVE"]
    assert POLICY.tags_for("단무지", 30, 3) == ["RECOMMENDED"]
    assert POLICY.tags_for("단무지", 30, 2) == []
    used = [
        name
        for name in (
            "축제-문화/예술",
            "축제-기타",
            "콘서트",
            "클래식",
            "연극",
            "뮤지컬/오페라",
            "무용",
            "국악",
            "독주/독창회",
        )
    ]
    assert all(POLICY.uses_event_category(name) for name in used)
    assert not any(POLICY.uses_event_category(name) for name in ("교육/체험", "전시/미술", "영화", "기타", None))


def test_quantity_rounds_to_six_places_and_rejects_non_numbers():
    assert to_qty(1.23456789) == Decimal("1.234568")
    assert to_qty(0) == 0
    for bad in (True, "3", -1, float("nan"), float("inf"), None):
        with pytest.raises(ValueError):
            to_qty(bad)


def test_sales_rows_skip_totals_and_net_refunds():
    rows = [
        ["주문일자", "상품명", "수량", "결제금액"],
        [datetime(2026, 11, 1, 12, 30), " 김밥 ", 2, 8000],
        ["2026/11/01 13:00", "김밥", "-1", "-4,000"],
        ["2026.11.02", "김밥", "3", "12,000"],
        ["합계", "", 4, 16000],
        [None, None, None, None],
    ]
    mapping = {"date": "주문일자", "menuName": "상품명", "quantity": "수량", "amount": "결제금액"}
    records = sales.parse_sales_rows(rows, mapping)
    assert [(record.sold_on, record.menu_name, record.quantity) for record in records] == [
        (date(2026, 11, 1), "김밥", 2),
        (date(2026, 11, 1), "김밥", -1),
        (date(2026, 11, 2), "김밥", 3),
    ]
    assert sales.latest_prices(records) == {"김밥": 4000}
