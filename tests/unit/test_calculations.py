"""validation.md의 계산 기준 fixture F01~F02와 문장 템플릿. DB·네트워크 없이 계산만 확인한다."""

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from app.infrastructure.providers.ports import DayWeather, HourlyWeather, WeatherObservation
from app.modules import catalog, forecasting, inventory, ordering, receipts, sales, stores, suppliers
from app.shared import texts
from app.shared.policy import load_policy
from app.shared.types import KST, DayHours, to_qty

POLICY = load_policy()
WED = date(2026, 11, 11)
SAT = date(2026, 11, 14)
DAY_CODES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
OPEN_HOURS = tuple(DayHours(day, False, time(10), time(21)) for day in DAY_CODES)
SUNDAY_CLOSED = tuple(DayHours(day, day == "SUN", time(10), time(21)) for day in DAY_CODES)


def make_store(hours=OPEN_HOURS, model=None) -> stores.StoreSnapshot:
    return stores.StoreSnapshot(
        "st", "행복분식", "BUNSIK", "주소", hours, 37.55, 126.91, "READY", None, datetime.now(KST), model
    )


def menu(menu_id="m1", name="김밥") -> catalog.MenuSnapshot:
    return catalog.MenuSnapshot(menu_id, "st", name)


def daily_sales(start: date, days: int, quantity_on, menu_id="m1") -> sales.SalesSnapshot:
    dates = [start + timedelta(days=offset) for offset in range(days)]
    rows = tuple((menu_id, day, Decimal(quantity_on(day))) for day in dates)
    return sales.SalesSnapshot(rows, ((dates[0], dates[-1]),))


def model_json(weekday=1, weather=1, saturday=1.2, weather_index=None) -> dict:
    model = forecasting.ForecastModel(
        Decimal(str(weekday)),
        Decimal(str(weather)),
        {"SAT": Decimal(str(saturday))},
        weather_index or {},
    )
    return model.to_json()


def event(start_hour=12, end_hour=18, day=SAT) -> forecasting.NearbyEvent:
    start = datetime.combine(day, time(start_hour), KST)
    return forecasting.NearbyEvent(
        "ev1", "축제", None, 650, start, start.replace(hour=end_hour), None, None, None, None, None, 37.55, 126.91
    )


def weather(condition="RAIN", temperature=22) -> forecasting.WeatherSnapshot:
    return forecasting.WeatherSnapshot("st", SAT, Decimal(temperature), condition, "비", None)


# ───────── F01 예측 ─────────

SATURDAY_SALES = daily_sales(SAT - timedelta(days=28), 28, lambda day: 60 if day.weekday() == 5 else 50)


def forecast(model=None, weather_snapshot=None, events=(), sold=SATURDAY_SALES, hours=SUNDAY_CLOSED, target=SAT):
    return forecasting.build(make_store(hours, model), target, (menu(),), sold, weather_snapshot, events, POLICY)


def test_f01_baseline_is_recent_level_times_weekday_tendency():
    [item] = forecast(model_json())
    assert (item.baseline, item.recommended, item.event_diff) == (60, 60, 0)
    assert forecasting.comparison(item, POLICY) == ("SIMILAR", 0)
    assert item.summary_text == "평소 토요일 60인분"
    [half_weight] = forecast(model_json(weekday=0.5))
    assert half_weight.recommended == 56
    [no_model] = forecast()
    assert no_model.recommended == 52


def test_f01_event_and_weather_factors():
    [with_event] = forecast(model_json(), events=(event(),))
    assert (with_event.recommended, with_event.event_diff) == (72, 12)
    assert forecasting.comparison(with_event, POLICY) == ("MORE", 12)
    assert with_event.summary_text == "평소 토요일 60인분 · 행사 +20%"
    rainy = {"m1": {"3:WET": Decimal("0.8")}}
    [full] = forecast(model_json(weather_index=rainy), weather())
    assert (full.recommended, full.summary_text) == (48, "평소 토요일 60인분 · 비 -20%")
    [half] = forecast(model_json(weather=0.5, weather_index=rainy), weather())
    assert half.recommended == 54
    assert forecast(model_json(weather_index=rainy), weather(temperature=15))[0].recommended == 60
    assert forecast(model_json(), weather())[0].recommended == 60


