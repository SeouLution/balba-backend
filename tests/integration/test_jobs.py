"""작업 루프의 재시도·DEAD·복구·dedupe, 스케줄, 밤사이 변경과 푸시, 실패 복구(A04)."""

import json
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from app.infrastructure.jobs import queue, schedule
from app.infrastructure.jobs.loop import JobRunner
from app.infrastructure.providers.ports import (
    CrowdHour,
    CrowdPlace,
    DayWeather,
    EventRecord,
    HourlyWeather,
    ProviderInvalidPayload,
    ProviderUnavailable,
    WeatherObservation,
)
from app.shared.types import KST
from tests import helpers
from tests.acceptance.test_onboarding import SALES, onboard
from tests.conftest import kst
from tests.fakes import receipt_image

ERD = Path(__file__).resolve().parents[2] / "docs" / "erd.dbml"
WED = date(2026, 11, 11)


async def test_migration_matches_the_erd(deps):
    declared: dict[str, set[str]] = {}
    table = None
    depth = 0
    for line in ERD.read_text(encoding="utf-8").splitlines():
        if matched := re.match(r"^Table (\w+) {", line):
            table, depth = matched[1], 1
            declared[table] = set()
            continue
        if table is None:
            continue
        depth += line.count("{") - line.count("}")
        if depth == 0:
            table = None
        elif depth == 1 and (column := re.match(r"^  (\w+) \w+", line)) and column[1] != "Note":
            declared[table].add(column[1])
    rows = await helpers.fetch_all(
        deps, "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
    )
    actual: dict[str, set[str]] = {}
    for row in rows:
        if row.table_name != "alembic_version":
            actual.setdefault(row.table_name, set()).add(row.column_name)
    assert len(declared) == 17
    assert actual == declared


async def test_constraints_reject_invalid_rows(deps):
    store_sql = (
        "INSERT INTO store (id, name, category, road_address, business_hours, latitude) "
        "VALUES ('st_x', '가게', :category, '주소', :hours, :latitude)"
    )
    for params in (
        {"category": "CAFE", "hours": "[1,2,3,4,5,6,7]", "latitude": None},
        {"category": "OTHER", "hours": "[1]", "latitude": None},
        {"category": "OTHER", "hours": "[1,2,3,4,5,6,7]", "latitude": 37.5},
    ):
        try:
            await helpers.execute(deps, store_sql.replace(":hours", "CAST(:hours AS jsonb)"), **params)
        except Exception as error:
            assert "CheckViolation" in str(error) or "violates check" in str(error)
        else:
            raise AssertionError(f"constraint accepted {params}")
    assert await helpers.count(deps, "store") == 0


async def test_retry_then_dead_and_status_becomes_500(api, deps, runner):
    store_id = (await api.call("createStore", 201, json=helpers.store_body())).json()["id"]
    await runner.run_until_idle()
    deps.providers.columns.error = ProviderUnavailable("timeout")
    content = helpers.sales_csv(SALES, date(2026, 11, 1), date(2026, 11, 9))
    accepted = await api.call(
        "createSalesImport", 202, storeId=store_id, files={"file": ("pos.csv", content, "text/csv")}
    )
    import_id = accepted.json()["id"]

    assert await runner.run_once()
    row = (
        await helpers.fetch_all(
            deps, "SELECT state, attempts, last_error FROM background_job WHERE kind = 'PARSE_SALES'"
        )
    )[0]
    assert (row.state, row.attempts) == ("READY", 1) and "ProviderUnavailable" in row.last_error
    assert (await api.call("getSalesImport", 200, storeId=store_id, importId=import_id)).json()["status"] == "PARSING"

    assert await runner.run_until_idle() == 2
    row = (await helpers.fetch_all(deps, "SELECT state, attempts FROM background_job WHERE kind = 'PARSE_SALES'"))[0]
    assert (row.state, row.attempts) == ("DEAD", 3)
    assert deps.providers.columns.calls == 3
    await api.call("getSalesImport", 500, storeId=store_id, importId=import_id)
    await api.call("confirmSalesImport", 500, storeId=store_id, importId=import_id)
    assert await helpers.count(deps, "daily_sales") == 0


