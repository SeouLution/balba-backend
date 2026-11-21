"""실제 adapter의 응답 매핑. 저장한 응답 fixture로 확인하고 실제 서비스는 부르지 않는다."""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.infrastructure.providers import real
from app.infrastructure.providers.ports import (
    CrowdPlace,
    GeoPoint,
    ProviderInvalidPayload,
    ProviderUnavailable,
    WeatherObservation,
)
from app.shared.types import KST

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers"


def fixture_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_kakao_address_maps_x_to_longitude_and_y_to_latitude():
    point = real.parse_kakao_address(fixture_json("kakao_address_documented_fields.json"))
    assert point == GeoPoint(latitude=35.97664845766847, longitude=126.99597295767953)
    assert real.parse_kakao_address({"meta": {"total_count": 0}, "documents": []}) is None
    with pytest.raises(ProviderInvalidPayload):
        real.parse_kakao_address({"errorType": "AccessDeniedError"})


@pytest.mark.parametrize(
    ("latitude", "longitude", "grid"),
    [
        (37.5703777777777, 126.981641666666, (60, 127)),
        (37.5607055555555, 126.910530555555, (59, 127)),
        (35.1600194444444, 129.165808333333, (99, 75)),
        (33.25406007, 126.559550, (53, 33)),
    ],
)
def test_kma_grid_matches_the_official_grid_table(latitude, longitude, grid):
    assert real.to_kma_grid(latitude, longitude) == grid


def test_kma_base_time_waits_ten_minutes_after_publication():
    def base(moment: str):
        return real.latest_kma_base(datetime.fromisoformat(moment + "+09:00"))

    assert base("2026-11-10T19:50:00") == ("20261110", "1700")
    assert base("2026-11-10T17:09:00") == ("20261110", "1400")
    assert base("2026-11-10T17:10:00") == ("20261110", "1700")
    assert base("2026-11-11T07:20:00") == ("20261111", "0500")
    assert base("2026-11-11T01:00:00") == ("20261110", "2300")


def test_kma_guide_sample_maps_to_hourly_weather():
    items, total = real.parse_kma_items((FIXTURES / "kma_vilage_fcst_guide_sample.xml").read_text(encoding="utf-8"))
    assert total == 742
    assert items == [
        {
            "baseDate": "20210628",
            "baseTime": "0500",
            "category": "TMP",
            "fcstDate": "20210628",
            "fcstTime": "0600",
            "fcstValue": "21",
            "nx": "55",
            "ny": "127",
        }
    ]
    day = real.to_day_weather(items, date(2021, 6, 28))
    assert [(hour.at, hour.temperature_c, hour.condition) for hour in day.hours] == [
        (datetime(2021, 6, 28, 6, 0, tzinfo=KST), Decimal(21), None)
    ]


def test_kma_codes_and_errors():
    def item(category, value, fcst_time="1400", fcst_date="20261111"):
        return {"category": category, "fcstValue": value, "fcstDate": fcst_date, "fcstTime": fcst_time}

    items = [
        item("TMP", "7"),
        item("SKY", "4"),
        item("PTY", "1"),
        item("TMP", "9", "1500"),
        item("SKY", "3", "1500"),
        item("PTY", "0", "1500"),
        item("TMX", "11.0", "1500"),
        item("TMP", "1", "0100", "20261112"),
        item("SKY", "1", "0100", "20261112"),
        item("TMP", "5", "1400", "20261113"),
    ]
    day = real.to_day_weather(items, date(2026, 11, 11))
    assert day.daily_max_c == Decimal("11.0")
    assert [(hour.at.hour, hour.temperature_c, hour.condition) for hour in day.hours] == [
        (14, 7, "RAIN"),
        (15, 9, "PARTLY_CLOUDY"),
        (1, 1, "CLEAR"),
    ]
    no_data = "<response><header><resultCode>03</resultCode><resultMsg>NO_DATA</resultMsg></header></response>"
    with pytest.raises(ProviderUnavailable):
        real.parse_kma_items(no_data)
    with pytest.raises(ProviderInvalidPayload):
        real.parse_kma_items(no_data.replace("03", "30"))
    with pytest.raises(ProviderInvalidPayload):
        real.parse_kma_items("<OpenAPI_ServiceResponse><cmmMsgHeader/></OpenAPI_ServiceResponse>")
    with pytest.raises(ProviderInvalidPayload):
        real.parse_kma_items("not xml")


