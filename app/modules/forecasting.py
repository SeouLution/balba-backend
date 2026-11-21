import asyncio
import logging
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.bootstrap import Container
from app.infrastructure.db.tables import event, forecast, menu, weather
from app.infrastructure.db.uow import UoW
from app.infrastructure.jobs import queue
from app.infrastructure.providers.ports import (
    CrowdHour,
    DayWeather,
    EventRecord,
    GeoPoint,
    ProviderInvalidPayload,
    ProviderUnavailable,
)
from app.modules import catalog, sales, stores
from app.shared import texts
from app.shared.policy import Policy
from app.shared.types import (
    KST,
    InvalidRequest,
    NotFound,
    business_window,
    ceil_int,
    kst_midnight,
    round_half_up,
)

logger = logging.getLogger(__name__)
EARTH_RADIUS_METERS = 6_371_000
CROWD_PLACE_MAX_METERS = 500
PROVIDER_CALL_SECONDS = 90
BASELINE_WEEKS = (1, 2, 3, 4)
SALES_LOOKBACK_DAYS = 28
PRECIPITATION = ("RAIN", "SNOW", "RAIN_SNOW", "SHOWER")


@dataclass(frozen=True)
class WeatherSnapshot:
    store_id: str
    target_date: date
    temperature_c: Decimal
    condition: str
    summary_text: str
    change_text: str | None


@dataclass(frozen=True)
class NearbyEvent:
    id: str
    name: str
    place: str | None
    distance_meters: int
    start_at: datetime
    end_at: datetime
    crowd_level: str | None
    expected_visitors: int | None
    peak_from: time | None
    peak_to: time | None
    detail_url: str | None
    latitude: float
    longitude: float


@dataclass(frozen=True)
class ForecastContext:
    weather: WeatherSnapshot | None
    events: tuple[NearbyEvent, ...]


@dataclass(frozen=True)
class ForecastSnapshot:
    menu: catalog.MenuSnapshot
    target_date: date
    baseline: Decimal
    recommended: Decimal
    adjusted: Decimal | None
    produced: Decimal | None
    event_diff: Decimal
    previous_recommended: Decimal | None
    summary_text: str | None

    @property
    def planned(self) -> Decimal:
        return self.adjusted if self.adjusted is not None else self.recommended

    @property
    def planned_without_event(self) -> Decimal:
        return self.adjusted if self.adjusted is not None else self.recommended - self.event_diff


@dataclass(frozen=True)
class UsageRow:
    menu_id: str
    target_date: date
    produced: Decimal | None
    planned: Decimal


# ───────── 순수 계산 ─────────


def haversine_meters(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> int:
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    half_dphi = (phi_b - phi_a) / 2
    half_dlambda = math.radians(lng_b - lng_a) / 2
    chord = math.sin(half_dphi) ** 2 + math.cos(phi_a) * math.cos(phi_b) * math.sin(half_dlambda) ** 2
    return round(2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(chord)))


def baseline_for(sales_snapshot: sales.SalesSnapshot, menu_id: str, target_date: date) -> tuple[Decimal, int] | None:
    """지난 4주 같은 요일 중 판매 기록이 있는 날의 평균과 표본 수. 표본이 없으면 None."""
    samples = [
        sold
        for weeks in BASELINE_WEEKS
        if (sold := sales_snapshot.sold(menu_id, target_date - timedelta(weeks=weeks))) is not None
    ]
    if not samples:
        return None
    return sum(samples, Decimal(0)) / len(samples), len(samples)


def events_during(events: tuple[NearbyEvent, ...], window: tuple[datetime, datetime] | None) -> tuple[NearbyEvent, ...]:
    if window is None:
        return ()
    start, end = window
    return tuple(item for item in events if item.start_at < end and item.end_at > start)