async def test_retry_succeeds_after_a_transient_failure(api, deps, runner):
    store_id = (await api.call("createStore", 201, json=helpers.store_body())).json()["id"]
    deps.providers.geocoder.error = ProviderUnavailable("timeout")
    assert await runner.run_once()
    deps.providers.geocoder.error = None
    await runner.run_until_idle()
    geo = await helpers.fetch_all(deps, "SELECT latitude, longitude FROM store WHERE id = :id", id=store_id)
    assert tuple(geo[0]) == (37.5559, 126.9108)
    states = await helpers.job_states(deps)
    assert states[f"geocode:{store_id}"] == "SUCCEEDED"
    assert {key for key in states if key.startswith("context:")} == {
        f"context:{store_id}:2026-11-10:DATA_CHANGED",
        f"context:{store_id}:2026-11-11:DATA_CHANGED",
    }


async def test_geocode_without_result_keeps_coordinates_empty(api, deps, runner):
    deps.providers.geocoder.point = None
    store_id = (await api.call("createStore", 201, json=helpers.store_body())).json()["id"]
    await runner.run_until_idle()
    geo = await helpers.fetch_all(deps, "SELECT latitude, longitude FROM store WHERE id = :id", id=store_id)
    assert tuple(geo[0]) == (None, None)
    assert await helpers.job_states(deps) == {f"geocode:{store_id}": "SUCCEEDED"}


async def test_unreadable_files_die_immediately(api, deps, runner):
    store_id = (await api.call("createStore", 201, json=helpers.store_body())).json()["id"]
    await runner.run_until_idle()

    content = helpers.sales_csv(SALES, date(2026, 11, 1), date(2026, 11, 2))
    third = (
        await api.call("createSalesImport", 202, storeId=store_id, files={"file": ("c.csv", content, "text/csv")})
    ).json()["id"]
    file_path = (await helpers.fetch_all(deps, "SELECT file_path FROM sales_import WHERE id = :id", id=third))[
        0
    ].file_path
    deps.files.delete(file_path)
    await runner.run_until_idle()
    await api.call("getSalesImport", 500, storeId=store_id, importId=third)

    jobs = await helpers.fetch_all(
        deps, "SELECT state, attempts FROM background_job WHERE kind = 'PARSE_SALES' ORDER BY created_at"
    )
    assert [(job.state, job.attempts) for job in jobs] == [("DEAD", 1)]


async def test_work_survives_a_restart_and_runs_once(api, deps, runner):
    store_id = (await api.call("createStore", 201, json=helpers.store_body())).json()["id"]
    content = helpers.sales_csv(SALES, date(2026, 9, 29), date(2026, 11, 9))
    import_id = (
        await api.call("createSalesImport", 202, storeId=store_id, files={"file": ("pos.csv", content, "text/csv")})
    ).json()["id"]
    await runner.run_until_idle()
    await api.call("confirmSalesImport", 202, storeId=store_id, importId=import_id)

    await helpers.execute(deps, "UPDATE background_job SET state = 'RUNNING' WHERE kind = 'APPLY_SALES'")
    restarted = JobRunner(deps, retry_delays=(0, 0), postpone_seconds=0)
    assert not await restarted.run_once()
    await restarted.recover()
    await restarted.run_until_idle()
    assert (await api.call("getSalesImport", 200, storeId=store_id, importId=import_id)).json()["status"] == "COMPLETED"
    total = (await helpers.fetch_all(deps, "SELECT sum(quantity) AS total FROM daily_sales"))[0].total

    await helpers.execute(
        deps, "UPDATE background_job SET state = 'READY' WHERE kind IN ('APPLY_SALES', 'PARSE_SALES')"
    )
    await restarted.run_until_idle()
    assert (await helpers.fetch_all(deps, "SELECT sum(quantity) AS total FROM daily_sales"))[0].total == total
    assert await helpers.count(deps, "menu") == 3
    states = await helpers.job_states(deps)
    assert sum(1 for key in states if key.startswith("recipe:")) == 1