def test_f01_no_recent_learning_day_or_closed_day_makes_no_forecast():
    old = daily_sales(SAT - timedelta(days=60), 10, lambda day: 50)
    assert forecast(model_json(), sold=old) == []
    saturday_closed = tuple(replace(hours, closed=hours.day == "SAT") for hours in OPEN_HOURS)
    assert forecast(model_json(), hours=saturday_closed) == []
    [early] = forecast(model_json(), events=(event(6, 9),))
    assert early.event_diff == 0
    covered = sales.SalesSnapshot((("m1", SAT - timedelta(days=1), Decimal(40)),), ((SAT - timedelta(days=2), SAT),))
    [zero_filled] = forecast(sold=covered, hours=OPEN_HOURS)
    assert zero_filled.baseline == 20


def test_f01_weekday_tendency_is_store_wide():
    monday = date(2026, 11, 2)
    sold = daily_sales(monday, 14, lambda day: 160 if day.weekday() == 5 else 100)
    days = forecasting.learning_days(sold, SUNDAY_CLOSED)
    weekday_index, _ = forecasting.tendencies(days, ["m1"], sold, {}, POLICY)
    assert len(days) == 12
    assert (weekday_index["SAT"], weekday_index["MON"], weekday_index["SUN"]) == (
        Decimal("1.454545"),
        Decimal("0.909091"),
        1,
    )
    one_week, _ = forecasting.tendencies(days[:6], ["m1"], sold, {}, POLICY)
    assert set(one_week.values()) == {Decimal(1)}


def test_f01_weather_tendency_needs_enough_days_and_is_clamped():
    flat_weekdays = replace(POLICY, min_weekday_days=99)
    start = date(2026, 10, 1)
    wet_days = {start + timedelta(days=offset) for offset in range(5)}
    sold = daily_sales(start, 10, lambda day: 40 if day in wet_days else 60)
    days = forecasting.learning_days(sold, OPEN_HOURS)
    buckets = {day: "2:WET" if day in wet_days else "2:DRY" for day in days}
    _, weather_index = forecasting.tendencies(days, ["m1"], sold, buckets, flat_weekdays)
    assert weather_index["m1"] == {"2:WET": Decimal("0.8"), "2:DRY": Decimal("1.2")}
    four_wet = {day: bucket for day, bucket in buckets.items() if day != start}
    assert "2:WET" not in forecasting.tendencies(days, ["m1"], sold, four_wet, flat_weekdays)[1]["m1"]
    collapsed = daily_sales(start, 10, lambda day: 10 if day in wet_days else 90)
    assert forecasting.tendencies(days, ["m1"], collapsed, buckets, flat_weekdays)[1]["m1"] == {
        "2:WET": Decimal("0.5"),
        "2:DRY": Decimal("1.5"),
    }
    assert forecasting.weather_bucket(Decimal("20.0"), False, POLICY) == "3:DRY"
    assert forecasting.weather_bucket(Decimal("-0.1"), True, POLICY) == "0:WET"


def observed(day: date, rain_mm="0") -> WeatherObservation:
    return WeatherObservation(day, Decimal(15), Decimal(rain_mm))