def build(
    store: stores.StoreSnapshot,
    target_date: date,
    menus: tuple[catalog.MenuSnapshot, ...],
    sales_snapshot: sales.SalesSnapshot,
    weather_snapshot: WeatherSnapshot | None,
    events: tuple[NearbyEvent, ...],
    policy: Policy,
) -> list[ForecastSnapshot]:
    window = business_window(store.business_hours, target_date)
    if window is None:
        return []
    condition = weather_snapshot.condition if weather_snapshot else None
    weather_factor = policy.weather_factor_for(store.category, condition)
    event_factor = policy.event_factor if events_during(events, window) else Decimal(1)
    drafts = []
    for target_menu in menus:
        sampled = baseline_for(sales_snapshot, target_menu.id, target_date)
        if sampled is None:
            continue
        baseline, sample_weeks = sampled
        recommended = Decimal(ceil_int(baseline * weather_factor * event_factor))
        drafts.append(
            ForecastSnapshot(
                menu=target_menu,
                target_date=target_date,
                baseline=baseline,
                recommended=recommended,
                adjusted=None,
                produced=None,
                event_diff=recommended - ceil_int(baseline * weather_factor),
                previous_recommended=None,
                summary_text=texts.forecast_summary(
                    sample_weeks, target_date, baseline, target_menu.unit, event_factor, condition, weather_factor
                ),
            )
        )
    return drafts


def comparison(item: ForecastSnapshot, policy: Policy) -> tuple[str, int]:
    diff = round_half_up(item.recommended - item.baseline)
    if abs(diff) <= policy.similar_max_diff:
        return "SIMILAR", diff
    return ("MORE" if diff > 0 else "LESS"), diff


def rank_by_recent_sales(sales_snapshot: sales.SalesSnapshot, menu_ids: list[str]) -> list[str]:
    """판매합 내림차순, 동률은 menuId 순."""
    totals: dict[str, Decimal] = dict.fromkeys(menu_ids, Decimal(0))
    for menu_id, _, quantity in sales_snapshot.rows:
        if menu_id in totals:
            totals[menu_id] += quantity
    return sorted(menu_ids, key=lambda menu_id: (-totals[menu_id], menu_id))


def summarize_weather(day_weather: DayWeather, window: tuple[datetime, datetime]) -> tuple[Decimal, str, str] | None:
    """영업시간 예보를 (기온, 상태, 문구)로 줄인다. 강수 형태가 있으면 가장 긴 것, 없으면 하늘 상태."""
    start, end = window
    hours = sorted((hour for hour in day_weather.hours if start <= hour.at < end), key=lambda hour: hour.at)
    conditions = [hour.condition for hour in hours if hour.condition]
    temperatures = [hour.temperature_c for hour in hours if hour.temperature_c is not None]
    temperature = day_weather.daily_max_c if day_weather.daily_max_c is not None else max(temperatures, default=None)
    if not conditions or temperature is None:
        return None
    wet = [condition for condition in conditions if condition in PRECIPITATION]
    condition = Counter(wet or conditions).most_common(1)[0][0]
    starts_at_hour = None
    if wet:
        first_wet = next(hour for hour in hours if hour.condition in PRECIPITATION)
        if first_wet.at > hours[0].at:
            starts_at_hour = first_wet.at.astimezone(KST).hour
    return temperature, condition, texts.weather_text(condition, starts_at_hour)


def crowd_peak(hours: list[CrowdHour], target_date: date) -> CrowdHour | None:
    same_day = [hour for hour in hours if hour.at.astimezone(KST).date() == target_date]
    return max(same_day, key=lambda hour: (hour.population, -hour.at.timestamp()), default=None)


# ───────── 읽기 포트 ─────────


def _forecast(row, target_menu: catalog.MenuSnapshot) -> ForecastSnapshot:
    return ForecastSnapshot(
        menu=target_menu,
        target_date=row.target_date,
        baseline=row.baseline,
        recommended=row.recommended,
        adjusted=row.adjusted,
        produced=row.produced,
        event_diff=row.event_diff,
        previous_recommended=row.previous_recommended,
        summary_text=row.summary_text,
    )


