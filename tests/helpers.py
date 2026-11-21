"""테스트가 같이 쓰는 입력 생성기와 DB 조회."""

import json
from datetime import date, timedelta

from sqlalchemy import func, select, text

from app.infrastructure.db.tables import metadata
from app.infrastructure.jobs import queue

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
PUSH_TOKEN = json.dumps({"endpoint": "https://push.example/abc", "keys": {"p256dh": "p256", "auth": "auth"}})


def business_hours(open_time="10:00", close_time="21:00", closed_days=()):
    return [
        {"day": day, "closed": True}
        if day in closed_days
        else {"day": day, "closed": False, "open": open_time, "close": close_time}
        for day in DAYS
    ]


def store_body(**overrides):
    body = {
        "name": "행복분식",
        "category": "BUNSIK",
        "address": {"roadAddress": "서울 마포구 망원로 00"},
        "businessHours": business_hours(),
    }
    return {**body, **overrides}


def sales_csv(
    daily: dict[str, tuple[int, int]], start: date, end: date, header="주문일자,상품명,수량,결제금액"
) -> bytes:
    """daily: 메뉴 이름 → (하루 판매량, 단가). start~end 모든 날짜에 같은 판매량을 적는다."""
    lines = [header]
    day = start
    while day <= end:
        for menu_name, (quantity, price) in daily.items():
            lines.append(f"{day.isoformat()},{menu_name},{quantity},{quantity * price}")
        day += timedelta(days=1)
    return ("\n".join(lines) + "\n").encode("utf-8-sig")


async def count(deps, table_name: str) -> int:
    async with deps.db.read() as uow:
        return (await uow.execute(select(func.count()).select_from(metadata.tables[table_name]))).scalar()


async def row_counts(deps) -> dict[str, int]:
    return {name: await count(deps, name) for name in metadata.tables if name != "background_job"}


async def fetch_all(deps, sql: str, **params):
    async with deps.db.read() as uow:
        return (await uow.execute(text(sql), params)).all()


async def execute(deps, sql: str, **params) -> None:
    async with deps.db.write() as uow:
        await uow.execute(text(sql), params)


async def job_states(deps) -> dict[str, str]:
    rows = await fetch_all(deps, "SELECT dedupe_key, state FROM background_job")
    return {row.dedupe_key: row.state for row in rows}


async def enqueue_context(deps, store_id: str, target_date: date, slot: str) -> None:
    async with deps.db.write() as uow:
        await queue.enqueue_refresh_context(uow, store_id, target_date, slot)


async def upload_sales(api, runner, store_id: str, content: bytes, filename="sales.csv") -> str:
    """판매 파일을 올리고 열 확인·반영·후속 작업까지 끝낸다."""
    accepted = await api.call(
        "createSalesImport", 202, storeId=store_id, files={"file": (filename, content, "text/csv")}
    )
    import_id = accepted.json()["id"]
    await runner.run_until_idle()
    await api.call("confirmSalesImport", 202, storeId=store_id, importId=import_id)
    await runner.run_until_idle()
    return import_id


async def scan_receipts(api, runner, store_id: str, images: list[bytes]) -> str:
    files = [("images", (f"{idx}.jpg", image, "image/jpeg")) for idx, image in enumerate(images)]
    accepted = await api.call("createReceiptScan", 202, storeId=store_id, files=files)
    await runner.run_until_idle()
    return accepted.json()["id"]


async def release_postponed_jobs(deps) -> None:
    """미뤄 둔 작업의 대기 시간이 지난 것으로 만든다."""
    await execute(deps, "UPDATE background_job SET available_at = now() WHERE state = 'READY'")