async def test_same_cell_is_replaced_by_the_latest_file(api, deps, runner):
    ids = await onboard(api, deps, runner)
    newer = helpers.sales_csv({"김밥": (70, 4500)}, date(2026, 11, 9), date(2026, 11, 9))
    await helpers.upload_sales(api, runner, ids["storeId"], newer)
    cell = await helpers.fetch_all(
        deps,
        "SELECT quantity FROM daily_sales WHERE menu_id = :menu AND sold_on = '2026-11-09'",
        menu=ids["menus"]["김밥"],
    )
    assert cell[0].quantity == 70
    menus = await helpers.fetch_all(deps, "SELECT name FROM menu ORDER BY name")
    assert [row.name for row in menus] == ["김밥", "떡볶이", "어묵탕"]
    assert (await helpers.fetch_all(deps, "SELECT generation_status FROM store"))[0].generation_status == "READY"
    assert deps.providers.recipes.calls == 1


async def test_model_is_fitted_after_sales_even_when_past_weather_fails(api, deps, runner):
    first_day = date(2026, 9, 29)
    deps.providers.observations.records = [
        WeatherObservation(first_day + timedelta(days=offset), Decimal(15), Decimal(0)) for offset in range(42)
    ]
    ids = await onboard(api, deps, runner)
    assert await helpers.count(deps, "weather_observation") == 42
    model = (await helpers.fetch_all(deps, "SELECT forecast_model FROM store"))[0].forecast_model
    assert (model["weights"], model["tunedDays"], model["holdoutError"]) == ({"weekday": 1, "weather": 1}, 42, None)
    assert model["weatherIndex"][ids["menus"]["김밥"]] == {"2:DRY": 1}

    deps.providers.observations.error = ProviderUnavailable("timeout")
    await helpers.execute(deps, "DELETE FROM weather_observation")
    newer = helpers.sales_csv({"김밥": (70, 4500)}, date(2026, 11, 9), date(2026, 11, 9))
    await helpers.upload_sales(api, runner, ids["storeId"], newer)
    assert set((await helpers.job_states(deps)).values()) == {"SUCCEEDED"}
    refitted = (await helpers.fetch_all(deps, "SELECT forecast_model FROM store"))[0].forecast_model
    assert refitted["weatherIndex"] == {} and refitted["fittedAt"] is not None
    assert await helpers.count(deps, "forecast") == 6


async def test_recipe_generation_failure_surfaces_as_500(api, deps, runner):
    deps.providers.recipes.error = ProviderInvalidPayload("schema violation")
    store_id = (await api.call("createStore", 201, json=helpers.store_body())).json()["id"]
    content = helpers.sales_csv(SALES, date(2026, 11, 1), date(2026, 11, 9))
    await helpers.upload_sales(api, runner, store_id, content)
    await api.call("listRecipes", 500, storeId=store_id)
    assert await helpers.count(deps, "ingredient") == 0


async def test_receipt_reading_failures(api, deps, runner):
    ids = await onboard(api, deps, runner)
    store_id = ids["storeId"]
    image = receipt_image("대한식자재", "2026-11-08", [{"name": "계란", "amount": 30, "unit": "개"}])

    deps.providers.receipts.error = ProviderInvalidPayload("refusal")
    refused = await helpers.scan_receipts(api, runner, store_id, [image])
    view = (await api.call("getReceiptScan", 200, storeId=store_id, scanId=refused)).json()
    assert (view["status"], view["groups"]) == ("NEEDS_REVIEW", [])
    assert view["removedDuplicateTexts"] == ["1번째 사진은 읽지 못해서 뺐어요"]

    deps.providers.receipts.error = ProviderUnavailable("timeout")
    failed = await helpers.scan_receipts(api, runner, store_id, [image])
    await api.call("getReceiptScan", 500, storeId=store_id, scanId=failed)
    await api.call("confirmReceiptScan", 500, storeId=store_id, scanId=failed)
    assert await helpers.count(deps, "receipt_line") == 0