def test_f01_weights_are_tuned_on_the_later_half():
    start = date(2026, 8, 3)

    def weekend_heavy(day):
        return 90 if day.weekday() >= 5 else 50

    short = daily_sales(start, 55, weekend_heavy)
    untuned = forecasting.fit_model(OPEN_HOURS, ["m1"], short, [], POLICY, datetime.now(KST))
    assert (untuned.weekday_weight, untuned.weather_weight, untuned.holdout_error) == (1, 1, None)

    enough = daily_sales(start, 56, weekend_heavy)
    tuned = forecasting.fit_model(OPEN_HOURS, ["m1"], enough, [], POLICY, datetime.now(KST))
    assert (tuned.weekday_weight, tuned.weather_weight, tuned.holdout_error, tuned.tuned_days) == (1, 1, 0, 56)
    assert tuned.weekday_index["SAT"] == Decimal("1.465116")

    rain_days = {start + timedelta(days=offset) for offset in range(0, 112, 4)}
    rain_sensitive = daily_sales(start, 112, lambda day: 30 if day in rain_days else 50)
    observations = [observed(start + timedelta(days=offset)) for offset in range(112)]
    observations = [
        observed(item.observed_on, "9.9") if item.observed_on in rain_days else item for item in observations
    ]
    fitted = forecasting.fit_model(OPEN_HOURS, ["m1"], rain_sensitive, observations, POLICY, datetime.now(KST))
    assert fitted.weather_weight == 1 and fitted.weather_index["m1"]["2:WET"] < 1
    drizzle = [observed(item.observed_on, "0.9") for item in observations]
    assert "2:WET" not in forecasting.fit_model(
        OPEN_HOURS, ["m1"], rain_sensitive, drizzle, POLICY, datetime.now(KST)
    ).weather_index.get("m1", {})
    again = forecasting.fit_model(OPEN_HOURS, ["m1"], rain_sensitive, observations, POLICY, fitted.fitted_at)
    assert again == fitted and forecasting.ForecastModel.from_json(fitted.to_json()).weather_weight == 1


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

EGG = catalog.IngredientSnapshot("egg", "st", "계란", "개")
GIMBAP = menu()
EGG_RECIPE = catalog.CatalogSnapshot("st", (GIMBAP,), (EGG,), (catalog.RecipeLine("m1", "egg", Decimal(1)),))
EGG_PRICE = Decimal("296.666667")


def lot(line_id=1, day=date(2026, 11, 8), quantity=60):
    return receipts.ReceiptLot(line_id, "egg", "s1", day, Decimal(quantity), EGG_PRICE, "계란 특란 30구")


def recorded_sales(*extra_rows) -> sales.SalesSnapshot:
    rows = (("m1", date(2026, 11, 8), Decimal(20)), ("m1", date(2026, 11, 9), Decimal(25)), *extra_rows)
    last_day = max(row[1] for row in rows)
    return sales.SalesSnapshot(rows, ((date(2026, 11, 8), last_day),))


def stock_on(as_of: date, sold=None, lots=None):
    return inventory.evaluate(EGG_RECIPE, sold or recorded_sales(), lots or [lot()], as_of)["egg"].stock


def test_f02_stock_is_receipts_minus_recorded_sales_only():
    tuesday = date(2026, 11, 10)
    assert stock_on(date(2026, 11, 9)) == 15
    assert stock_on(tuesday) == 15
    assert stock_on(tuesday, sold=recorded_sales(("m1", tuesday, Decimal(10)))) == 5
    assert inventory.evaluate(EGG_RECIPE, recorded_sales(), [], tuesday)["egg"].stock is None


def test_f02_usage_above_stock_goes_negative_with_warning():
    heavy = sales.SalesSnapshot((("m1", date(2026, 11, 8), Decimal(100)),), ((date(2026, 11, 8), date(2026, 11, 9)),))
    result = inventory.evaluate(EGG_RECIPE, heavy, [lot()], date(2026, 11, 9))["egg"]
    assert (result.stock, result.missing_purchase, result.stock_for_planning) == (-40, True, 0)


def test_f02_sales_before_first_purchase_are_ignored():
    early = recorded_sales(("m1", date(2026, 11, 7), Decimal(30)))
    assert stock_on(date(2026, 11, 9), sold=early) == 15
    assert inventory.evaluate(EGG_RECIPE, early, [lot()], date(2026, 11, 9))["egg"].started_on == date(2026, 11, 8)