async def read(uow: UoW, store_id: str, target_date: date) -> list[ForecastSnapshot]:
    rows = (
        await uow.execute(
            select(forecast, menu.c.store_id, menu.c.name, menu.c.unit, menu.c.price)
            .join(menu, menu.c.id == forecast.c.menu_id)
            .where(menu.c.store_id == store_id, forecast.c.target_date == target_date)
            .order_by(forecast.c.menu_id)
        )
    ).all()
    return [
        _forecast(row, catalog.MenuSnapshot(row.menu_id, row.store_id, row.name, row.unit, row.price)) for row in rows
    ]


async def usage(uow: UoW, store_id: str, start: date, through: date) -> list[UsageRow]:
    rows = (
        await uow.execute(
            select(
                forecast.c.menu_id,
                forecast.c.target_date,
                forecast.c.produced,
                forecast.c.adjusted,
                forecast.c.recommended,
            )
            .join(menu, menu.c.id == forecast.c.menu_id)
            .where(menu.c.store_id == store_id, forecast.c.target_date.between(start, through))
        )
    ).all()
    return [
        UsageRow(
            row.menu_id, row.target_date, row.produced, row.adjusted if row.adjusted is not None else row.recommended
        )
        for row in rows
    ]


async def read_weather(uow: UoW, store_id: str, target_date: date) -> WeatherSnapshot | None:
    row = (
        await uow.execute(select(weather).where(weather.c.store_id == store_id, weather.c.target_date == target_date))
    ).first()
    if row is None:
        return None
    return WeatherSnapshot(
        row.store_id, row.target_date, row.temperature_c, row.condition, row.summary_text, row.change_text
    )


async def context(uow: UoW, store: stores.StoreSnapshot, target_date: date, policy: Policy) -> ForecastContext:
    """대상일의 날씨와 반경 안 행사. 행사는 거리, 시작 시각, id 순이다."""
    weather_snapshot = await read_weather(uow, store.id, target_date)
    if store.latitude is None:
        return ForecastContext(weather_snapshot, ())
    day_start = kst_midnight(target_date)
    rows = (
        await uow.execute(
            select(event).where(event.c.start_at < day_start + timedelta(days=1), event.c.end_at > day_start)
        )
    ).all()
    nearby = []
    for row in rows:
        distance = haversine_meters(store.latitude, store.longitude, row.latitude, row.longitude)
        if distance <= policy.event_radius_meters:
            nearby.append(
                NearbyEvent(
                    id=row.id,
                    name=row.name,
                    place=row.place,
                    distance_meters=distance,
                    start_at=row.start_at,
                    end_at=row.end_at,
                    crowd_level=row.crowd_level,
                    expected_visitors=row.expected_visitors,
                    peak_from=row.peak_from,
                    peak_to=row.peak_to,
                    detail_url=row.detail_url,
                    latitude=row.latitude,
                    longitude=row.longitude,
                )
            )
    nearby.sort(key=lambda item: (item.distance_meters, item.start_at, item.id))
    return ForecastContext(weather_snapshot, tuple(nearby))


# ───────── 사용자 쓰기 ─────────


async def _require_forecast(uow: UoW, store_id: str, target_date: date, menu_id: str) -> ForecastSnapshot:
    target_menu = await catalog.require_menu(uow, store_id, menu_id)
    row = (
        await uow.execute(select(forecast).where(forecast.c.menu_id == menu_id, forecast.c.target_date == target_date))
    ).first()
    if row is None:
        raise NotFound("forecast")
    return _forecast(row, target_menu)


async def set_adjusted(uow: UoW, store_id: str, target_date: date, menu_id: str, quantity: Decimal) -> ForecastSnapshot:
    await _require_forecast(uow, store_id, target_date, menu_id)
    await uow.execute(
        update(forecast)
        .where(forecast.c.menu_id == menu_id, forecast.c.target_date == target_date)
        .values(adjusted=quantity)
    )
    return await _require_forecast(uow, store_id, target_date, menu_id)