async def test_dedupe_key_registers_a_job_once(deps):
    async with deps.db.write() as uow:
        for _ in range(3):
            await queue.enqueue(uow, "GEOCODE_STORE", {"storeId": "st_x"}, "geocode:st_x")
    assert await helpers.count(deps, "background_job") == 1


async def test_schedule_registers_each_slot_once(api, deps, runner):
    ids = await onboard(api, deps, runner)
    store_id = ids["storeId"]
    await api.call("putPushToken", 204, storeId=store_id, json={"token": helpers.PUSH_TOKEN})
    hours = helpers.business_hours(closed_days=("THU",))
    closed = (await api.call("createStore", 201, json=helpers.store_body(businessHours=hours))).json()["id"]
    await runner.run_until_idle()
    await api.call("putPushToken", 204, storeId=closed, json={"token": helpers.PUSH_TOKEN})
    await helpers.execute(deps, "DELETE FROM background_job")

    deps.clock.fixed = kst("2026-11-11T07:19:00")
    await schedule.register_due_slots(deps)
    assert await helpers.job_states(deps) == {}

    deps.clock.fixed = kst("2026-11-11T20:01:00")
    for _ in range(3):
        await schedule.register_due_slots(deps)
    assert set(await helpers.job_states(deps)) == {
        f"context:{store_id}:2026-11-12:NIGHT",
        f"push:{store_id}:2026-11-12:NIGHT",
    }

    await helpers.execute(deps, "DELETE FROM background_job")
    deps.clock.fixed = kst("2026-11-12T08:01:00")
    await schedule.register_due_slots(deps)
    assert set(await helpers.job_states(deps)) == {f"context:{store_id}:2026-11-12:MORNING"}


def rainy_day(start_hour: int) -> DayWeather:
    hours = tuple(
        HourlyWeather(datetime.combine(WED, time(hour), KST), Decimal(7), "RAIN" if hour >= start_hour else "CLOUDY")
        for hour in range(6, 23)
    )
    return DayWeather(hours, Decimal(9))