def test_f02_second_purchase_adds_from_its_date():
    tuesday = date(2026, 11, 10)
    sold = recorded_sales(("m1", tuesday, Decimal(10)))
    two_lots = [lot(), lot(line_id=2, day=tuesday, quantity=30)]
    assert stock_on(date(2026, 11, 9), sold=sold, lots=two_lots) == 15
    assert stock_on(tuesday, sold=sold, lots=two_lots) == 35


def test_f02_stock_reset_ignores_earlier_ledger():
    reset = receipts.ReceiptLot(2, "egg", None, date(2026, 11, 9), Decimal(50), None, None, "MANUAL", True)
    result = inventory.evaluate(EGG_RECIPE, recorded_sales(), [lot(), reset], date(2026, 11, 9))["egg"]
    assert (result.stock, result.started_on) == (25, date(2026, 11, 9))


def forecast_row(planned: int, baseline=60, event_diff=0, adjusted=None) -> forecasting.ForecastSnapshot:
    return forecasting.ForecastSnapshot(
        GIMBAP, WED, Decimal(baseline), Decimal(planned), adjusted, None, Decimal(event_diff), None, None
    )


def supplier(order_days=("TUE",), status="CONFIRMED") -> suppliers.SupplierSnapshot:
    return suppliers.SupplierSnapshot("s1", "st", "대한식자재", None, False, None, order_days, status)


def plan(forecasts, remaining, target_supplier=None, order_day="TUE", price=EGG_PRICE):
    fact = receipts.LatestPurchaseFact("egg", "s1", "계란 특란 30구", date(2026, 11, 8), 1)
    stock = inventory.InventorySnapshot("egg", date(2026, 11, 10), None if remaining is None else Decimal(remaining))
    return ordering.plan(
        EGG_RECIPE,
        forecasts,
        {"egg": stock},
        {"egg": fact},
        {"egg": price},
        {"s1": target_supplier or supplier()},
        (),
        order_day,
        WED,
    )


def test_f02_order_is_the_shortage_in_ingredient_units():
    result = plan([forecast_row(75, baseline=60, event_diff=15)], 15)
    [line] = result.lines
    assert (line.quantity, line.unit, line.stock, line.event_diff, line.price) == (60, "개", 15, 15, EGG_PRICE)
    assert (result.total, result.usual_total) == (17800, 13350)
    [fraction] = plan([forecast_row(75)], Decimal("14.5")).lines
    assert fraction.quantity == 61
    assert plan([forecast_row(75)], 75).lines == ()


def test_adjusted_menu_has_no_event_increment():
    [line] = plan([forecast_row(60, event_diff=10, adjusted=Decimal(90))], 0).lines
    assert (line.quantity, line.event_diff) == (90, 0)


def test_supplier_must_order_today_with_known_days():
    assert plan([forecast_row(75)], 0, order_day="WED").lines == ()
    assert plan([forecast_row(75)], 0, supplier(status="UNKNOWN")).lines == ()
    assert plan([forecast_row(75)], 0, supplier(order_days=(), status="CONFIRMED")).lines == ()
    assert len(plan([forecast_row(75)], 0, supplier(status="ESTIMATED")).lines) == 1
    stock = {"egg": inventory.InventorySnapshot("egg", WED, None)}
    [unknown] = ordering.plan(EGG_RECIPE, [forecast_row(75)], stock, {}, {}, {}, (), "WED", WED).lines
    assert (unknown.supplier, unknown.quantity, unknown.price) == (None, 75, None)


def test_money_total_adds_only_known_prices():
    assert (
        ordering.money_total([(Decimal(60), EGG_PRICE), (Decimal(1), None), (Decimal(4538), Decimal("2.5"))]) == 29145
    )
    assert ordering.money_total([(Decimal(2), None)]) is None
    assert ordering.money_total([]) is None
    assert plan([forecast_row(75)], 15, price=None).total is None