def test_asos_daily_items_map_to_observations():
    items = [
        {"tm": "2026-11-08", "maxTa": "12.3", "sumRn": "4.5"},
        {"tm": "2026-11-09", "maxTa": "-1.0", "sumRn": ""},
        {"tm": "2026-11-10", "maxTa": "", "sumRn": "1.0"},
    ]
    assert real.to_weather_observations(items) == [
        WeatherObservation(date(2026, 11, 8), Decimal("12.3"), Decimal("4.5")),
        WeatherObservation(date(2026, 11, 9), Decimal("-1.0"), Decimal(0)),
    ]
    with pytest.raises(ProviderInvalidPayload):
        real.to_weather_observations([{"maxTa": "1.0"}])


def test_seoul_events_keep_only_events_open_on_the_target_date():
    rows, total = real.seoul_rows(fixture_json("seoul_cultural_events.json"), "culturalEventInfo")
    assert (len(rows), total) == (3, 19782)
    assert real.to_event_records(rows, date(2026, 11, 21)) == []
    [concert] = real.to_event_records(rows, date(2026, 12, 26))
    assert concert.id == "seoul_160099"
    assert concert.name == "2026 송년음악회 [One Voice, One Dream]"
    assert concert.place == "국립국악원 예악당"
    assert concert.category == "국악"
    assert (concert.latitude, concert.longitude) == (37.4777188083482, 127.009818464555)
    assert concert.start_at == datetime(2026, 12, 26, 0, 0, tzinfo=KST)
    assert concert.end_at == datetime(2026, 12, 27, 0, 0, tzinfo=KST)
    assert concert.detail_url.startswith("https://culture.seoul.go.kr/")

    by_date, _ = real.seoul_rows(fixture_json("seoul_cultural_events_by_date.json"), "culturalEventInfo")
    open_on_day = real.to_event_records(by_date, date(2026, 11, 20))
    assert {record.start_at.date() for record in open_on_day} == {date(2026, 11, 14), date(2026, 11, 10)}


def test_seoul_coordinates_are_taken_by_value_range_and_bad_rows_are_dropped():
    row = fixture_json("seoul_cultural_events.json")["culturalEventInfo"]["row"][1]
    swapped = {**row, "LAT": row["LOT"], "LOT": row["LAT"]}
    [record] = real.to_event_records([swapped], date(2026, 12, 26))
    assert (record.latitude, record.longitude) == (37.4777188083482, 127.009818464555)
    outside = {**row, "LAT": "35.1", "LOT": "129.0"}
    missing = {**row, "LAT": "", "LOT": ""}
    assert real.to_event_records([outside, missing], date(2026, 12, 26)) == []
    assert real.seoul_rows(fixture_json("seoul_no_data.json"), "culturalEventInfo") == ([], 0)
    with pytest.raises(ProviderInvalidPayload):
        real.seoul_rows({"RESULT": {"CODE": "INFO-100", "MESSAGE": "인증키가 유효하지 않습니다."}}, "culturalEventInfo")


def test_seoul_citydata_maps_forecast_population_and_levels():
    payload = fixture_json("seoul_citydata_population.json")
    place = CrowdPlace("POI009", "광화문·덕수궁", 37.570112, 126.978163)
    hours = real.to_crowd_hours(payload, place)
    assert len(hours) == 12
    assert hours[0].at == datetime(2026, 11, 20, 22, 0, tzinfo=KST)
    assert (hours[0].level, hours[0].population) == ("NORMAL", 18000)
    with pytest.raises(ProviderInvalidPayload):
        real.to_crowd_hours(payload, CrowdPlace("POI094", "망원한강공원", 37.553278, 126.901004))
    assert real.CROWD_LEVEL == {"여유": "NORMAL", "보통": "NORMAL", "약간 붐빔": "BUSY", "붐빔": "VERY_BUSY"}