async def set_production(deps: Container, store_id: str, target_date: date, menu_id: str, quantity: Decimal) -> None:
    ctx = deps.clock.context(store_id)
    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        await _require_forecast(uow, store_id, target_date, menu_id)
        if target_date != ctx.local_date:
            raise InvalidRequest("production can only be recorded for today")
        await uow.execute(
            update(forecast)
            .where(forecast.c.menu_id == menu_id, forecast.c.target_date == target_date)
            .values(produced=quantity)
        )


# ───────── 작업 handler ─────────


async def _safe_call(label: str, call):
    """날씨·행사 호출이 실패해도 기존 행을 그대로 두고 예측은 진행한다."""
    try:
        async with asyncio.timeout(PROVIDER_CALL_SECONDS):
            return await call
    except (ProviderUnavailable, ProviderInvalidPayload, TimeoutError) as error:
        logger.warning("context refresh skipped provider=%s reason=%s", label, type(error).__name__)
        return None


async def _crowd_updates(
    deps: Container, store: stores.StoreSnapshot, records: list[EventRecord], target_date: date
) -> dict:
    updates: dict[str, CrowdHour] = {}
    places = deps.providers.crowd.places()
    for record in records:
        if (
            haversine_meters(store.latitude, store.longitude, record.latitude, record.longitude)
            > deps.policy.event_radius_meters
        ):
            continue
        nearest = min(
            places,
            key=lambda place: haversine_meters(record.latitude, record.longitude, place.latitude, place.longitude),
            default=None,
        )
        if nearest is None or (
            haversine_meters(record.latitude, record.longitude, nearest.latitude, nearest.longitude)
            > CROWD_PLACE_MAX_METERS
        ):
            continue
        hours = await _safe_call("crowd", deps.providers.crowd.forecast(nearest))
        peak = crowd_peak(hours or [], target_date)
        if peak is not None:
            updates[record.id] = peak
    return updates


async def refresh_context(deps: Container, job: queue.Job) -> None:
    store_id, slot = job.payload["storeId"], job.payload["slot"]
    target_date = date.fromisoformat(job.payload["targetDate"])
    async with deps.db.read() as uow:
        try:
            store = await stores.require(uow, store_id)
        except NotFound as error:
            raise queue.PermanentJobFailure("store not found") from error

    summary = None
    records: list[EventRecord] | None = None
    crowd: dict[str, CrowdHour] = {}
    window = business_window(store.business_hours, target_date)
    if store.latitude is not None:
        point = GeoPoint(store.latitude, store.longitude)
        if window is not None:
            day_weather = await _safe_call(
                "weather", deps.providers.weather.day_weather(point, target_date, deps.clock.now())
            )
            summary = summarize_weather(day_weather, window) if day_weather else None
        fetched = await _safe_call("events", deps.providers.events.events_on(target_date))
        if fetched is not None:
            records = [record for record in fetched if deps.policy.uses_event_category(record.category)]
        if slot == "MORNING" and records:
            crowd = await _crowd_updates(deps, store, records, target_date)

    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        if summary is not None:
            await _save_weather(uow, store_id, target_date, slot, summary)
        for record in records or []:
            values = {
                "name": record.name,
                "place": record.place,
                "latitude": record.latitude,
                "longitude": record.longitude,
                "start_at": record.start_at,
                "end_at": record.end_at,
                "detail_url": record.detail_url,
            }
            await uow.execute(
                pg_insert(event)
                .values(id=record.id, **values)
                .on_conflict_do_update(index_elements=["id"], set_=values)
            )
        for event_id, peak in crowd.items():
            peak_from = peak.at.astimezone(KST)
            await uow.execute(
                update(event)
                .where(event.c.id == event_id)
                .values(
                    crowd_level=peak.level,
                    expected_visitors=peak.population,
                    peak_from=peak_from.time().replace(tzinfo=None),
                    peak_to=(peak_from + timedelta(hours=1)).time().replace(tzinfo=None),
                )
            )
        await queue.enqueue_forecast(uow, store_id, target_date, slot, job.id)
        await queue.succeed(uow, job)