async def test_overnight_change_and_morning_push(api, deps, runner):
    ids = await onboard(api, deps, runner)
    store_id, gimbap = ids["storeId"], ids["menus"]["김밥"]
    await api.call("putPushToken", 204, storeId=store_id, json={"token": helpers.PUSH_TOKEN})
    await helpers.execute(
        deps,
        "UPDATE store SET forecast_model = jsonb_set(forecast_model, '{weatherIndex}', CAST(:index AS jsonb))",
        index=json.dumps({gimbap: {"1:WET": 0.9}}),
    )
    deps.providers.weather.day = DayWeather(
        tuple(HourlyWeather(datetime.combine(WED, time(hour), KST), Decimal(7), "CLOUDY") for hour in range(6, 23)),
        Decimal(9),
    )

    await helpers.enqueue_context(deps, store_id, WED, "NIGHT")
    async with deps.db.write() as uow:
        await queue.enqueue_push(uow, store_id, WED, "NIGHT")
    await runner.run_until_idle()
    await helpers.release_postponed_jobs(deps)
    await runner.run_until_idle()
    assert deps.providers.push.sent == [(helpers.PUSH_TOKEN, "내일 준비량이 나왔어요", "김밥 50인분 등 3개 메뉴", "/")]
    night = (await api.call("getHome", 200, storeId=store_id)).json()
    assert night["tiles"]["weather"] == {"temperatureC": 9, "condition": "CLOUDY", "text": "흐림", "changeText": None}
    assert night["forecast"]["items"][0]["previousRecommended"] is None

    deps.clock.fixed = kst("2026-11-11T07:30:00")
    deps.providers.weather.day = rainy_day(14)
    async with deps.db.write() as uow:
        await queue.enqueue_push(uow, store_id, WED, "MORNING")
    for _ in range(3):
        await runner.run_until_idle()
        await helpers.release_postponed_jobs(deps)
    assert len(deps.providers.push.sent) == 1
    waiting = await helpers.fetch_all(
        deps, "SELECT state, attempts, payload FROM background_job WHERE kind = 'SEND_PUSH' AND state = 'READY'"
    )
    assert waiting[0].attempts == 0 and waiting[0].payload["waits"] == 3

    await helpers.enqueue_context(deps, store_id, WED, "MORNING")
    await runner.run_until_idle()
    await helpers.release_postponed_jobs(deps)
    await runner.run_until_idle()
    assert deps.providers.push.sent[1][1:3] == ("오늘 미리 만들어둘 양", "김밥 45인분 등 3개 메뉴")
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    first = home["forecast"]["items"][0]
    assert (first["recommended"], first["previousRecommended"]) == (45, 50)
    assert first["summaryText"] == "평소 수요일 50인분 · 비 -10%"
    assert first["comparison"] == {"direction": "LESS", "diff": -5}
    assert home["tiles"]["weather"]["text"] == "비 · 14시부터"
    assert home["tiles"]["weather"]["changeText"] == "흐림 → 비 · 14시부터"
    assert home["banners"][0] == {
        "type": "OVERNIGHT_CHANGE",
        "title": "밤사이 예보가 바뀌어 김밥을 5인분 줄였어요",
        "message": None,
    }

    await helpers.execute(
        deps, "UPDATE background_job SET state = 'READY' WHERE dedupe_key LIKE :key", key="forecast:%:MORNING"
    )
    await runner.run_until_idle()
    again = (await api.call("getHome", 200, storeId=store_id)).json()["forecast"]["items"][0]
    assert (again["recommended"], again["previousRecommended"]) == (45, 50)

    await api.call("adjustForecast", 200, storeId=store_id, date="2026-11-11", menuId=gimbap, json={"quantity": 0})
    deps.providers.weather.day = rainy_day(13)
    await helpers.execute(deps, "DELETE FROM background_job WHERE dedupe_key LIKE :key", key="context:%:DATA_CHANGED")
    async with deps.db.write() as uow:
        await queue.enqueue_refresh_context(uow, store_id, WED, "DATA_CHANGED")
    await runner.run_until_idle()
    same = (await api.call("getHome", 200, storeId=store_id)).json()
    assert same["forecast"]["items"][0]["planned"] == 0
    assert same["forecast"]["items"][0]["previousRecommended"] == 50
    assert same["tiles"]["weather"]["text"] == "비 · 13시부터" and same["tiles"]["weather"]["changeText"] is None

    deps.providers.weather.day = DayWeather(
        tuple(HourlyWeather(datetime.combine(WED, time(hour), KST), Decimal(7), "CLEAR") for hour in range(6, 23)),
        Decimal(9),
    )
    await helpers.execute(deps, "DELETE FROM background_job WHERE dedupe_key LIKE :key", key="context:%:DATA_CHANGED")
    async with deps.db.write() as uow:
        await queue.enqueue_refresh_context(uow, store_id, WED, "DATA_CHANGED")
    await runner.run_until_idle()
    cleared = (await api.call("getHome", 200, storeId=store_id)).json()["forecast"]["items"][0]
    assert (cleared["recommended"], cleared["previousRecommended"], cleared["planned"]) == (50, None, 0)


async def test_push_is_skipped_when_forecast_never_arrives_and_expired_subscription_is_cleared(api, deps, runner):
    ids = await onboard(api, deps, runner)
    store_id = ids["storeId"]
    await api.call("putPushToken", 204, storeId=store_id, json={"token": helpers.PUSH_TOKEN})
    async with deps.db.write() as uow:
        await queue.enqueue_push(uow, store_id, WED, "NIGHT")
    for _ in range(11):
        await helpers.release_postponed_jobs(deps)
        assert await runner.run_until_idle() == 1
    assert deps.providers.push.sent == []
    assert (await helpers.job_states(deps))[f"push:{store_id}:2026-11-11:NIGHT"] == "SUCCEEDED"

    deps.providers.push.delivered = False
    await helpers.enqueue_context(deps, store_id, date(2026, 11, 12), "NIGHT")
    async with deps.db.write() as uow:
        await queue.enqueue_push(uow, store_id, date(2026, 11, 12), "NIGHT")
    await runner.run_until_idle()
    await helpers.release_postponed_jobs(deps)
    await runner.run_until_idle()
    assert (await helpers.fetch_all(deps, "SELECT push_token FROM store WHERE id = :id", id=store_id))[
        0
    ].push_token is None