def test_seoul_place_list_has_121_places_inside_seoul():
    places = real.load_crowd_places()
    assert len(places) == 121 and len({place.code for place in places}) == 121
    assert all(37.4 <= place.latitude <= 37.72 and 126.75 <= place.longitude <= 127.2 for place in places)


async def test_http_retries_only_429_and_5xx_twice():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(int(request.url.path.strip("/")))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        for status, error, expected_calls in (
            (503, ProviderUnavailable, 3),
            (429, ProviderUnavailable, 3),
            (401, ProviderInvalidPayload, 1),
        ):
            calls.clear()
            with pytest.raises(error) as raised:
                await real.get_with_retry(client, f"http://provider.test/{status}", params={"serviceKey": "secret"})
            assert len(calls) == expected_calls
            assert "secret" not in str(raised.value)
        assert (await real.get_with_retry(client, "http://provider.test/200")).status_code == 200


async def test_claude_adapter_requests_structured_output_and_rejects_refusals():
    adapter = real.ClaudeAdapter("test-key", "claude-opus-5-5")
    sent = {}
    parsed = real.ReceiptOut(
        readable=True,
        supplierName="대한식자재",
        phone=None,
        purchasedOn="2026-11-08",
        lines=[
            real.ReceiptLineOut(
                rawProductName="계란 특란 30구",
                amount=60,
                unit="개",
                linePrice=17800,
                match=real.ReceiptMatchOut(ingredientName="계란", kind="exact"),
            )
        ],
    )
    reply = SimpleNamespace(stop_reason="end_turn", parsed_output=parsed)

    async def fake_parse(**kwargs):
        sent.update(kwargs)
        return reply

    adapter.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=fake_parse)))
    reading = await adapter.read_receipt(b"\xff\xd8\xff", {"계란": "개"})
    assert (reading.supplier_name, reading.purchased_on) == ("대한식자재", date(2026, 11, 8))
    assert reading.lines[0].amount == Decimal("60.0") and reading.lines[0].match_kind == "exact"
    assert sent["model"] == "claude-opus-5-5" and sent["output_format"] is real.ReceiptOut
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert "tool_choice" not in sent and "thinking" not in sent
    image = sent["messages"][0]["content"][0]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg"

    for stop_reason in ("refusal", "max_tokens"):
        reply.stop_reason = stop_reason
        with pytest.raises(ProviderInvalidPayload):
            await adapter.read_receipt(b"\xff\xd8\xff", {})
    assert real.to_receipt_reading(parsed.model_copy(update={"purchasedOn": "11월 8일"})).purchased_on is None


def test_live_kakao_response_maps_to_a_point_in_seoul():
    point = real.parse_kakao_address(fixture_json("kakao_address_live.json"))
    assert 37.4 <= point.latitude <= 37.7 and 126.7 <= point.longitude <= 127.2


def test_live_kma_response_maps_to_hourly_weather():
    items, total = real.parse_kma_items((FIXTURES / "kma_vilage_fcst_live.xml").read_text(encoding="utf-8"))
    assert len(items) == 30 and total > len(items)
    assert {"baseDate", "baseTime", "category", "fcstDate", "fcstTime", "fcstValue", "nx", "ny"} == set(items[0])
    first_day = datetime.strptime(items[0]["fcstDate"], "%Y%m%d").date()
    day = real.to_day_weather(items, first_day)
    assert day.hours and all(hour.temperature_c is not None for hour in day.hours)
    assert {hour.condition for hour in day.hours} <= {
        "CLEAR",
        "PARTLY_CLOUDY",
        "CLOUDY",
        "RAIN",
        "RAIN_SNOW",
        "SNOW",
        "SHOWER",
    }
