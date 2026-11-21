from dataclasses import dataclass

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.db.tables import background_job
from app.infrastructure.db.uow import UoW
from app.shared.types import InternalFailure, new_id


class PermanentJobFailure(Exception):
    """다시 실행해도 같은 결과인 실패. 재시도 없이 DEAD로 끝낸다."""


class PostponeJob(Exception):
    """아직 실행할 수 없는 작업. attempts를 올리지 않고 뒤로 미룬다."""


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    payload: dict
    dedupe_key: str
    state: str
    attempts: int


def _job(row) -> Job:
    return Job(row.id, row.kind, row.payload, row.dedupe_key, row.state, row.attempts)


async def enqueue(uow: UoW, kind: str, payload: dict, dedupe_key: str) -> None:
    await uow.execute(
        insert(background_job)
        .values(id=new_id("job"), kind=kind, payload=payload, dedupe_key=dedupe_key)
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )


async def find(uow: UoW, dedupe_key: str) -> Job | None:
    row = (await uow.execute(select(background_job).where(background_job.c.dedupe_key == dedupe_key))).first()
    return _job(row) if row else None


async def latest_for_store(uow: UoW, kind: str, store_id: str) -> Job | None:
    row = (
        await uow.execute(
            select(background_job)
            .where(background_job.c.kind == kind, background_job.c.payload["storeId"].astext == store_id)
            .order_by(background_job.c.created_at.desc(), background_job.c.id.desc())
            .limit(1)
        )
    ).first()
    return _job(row) if row else None


async def fail_if_dead(uow: UoW, *dedupe_keys: str) -> None:
    dead = (
        await uow.execute(
            select(background_job.c.id).where(
                background_job.c.dedupe_key.in_(dedupe_keys), background_job.c.state == "DEAD"
            )
        )
    ).first()
    if dead:
        raise InternalFailure(f"job {dead.id} is DEAD")


async def succeed(uow: UoW, job: Job, result: dict | None = None) -> None:
    await uow.execute(
        update(background_job)
        .where(background_job.c.id == job.id)
        .values(state="SUCCEEDED", finished_at=func.now(), last_error=None, payload={**job.payload, **(result or {})})
    )


async def claim_next(uow: UoW) -> Job | None:
    row = (
        await uow.execute(
            select(background_job)
            .where(background_job.c.state == "READY", background_job.c.available_at <= func.now())
            .order_by(background_job.c.created_at, background_job.c.id)
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    await uow.execute(update(background_job).where(background_job.c.id == row.id).values(state="RUNNING"))
    return _job(row)


async def release_running(uow: UoW) -> None:
    await uow.execute(update(background_job).where(background_job.c.state == "RUNNING").values(state="READY"))


async def reschedule(uow: UoW, job: Job, delay_seconds: int, error: str | None, attempts: int, payload: dict) -> None:
    await uow.execute(
        update(background_job)
        .where(background_job.c.id == job.id)
        .values(
            state="READY",
            attempts=attempts,
            last_error=error,
            payload=payload,
            available_at=func.now() + text(f"interval '{int(delay_seconds)} seconds'"),
        )
    )


async def bury(uow: UoW, job: Job, error: str) -> None:
    await uow.execute(
        update(background_job)
        .where(background_job.c.id == job.id)
        .values(state="DEAD", attempts=job.attempts + 1, last_error=error, finished_at=func.now())
    )


async def enqueue_refresh_context(uow: UoW, store_id: str, target_date, slot: str) -> None:
    payload = {"storeId": store_id, "targetDate": target_date.isoformat(), "slot": slot}
    await enqueue(uow, "REFRESH_CONTEXT", payload, f"context:{store_id}:{target_date.isoformat()}:{slot}")


async def enqueue_forecast(uow: UoW, store_id: str, target_date, slot: str, source_job_id: str | None = None) -> None:
    payload = {"storeId": store_id, "targetDate": target_date.isoformat(), "slot": slot}
    key = forecast_key(store_id, target_date, slot)
    if slot == "DATA_CHANGED":
        payload["sourceJobId"] = source_job_id
        key += f":{source_job_id}"
    await enqueue(uow, "GENERATE_FORECAST", payload, key)


def forecast_key(store_id: str, target_date, slot: str) -> str:
    return f"forecast:{store_id}:{target_date.isoformat()}:{slot}"


async def enqueue_push(uow: UoW, store_id: str, target_date, slot: str) -> None:
    payload = {"storeId": store_id, "targetDate": target_date.isoformat(), "slot": slot}
    await enqueue(uow, "SEND_PUSH", payload, f"push:{store_id}:{target_date.isoformat()}:{slot}")