async def test_events_feed_forecast_order_plan_and_home(api, deps, runner):
    ids = await onboard(api, deps, runner)
    store_id = ids["storeId"]

    def record(event_id, name, longitude_offset, category):
        return EventRecord(
            id=event_id,
            name=name,
            place="잠실" if event_id == "ev_far" else None,
            latitude=37.5559,
            longitude=126.9108 + longitude_offset,
            start_at=kst("2026-11-11T12:00:00"),
            end_at=kst("2026-11-11T18:00:00"),
            detail_url=None,
            category=category,
        )

    near = record("ev_near", "망원한강공원 축제", 0.0113, "축제-문화/예술")
    far = record("ev_far", "먼 행사", 0.05, "콘서트")
    library_class = record("ev_class", "도서관 상시 프로그램", 0.001, "교육/체험")
    exhibition = record("ev_exhibition", "상설 전시", 0.002, "전시/미술")
    uncategorized = record("ev_unknown", "분류 없는 행사", 0.003, None)
    deps.providers.events.records = [near, far, library_class, exhibition, uncategorized]
    deps.providers.crowd.place_list = [CrowdPlace("POI001", "망원한강공원", 37.5559, 126.9108 + 0.0113)]
    deps.providers.crowd.hours = [
        CrowdHour(kst("2026-11-11T13:00:00"), "BUSY", 18000),
        CrowdHour(kst("2026-11-11T14:00:00"), "VERY_BUSY", 20000),
    ]

    await helpers.enqueue_context(deps, store_id, WED, "NIGHT")
    await runner.run_until_idle()
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    [event] = home["tiles"]["events"]
    assert event["id"] == "ev_near" and 990 <= event["distanceMeters"] <= 1010
    assert "place" not in event and "crowdLevel" not in event
    assert (event["expectedVisitors"], event["peakFrom"], event["peakTo"], event["detailUrl"]) == (
        None,
        None,
        None,
        None,
    )
    assert event["startAt"] == "2026-11-11T12:00:00+09:00"
    first = home["forecast"]["items"][0]
    assert (first["recommended"], first["eventDiff"]) == (60, 10)
    assert first["summaryText"] == "평소 수요일 50인분 · 행사 +20%"
    assert first["comparison"] == {"direction": "MORE", "diff": 10}
    assert [banner["type"] for banner in home["banners"]] == ["FIRST_FORECAST"]
    plan = (await api.call("getOrderPlan", 200, storeId=store_id, targetDate="2026-11-11")).json()
    assert plan["events"] == [event] and plan["suppliers"] == []

    deps.clock.fixed = kst("2026-11-11T07:40:00")
    await helpers.enqueue_context(deps, store_id, WED, "MORNING")
    await runner.run_until_idle()
    home = (await api.call("getHome", 200, storeId=store_id)).json()
    [event] = home["tiles"]["events"]
    assert (event["crowdLevel"], event["expectedVisitors"], event["peakFrom"], event["peakTo"]) == (
        "VERY_BUSY",
        20000,
        "14:00",
        "15:00",
    )
    assert home["banners"] == [
        {
            "type": "EVENT_TODAY",
            "title": "오늘 근처 행사 · 12:00 ~ 18:00",
            "message": "14~15시가 가장 붐벼요 · 12시 전에 김밥 준비를 끝내두세요",
        }
    ]

    deps.providers.events.error = ProviderUnavailable("timeout")
    deps.providers.weather.error = ProviderUnavailable("timeout")
    await helpers.execute(deps, "DELETE FROM background_job")
    async with deps.db.write() as uow:
        await queue.enqueue_refresh_context(uow, store_id, WED, "DATA_CHANGED")
    await runner.run_until_idle()
    assert set((await helpers.job_states(deps)).values()) == {"SUCCEEDED"}
    assert await helpers.count(deps, "event") == 2