async def _night_reference(uow: UoW, store_id: str, target_date: date) -> dict | None:
    night_job = await queue.find(uow, queue.forecast_key(store_id, target_date, "NIGHT"))
    if night_job is None or night_job.state != "SUCCEEDED":
        return None
    return night_job.payload.get("nightReference")


async def _save_weather(
    uow: UoW, store_id: str, target_date: date, slot: str, summary: tuple[Decimal, str, str]
) -> None:
    temperature, condition, text = summary
    existing = await read_weather(uow, store_id, target_date)
    change_text = None
    if slot == "MORNING":
        night = await _night_reference(uow, store_id, target_date)
        change_text = texts.weather_change_text(night.get("weatherText") if night else None, text)
    elif slot == "DATA_CHANGED" and existing is not None and existing.summary_text == text:
        change_text = existing.change_text
    values = {"temperature_c": temperature, "condition": condition, "summary_text": text, "change_text": change_text}
    await uow.execute(
        pg_insert(weather)
        .values(store_id=store_id, target_date=target_date, **values)
        .on_conflict_do_update(index_elements=["store_id", "target_date"], set_=values)
    )


async def generate_forecast(deps: Container, job: queue.Job) -> None:
    store_id, slot = job.payload["storeId"], job.payload["slot"]
    target_date = date.fromisoformat(job.payload["targetDate"])
    async with deps.db.write() as uow:
        try:
            store = await stores.lock(uow, store_id)
        except NotFound as error:
            raise queue.PermanentJobFailure("store not found") from error
        menus = (await catalog.snapshot(uow, store_id)).menus
        sales_snapshot = await sales.read(
            uow, store_id, target_date - timedelta(days=SALES_LOOKBACK_DAYS), target_date - timedelta(days=1)
        )
        forecast_context = await context(uow, store, target_date, deps.policy)
        drafts = build(
            store, target_date, menus, sales_snapshot, forecast_context.weather, forecast_context.events, deps.policy
        )
        existing = {item.menu.id: item for item in await read(uow, store_id, target_date)}
        night = await _night_reference(uow, store_id, target_date) if slot == "MORNING" else None
        night_by_menu = (night or {}).get("recommendedByMenuId", {})

        for draft in drafts:
            previous = None
            current = existing.get(draft.menu.id)
            if slot == "MORNING" and draft.menu.id in night_by_menu:
                night_value = Decimal(str(night_by_menu[draft.menu.id]))
                previous = night_value if night_value != draft.recommended else None
            elif slot == "DATA_CHANGED" and current is not None and current.recommended == draft.recommended:
                previous = current.previous_recommended
            values = {
                "recommended": draft.recommended,
                "baseline": draft.baseline,
                "event_diff": draft.event_diff,
                "summary_text": draft.summary_text,
                "previous_recommended": previous,
            }
            await uow.execute(
                pg_insert(forecast)
                .values(menu_id=draft.menu.id, target_date=target_date, **values)
                .on_conflict_do_update(index_elements=["menu_id", "target_date"], set_=values)
            )
        if drafts:
            await stores.mark_first_forecast(uow, store_id, deps.clock.now())

        result: dict = {}
        if slot in ("NIGHT", "MORNING"):
            saved = {item.menu.id: item for item in await read(uow, store_id, target_date)}
            ranked = rank_by_recent_sales(sales_snapshot, list(saved))
            result["pushText"] = None
            if ranked:
                top = saved[ranked[0]]
                title, body = texts.push_message(slot, top.menu.name, top.planned, top.menu.unit, len(saved))
                result["pushText"] = {"title": title, "body": body}
        if slot == "NIGHT":
            result["nightReference"] = {
                "recommendedByMenuId": {draft.menu.id: float(draft.recommended) for draft in drafts},
                "weatherText": forecast_context.weather.summary_text if forecast_context.weather else None,
            }
        await queue.succeed(uow, job, result)