# ───────── 문장·정책·수량 ─────────


def test_templates():
    assert texts.josa("김밥", "을", "를") == "김밥을"
    assert texts.josa("떡볶이", "을", "를") == "떡볶이를"
    assert texts.overnight_change_title("김밥", Decimal(-2)) == "밤사이 예보가 바뀌어 김밥을 2인분 줄였어요"
    assert texts.overnight_change_title(None, Decimal(0)) == "밤사이 날씨 예보가 바뀌었어요"
    assert texts.delivery_expected_banner(
        [("계란", Decimal(90), "개"), ("김", Decimal(200), "개"), ("떡", Decimal(5000), "g"), ("무", Decimal(1), "개")]
    ) == (
        "어젯밤 발주한 재료가 오전에 들어와요",
        "계란 90개 · 김 200개 · 떡 5000g 외 1개",
    )
    assert texts.low_stock_banner(["계란"], False) == (
        "계란 재고가 얼마 안 남았어요",
        "발주해 주세요 · 이미 발주했다면 영수증을 올려주세요",
    )
    assert texts.low_stock_banner(["계란", "김", "떡"], True) == (
        "계란·김 외 1개 재고가 얼마 안 남았어요",
        "이미 발주한 재료예요 · 받았다면 영수증을 올려주세요",
    )
    assert texts.low_stock_push_suffix(["계란", "김"]) == " · 계란 외 1개 발주가 필요해요"
    assert texts.low_stock_push_suffix(["계란"]) == " · 계란 발주가 필요해요" and texts.low_stock_push_suffix([]) == ""
    assert texts.weather_change_text("비 · 14시부터", "비 · 13시부터") == "비 · 14시부터 → 비 · 13시부터"
    assert texts.weather_change_text("비", "비") is None and texts.weather_change_text(None, "비") is None
    assert texts.push_message("NIGHT", "김밥", Decimal(60), 12) == (
        "내일 준비량이 나왔어요",
        "김밥 60인분 등 12개 메뉴",
    )
    assert texts.push_message("MORNING", "김밥", Decimal(48), 12) == (
        "오늘 미리 만들어둘 양",
        "김밥 48인분 등 12개 메뉴",
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
        [("계란 특란 30구", Decimal(60), "개"), ("떡볶이떡 2kg", Decimal(4538), "g")],
    )
    assert message.splitlines() == [
        "[행복분식] 11/11(수) 발주",
        "계란 특란 30구 × 60개",
        "떡볶이떡 2kg × 4538g",
        "내일 오전 10시 전 도착 부탁드립니다.",
    ]
    assert texts.purchase_order_message("행복분식", WED, WED, 10, []).endswith("11/11 오전 10시 전 도착 부탁드립니다.")
    title, body = texts.event_today_banner(
        datetime.combine(WED, time(12), KST), datetime.combine(WED, time(18), KST), 13, 16, "김밥"
    )
    assert title == "오늘 근처 행사 · 12:00 ~ 18:00"
    assert body == "13~16시가 가장 붐벼요 · 12시 전에 김밥 준비를 끝내두세요"


def test_policy_lookups():
    used = ("축제-문화/예술", "축제-기타", "콘서트")
    unused = (
        "클래식",
        "연극",
        "뮤지컬/오페라",
        "무용",
        "국악",
        "독주/독창회",
        "교육/체험",
        "전시/미술",
        "영화",
        "기타",
        None,
    )
    assert all(POLICY.uses_event_category(name) for name in used)
    assert not any(POLICY.uses_event_category(name) for name in unused)


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
    mapping = {"date": "주문일자", "menuName": "상품명", "quantity": "수량"}
    records = sales.parse_sales_rows(rows, mapping)
    assert [(record.sold_on, record.menu_name, record.quantity) for record in records] == [
        (date(2026, 11, 1), "김밥", 2),
        (date(2026, 11, 1), "김밥", -1),
        (date(2026, 11, 2), "김밥", 3),
    ]