async def test_low_stock_counts_menus_whose_forecast_is_missing(api, deps, runner):
    deps.providers.recipes.recipes = {"떡볶이": [("떡", "g", 200)], "계란말이": [("계란", "개", 1)]}
    store_id = (await api.call("createStore", 201, json=helpers.store_body())).json()["id"]
    content = helpers.sales_csv({"떡볶이": (20, 5000), "계란말이": (35, 6000)}, date(2026, 9, 29), date(2026, 11, 9))
    await helpers.upload_sales(api, runner, store_id, content)
    egg_line = {
        "name": "계란 특란 30구",
        "amount": 60,
        "unit": "개",
        "linePrice": 17800,
        "match": "계란",
        "kind": "exact",
    }
    scan_id = await helpers.scan_receipts(
        api, runner, store_id, [receipt_image("대한식자재", "2026-11-10", [egg_line])]
    )
    await api.call("confirmReceiptScan", 204, storeId=store_id, scanId=scan_id, json={"answers": []})
    await helpers.execute(deps, "DELETE FROM forecast WHERE menu_id = (SELECT id FROM menu WHERE name = '계란말이')")
    assert [
        row.name for row in await helpers.fetch_all(deps, "SELECT name FROM forecast JOIN menu ON id = menu_id")
    ] == [
        "떡볶이",
        "떡볶이",
    ]

    home = (await api.call("getHome", 200, storeId=store_id)).json()
    assert [banner["title"] for banner in home["banners"] if banner["type"] == "LOW_STOCK"] == [
        "계란 재고가 얼마 안 남았어요"
    ]


async def test_concurrent_requests_on_one_store_are_serialized(api, deps, runner):
    import asyncio

    ids = await onboard(api, deps, runner)
    store_id, gimbap = ids["storeId"], ids["menus"]["김밥"]

    content = helpers.sales_csv({"김밥": (60, 4000)}, date(2026, 11, 9), date(2026, 11, 9))
    accepted = await api.call(
        "createSalesImport", 202, storeId=store_id, files={"file": ("pos.csv", content, "text/csv")}
    )
    import_id = accepted.json()["id"]
    await runner.run_until_idle()
    await asyncio.gather(*(api.call("confirmSalesImport", 202, storeId=store_id, importId=import_id) for _ in range(5)))
    apply_jobs = await helpers.fetch_all(
        deps, "SELECT id FROM background_job WHERE dedupe_key = :key", key=f"apply:{import_id}"
    )
    assert len(apply_jobs) == 1
    await runner.run_until_idle()

    quantities = [10, 20, 30, 40, 50, 60]
    responses = await asyncio.gather(
        *(
            api.call(
                "adjustForecast", 200, storeId=store_id, date="2026-11-11", menuId=gimbap, json={"quantity": quantity}
            )
            for quantity in quantities
        )
    )
    assert sorted(response.json()["item"]["planned"] for response in responses) == quantities
    saved = await helpers.fetch_all(
        deps, "SELECT adjusted FROM forecast WHERE menu_id = :id AND target_date = '2026-11-11'", id=gimbap
    )
    assert saved[0].adjusted in quantities

    image = receipt_image(
        "대한식자재", "2026-11-08", [{"name": "계란 특란", "amount": 60, "unit": "개", "linePrice": 17800}]
    )
    scan_id = await helpers.scan_receipts(api, runner, store_id, [image])
    await asyncio.gather(*(api.call("confirmReceiptScan", 204, storeId=store_id, scanId=scan_id) for _ in range(5)))
    assert await helpers.count(deps, "receipt_line") == 1
    assert await helpers.count(deps, "supplier") == 1
