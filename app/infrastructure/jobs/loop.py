import asyncio
import logging
import time
from datetime import date

from app.bootstrap import Container
from app.infrastructure.jobs import queue, schedule
from app.modules import catalog, forecasting, ordering, receipts, sales, stores
from app.shared import texts

logger = logging.getLogger("app.jobs")
MAX_ATTEMPTS = 3
MAX_PUSH_WAITS = 10


async def send_push(deps: Container, job: queue.Job) -> None:
    """같은 가게·대상일·slot 예측이 성공했을 때만 그 작업의 문구를 보낸다. 지난 slot의 알림은 보내지 않는다."""
    store_id, slot = job.payload["storeId"], job.payload["slot"]
    target_date = date.fromisoformat(job.payload["targetDate"])
    async with deps.db.read() as uow:
        forecast_job = await queue.find(uow, queue.forecast_key(store_id, target_date, slot))
        token = await stores.push_token(uow, store_id)
        low_stock = []
        if slot == "NIGHT" and token:
            today = deps.clock.context(store_id).local_date
            store = await stores.require(uow, store_id)
            low_stock = await ordering.find_low_stock(uow, deps, store, today, target_date)
    waiting = forecast_job is None or forecast_job.state in ("READY", "RUNNING")
    if waiting and job.payload.get("waits", 0) < MAX_PUSH_WAITS:
        raise queue.PostponeJob
    push_text = None if waiting or forecast_job.state != "SUCCEEDED" else forecast_job.payload.get("pushText")
    delivered = True
    if push_text and token:
        suffix = texts.low_stock_push_suffix([item.ingredient.name for item in low_stock if not item.ordered])
        delivered = await deps.providers.push.send(token, push_text["title"], push_text["body"] + suffix, "/")
    async with deps.db.write() as uow:
        if not delivered:
            await stores.clear_push_token_if_unchanged(uow, store_id, token)
        await queue.succeed(uow, job)


HANDLERS = {
    "GEOCODE_STORE": stores.geocode_store,
    "PARSE_SALES": sales.parse_import,
    "APPLY_SALES": sales.apply_import,
    "GENERATE_RECIPES": catalog.generate_recipes,
    "SCAN_RECEIPTS": receipts.scan_receipts,
    "FIT_FORECAST_MODEL": forecasting.fit_forecast_model,
    "REFRESH_CONTEXT": forecasting.refresh_context,
    "GENERATE_FORECAST": forecasting.generate_forecast,
    "SEND_PUSH": send_push,
}


class JobRunner:
    """API 프로세스 안에서 도는 작업 루프. 한 번에 작업 하나만 실행한다."""

    def __init__(
        self,
        deps: Container,
        retry_delays: tuple[int, int] = (30, 120),
        postpone_seconds: int = 60,
        poll_seconds: float = 2,
        schedule_seconds: float = 60,
    ):
        self.deps = deps
        self.retry_delays = retry_delays
        self.postpone_seconds = postpone_seconds
        self.poll_seconds = poll_seconds
        self.schedule_seconds = schedule_seconds
        self._stopping = asyncio.Event()

    async def recover(self) -> None:
        """RUNNING으로 남은 작업은 서버가 도중에 꺼진 것이므로 다시 실행 대상으로 돌린다."""
        async with self.deps.db.write() as uow:
            await queue.release_running(uow)

    async def run_once(self) -> bool:
        async with self.deps.db.write() as uow:
            job = await queue.claim_next(uow)
        if job is None:
            return False
        started = time.monotonic()
        state, reason = "SUCCEEDED", None
        try:
            await HANDLERS[job.kind](self.deps, job)
        except queue.PostponeJob:
            state = "READY"
            payload = {**job.payload, "waits": job.payload.get("waits", 0) + 1}
            async with self.deps.db.write() as uow:
                await queue.reschedule(uow, job, self.postpone_seconds, None, job.attempts, payload)
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"[:500]
            attempts = job.attempts + 1
            permanent = isinstance(error, queue.PermanentJobFailure) or attempts >= MAX_ATTEMPTS
            state = "DEAD" if permanent else "READY"
            async with self.deps.db.write() as uow:
                if permanent:
                    await queue.bury(uow, job, reason)
                else:
                    await queue.reschedule(uow, job, self.retry_delays[attempts - 1], reason, attempts, job.payload)
        log = logger.error if state == "DEAD" else logger.info
        log(
            "job jobId=%s kind=%s attempts=%s state=%s ms=%d reason=%s",
            job.id,
            job.kind,
            job.attempts,
            state,
            (time.monotonic() - started) * 1000,
            reason,
        )
        return True

    async def run_until_idle(self, limit: int = 200) -> int:
        executed = 0
        while executed < limit and await self.run_once():
            executed += 1
        return executed

    async def run_forever(self) -> None:
        next_schedule_check = 0.0
        while not self._stopping.is_set():
            try:
                if self.deps.scheduler_enabled and time.monotonic() >= next_schedule_check:
                    next_schedule_check = time.monotonic() + self.schedule_seconds
                    await schedule.register_due_slots(self.deps)
                if await self.run_once():
                    continue
            except Exception:
                logger.exception("job loop iteration failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stopping.set()
