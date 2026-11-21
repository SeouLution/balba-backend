import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import insert, select, update

from app.bootstrap import Container
from app.infrastructure.db.tables import store
from app.infrastructure.db.uow import UoW
from app.infrastructure.jobs import queue
from app.shared.types import (
    DAYS,
    DayHours,
    InvalidRequest,
    NotFound,
    hhmm,
    new_id,
    normalize_name,
    parse_hhmm,
)

PUSH_TOKEN_MAX_LENGTH = 4096


@dataclass(frozen=True)
class StoreSnapshot:
    id: str
    name: str
    category: str
    road_address: str
    business_hours: tuple[DayHours, ...]
    latitude: float | None
    longitude: float | None
    generation_status: str
    first_forecast_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class StoreCreateInput:
    name: str
    category: str
    road_address: str
    business_hours: tuple[DayHours, ...]


def _hours_from_json(raw: list[dict]) -> tuple[DayHours, ...]:
    return tuple(
        DayHours(
            day=item["day"],
            closed=item["closed"],
            open=None if item["closed"] else parse_hhmm(item["open"]),
            close=None if item["closed"] else parse_hhmm(item["close"]),
        )
        for item in raw
    )


def _hours_to_json(hours: tuple[DayHours, ...]) -> list[dict]:
    return [
        {"day": item.day, "closed": True}
        if item.closed
        else {"day": item.day, "closed": False, "open": hhmm(item.open), "close": hhmm(item.close)}
        for item in hours
    ]


def _snapshot(row) -> StoreSnapshot:
    return StoreSnapshot(
        id=row.id,
        name=row.name,
        category=row.category,
        road_address=row.road_address,
        business_hours=_hours_from_json(row.business_hours),
        latitude=row.latitude,
        longitude=row.longitude,
        generation_status=row.generation_status,
        first_forecast_at=row.first_forecast_at,
        created_at=row.created_at,
    )


async def require(uow: UoW, store_id: str, for_update: bool = False) -> StoreSnapshot:
    statement = select(store).where(store.c.id == store_id)
    if for_update:
        statement = statement.with_for_update()
    row = (await uow.execute(statement)).first()
    if row is None:
        raise NotFound("store")
    return _snapshot(row)


async def lock(uow: UoW, store_id: str) -> StoreSnapshot:
    """쓰기 유스케이스는 가게 행을 잠그고 시작한다. 같은 가게의 쓰기가 차례로 실행된다."""
    return await require(uow, store_id, for_update=True)


def validated_business_hours(hours: list[DayHours]) -> tuple[DayHours, ...]:
    by_day = {item.day: item for item in hours}
    if len(hours) != 7 or set(by_day) != set(DAYS):
        raise InvalidRequest("businessHours needs each day exactly once")
    ordered = []
    for day in DAYS:
        item = by_day[day]
        if item.closed:
            ordered.append(DayHours(day=day, closed=True))
            continue
        if item.open is None or item.close is None or item.open == item.close:
            raise InvalidRequest("open day needs different open and close times")
        ordered.append(item)
    return tuple(ordered)


async def create_store(deps: Container, payload: StoreCreateInput) -> StoreSnapshot:
    name = normalize_name(payload.name)
    road_address = normalize_name(payload.road_address)
    if not 1 <= len(name) <= 40 or not road_address:
        raise InvalidRequest("name or address is empty or too long")
    hours = validated_business_hours(list(payload.business_hours))
    store_id = new_id("st")
    async with deps.db.write() as uow:
        await uow.execute(
            insert(store).values(
                id=store_id,
                name=name,
                category=payload.category,
                road_address=road_address,
                business_hours=_hours_to_json(hours),
                generation_status="GENERATING",
                created_at=deps.clock.now(),
            )
        )
        await queue.enqueue(uow, "GEOCODE_STORE", {"storeId": store_id}, f"geocode:{store_id}")
        return await require(uow, store_id)


def validated_push_token(token: str) -> str:
    if len(token) > PUSH_TOKEN_MAX_LENGTH:
        raise InvalidRequest("push token is too long")
    try:
        subscription = json.loads(token)
        endpoint = subscription["endpoint"]
        keys = subscription["keys"]
        valid = (
            isinstance(endpoint, str)
            and endpoint.startswith("https://")
            and isinstance(keys["p256dh"], str)
            and isinstance(keys["auth"], str)
            and bool(keys["p256dh"])
            and bool(keys["auth"])
        )
    except (ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise InvalidRequest("push token is not a web push subscription")
    return token


async def register_push_token(deps: Container, store_id: str, token: str) -> None:
    async with deps.db.read() as uow:
        await require(uow, store_id)
    validated_push_token(token)
    async with deps.db.write() as uow:
        await lock(uow, store_id)
        await uow.execute(
            update(store)
            .where(store.c.id == store_id, store.c.push_token.is_distinct_from(token))
            .values(push_token=token)
        )


async def push_token(uow: UoW, store_id: str) -> str | None:
    return (await uow.execute(select(store.c.push_token).where(store.c.id == store_id))).scalar()


async def clear_push_token_if_unchanged(uow: UoW, store_id: str, sent_token: str) -> None:
    await uow.execute(
        update(store).where(store.c.id == store_id, store.c.push_token == sent_token).values(push_token=None)
    )


async def mark_first_forecast(uow: UoW, store_id: str, at: datetime) -> None:
    await uow.execute(
        update(store).where(store.c.id == store_id, store.c.first_forecast_at.is_(None)).values(first_forecast_at=at)
    )


async def set_generation_status(uow: UoW, store_id: str, status: str) -> None:
    await uow.execute(update(store).where(store.c.id == store_id).values(generation_status=status))


async def list_for_schedule(uow: UoW) -> list[tuple[StoreSnapshot, bool]]:
    rows = (await uow.execute(select(store).order_by(store.c.id))).all()
    return [(_snapshot(row), row.push_token is not None) for row in rows]


async def geocode_store(deps: Container, job: queue.Job) -> None:
    store_id = job.payload["storeId"]
    async with deps.db.read() as uow:
        try:
            snapshot = await require(uow, store_id)
        except NotFound as error:
            raise queue.PermanentJobFailure("store not found") from error
    point = None if snapshot.latitude is not None else await deps.providers.geocoder.geocode(snapshot.road_address)
    async with deps.db.write() as uow:
        await lock(uow, store_id)
        if point is not None:
            await uow.execute(
                update(store).where(store.c.id == store_id).values(latitude=point.latitude, longitude=point.longitude)
            )
            today = deps.clock.context(store_id).local_date
            for target_date in (today, today + timedelta(days=1)):
                await queue.enqueue_refresh_context(uow, store_id, target_date, "DATA_CHANGED")
        await queue.succeed(uow, job)
