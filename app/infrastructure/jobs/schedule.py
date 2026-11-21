"""정해진 시각의 작업 등록. dedupe_key가 같으면 다시 등록되지 않으므로 여러 번 확인해도 한 번만 실행된다."""

import asyncio
import os
import sys
from datetime import date, time, timedelta

from app.bootstrap import Container
from app.infrastructure.db.uow import Database
from app.infrastructure.jobs import queue
from app.modules import catalog, stores
from app.shared.types import KST, hours_for

# (시작, 끝, 작업, slot, 대상일 = 오늘 + n일). 끝이 지난 slot은 나중에 등록하지 않는다.
SLOTS = (
    (time(19, 50), time(23, 59, 59), "CONTEXT", "NIGHT", 1),
    (time(20, 0), time(20, 30), "PUSH", "NIGHT", 1),
    (time(7, 20), time(19, 50), "CONTEXT", "MORNING", 0),
    (time(7, 30), time(8, 0), "PUSH", "MORNING", 0),
)


async def register_due_slots(deps: Container) -> None:
    local_now = deps.clock.now().astimezone(KST)
    due = [slot for slot in SLOTS if slot[0] <= local_now.time() <= slot[1]]
    if not due:
        return
    async with deps.db.write() as uow:
        for store, has_push_token in await stores.list_for_schedule(uow):
            # ponytail: 가게마다 메뉴를 따로 읽는다. 가게가 수백 곳을 넘으면 한 번의 집계 쿼리로 바꾼다.
            has_menus = bool((await catalog.snapshot(uow, store.id)).menus)
            for _, _, work, slot, day_offset in due:
                target_date = local_now.date() + timedelta(days=day_offset)
                if work == "CONTEXT" and store.latitude is not None and has_menus:
                    await queue.enqueue_refresh_context(uow, store.id, target_date, slot)
                if work == "PUSH" and has_push_token and not hours_for(store.business_hours, target_date).closed:
                    await queue.enqueue_push(uow, store.id, target_date, slot)


async def register_demo_slot(database_url: str, store_id: str, target_date: date, slot: str) -> None:
    """시연용 1회 등록. 운영과 같은 handler와 dedupe_key를 쓴다."""
    database = Database(database_url)
    try:
        async with database.write() as uow:
            await stores.require(uow, store_id)
            await queue.enqueue_refresh_context(uow, store_id, target_date, slot)
    finally:
        await database.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[3] not in ("NIGHT", "MORNING"):
        sys.exit("usage: python -m app.infrastructure.jobs.schedule <storeId> <YYYY-MM-DD> <NIGHT|MORNING>")
    asyncio.run(
        register_demo_slot(os.environ["DATABASE_URL"], sys.argv[1], date.fromisoformat(sys.argv[2]), sys.argv[3])
    )
