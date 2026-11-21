import asyncio
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import cached_property

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.bootstrap import Container
from app.infrastructure.db.tables import daily_sales, menu, sales_import
from app.infrastructure.db.uow import UoW
from app.infrastructure.files import tabular
from app.infrastructure.jobs import queue
from app.infrastructure.providers.ports import ProviderInvalidPayload
from app.modules import catalog, stores
from app.shared.types import KST, InvalidRequest, NotFound, new_id, normalize_name, round_half_up

SALES_EXTENSIONS = {"xlsx", "xls", "csv"}
SAMPLE_ROW_COUNT = 20
PREVIEW_ROW_COUNT = 5
DATE_PATTERN = re.compile(r"^\s*(\d{4})[-/.]\s?(\d{1,2})[-/.]\s?(\d{1,2})")


@dataclass(frozen=True)
class SalesRecord:
    sold_on: date
    menu_name: str
    quantity: Decimal
    amount: Decimal | None


@dataclass(frozen=True)
class SalesSnapshot:
    rows: tuple[tuple[str, date, Decimal], ...]
    covered_ranges: tuple[tuple[date, date], ...]

    @cached_property
    def _cells(self) -> dict[tuple[str, date], Decimal]:
        return {(menu_id, sold_on): quantity for menu_id, sold_on, quantity in self.rows}

    def has_record(self, day: date) -> bool:
        return any(start <= day <= end for start, end in self.covered_ranges)

    def sold(self, menu_id: str, day: date) -> Decimal | None:
        """판매 기록이 있는 날이면 판매량(행이 없으면 0), 기록이 없는 날이면 None."""
        if not self.has_record(day):
            return None
        return self._cells.get((menu_id, day), Decimal(0))

    def covered_day_count(self) -> int:
        days = set()
        for start, end in self.covered_ranges:
            days.update(start + timedelta(days=offset) for offset in range((end - start).days + 1))
        return len(days)


@dataclass(frozen=True)
class SalesImportView:
    id: str
    status: str
    pos_system: str | None
    detection: dict | None
    period_from: date | None
    period_to: date | None
    record_count: int | None


async def read(uow: UoW, store_id: str, start: date, through: date) -> SalesSnapshot:
    rows = (
        await uow.execute(
            select(daily_sales.c.menu_id, daily_sales.c.sold_on, daily_sales.c.quantity)
            .join(menu, menu.c.id == daily_sales.c.menu_id)
            .where(menu.c.store_id == store_id, daily_sales.c.sold_on.between(start, through))
        )
    ).all()
    ranges = (
        await uow.execute(
            select(sales_import.c.period_from, sales_import.c.period_to).where(
                sales_import.c.store_id == store_id, sales_import.c.status == "COMPLETED"
            )
        )
    ).all()
    return SalesSnapshot(
        rows=tuple((row.menu_id, row.sold_on, row.quantity) for row in rows),
        covered_ranges=tuple((row.period_from, row.period_to) for row in ranges),
    )


def _parse_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(KST).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    matched = DATE_PATTERN.match(value)
    if not matched:
        return None
    try:
        return date(int(matched[1]), int(matched[2]), int(matched[3]))
    except ValueError:
        return None


def _parse_number(value) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
    else:
        try:
            number = Decimal(str(value).replace(",", "").strip())
        except InvalidOperation:
            return None
    return number if number.is_finite() else None


def header_of(rows: list[list]) -> tuple[list[str], list[list]]:
    for idx, row in enumerate(rows):
        if any(cell not in (None, "") for cell in row):
            return ["" if cell is None else str(cell).strip() for cell in row], rows[idx + 1 :]
    return [], []


def parse_sales_rows(rows: list[list], mapping: dict) -> list[SalesRecord]:
    """열 매핑으로 판매 행을 읽는다. 날짜로 읽히지 않는 행(합계·소계·빈 줄)은 건너뛴다."""
    header, body = header_of(rows)
    try:
        date_idx = header.index(mapping["date"])
        menu_idx = header.index(mapping["menuName"])
        quantity_idx = header.index(mapping["quantity"])
        amount_idx = header.index(mapping["amount"]) if mapping.get("amount") else None
    except ValueError as error:
        raise ProviderInvalidPayload("column mapping names a column that is not in the header") from error

    def cell(row: list, idx: int):
        return row[idx] if idx < len(row) else None

    records = []
    for row in body:
        sold_on = _parse_date(cell(row, date_idx))
        quantity = _parse_number(cell(row, quantity_idx))
        menu_name = normalize_name(str(cell(row, menu_idx) or ""))
        if sold_on is None or quantity is None or not menu_name:
            continue
        amount = _parse_number(cell(row, amount_idx)) if amount_idx is not None else None
        records.append(SalesRecord(sold_on, menu_name, quantity, amount))
    return records


def _extension(filename: str | None) -> str:
    extension = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if extension not in SALES_EXTENSIONS:
        raise InvalidRequest("sales file must be xlsx, xls or csv")
    return extension


async def create_import(deps: Container, store_id: str, upload) -> str:
    async with deps.db.read() as uow:
        await stores.require(uow, store_id)
    extension = _extension(upload.filename)
    file_ref = await deps.files.put(
        store_id,
        "sales",
        extension,
        upload,
        deps.policy.sales_file_max_bytes,
        "application/octet-stream",
        lambda head: tabular.has_valid_signature(extension, head),
    )
    import_id = new_id("imp")
    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        await uow.execute(
            insert(sales_import).values(
                id=import_id, store_id=store_id, status="RECEIVED", file_path=file_ref.key, created_at=deps.clock.now()
            )
        )
        await queue.enqueue(uow, "PARSE_SALES", {"storeId": store_id, "importId": import_id}, f"parse:{import_id}")
    return import_id


async def _require_import(uow: UoW, store_id: str, import_id: str, for_update: bool = False):
    statement = select(sales_import).where(sales_import.c.id == import_id, sales_import.c.store_id == store_id)
    row = (await uow.execute(statement.with_for_update() if for_update else statement)).first()
    if row is None:
        raise NotFound("sales import")
    return row


async def get_import(deps: Container, store_id: str, import_id: str) -> SalesImportView:
    async with deps.db.read() as uow:
        await stores.require(uow, store_id)
        row = await _require_import(uow, store_id, import_id)
        await queue.fail_if_dead(uow, f"parse:{import_id}", f"apply:{import_id}")
        return SalesImportView(
            id=row.id,
            status=row.status,
            pos_system=row.pos_system,
            detection=row.detection,
            period_from=row.period_from,
            period_to=row.period_to,
            record_count=row.record_count,
        )


async def confirm_import(deps: Container, store_id: str, import_id: str) -> None:
    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        row = await _require_import(uow, store_id, import_id)
        await queue.fail_if_dead(uow, f"parse:{import_id}", f"apply:{import_id}")
        if row.status in ("RECEIVED", "PARSING"):
            raise InvalidRequest("sales import is not ready to confirm")
        if row.status == "NEEDS_CONFIRMATION":
            await uow.execute(update(sales_import).where(sales_import.c.id == import_id).values(status="APPLYING"))
            await queue.enqueue(uow, "APPLY_SALES", {"storeId": store_id, "importId": import_id}, f"apply:{import_id}")


async def _read_file_rows(deps: Container, file_key: str) -> list[list]:
    path = deps.files.path(file_key)
    if not path.is_file():
        raise queue.PermanentJobFailure("sales file is missing")
    try:
        return await asyncio.to_thread(tabular.read_rows, path, deps.policy.sales_max_rows)
    except tabular.TooManyRows as error:
        raise queue.PermanentJobFailure("sales file has too many rows") from error
    except Exception as error:
        raise queue.PermanentJobFailure(f"sales file cannot be read: {type(error).__name__}") from error


async def parse_import(deps: Container, job: queue.Job) -> None:
    store_id, import_id = job.payload["storeId"], job.payload["importId"]
    async with deps.db.write() as uow:
        try:
            await stores.lock(uow, store_id)
            row = await _require_import(uow, store_id, import_id)
        except NotFound as error:
            raise queue.PermanentJobFailure("sales import not found") from error
        if row.status not in ("RECEIVED", "PARSING"):
            await queue.succeed(uow, job)
            return
        await uow.execute(update(sales_import).where(sales_import.c.id == import_id).values(status="PARSING"))

    rows = await _read_file_rows(deps, row.file_path)
    header, body = header_of(rows)
    if not any(header):
        raise queue.PermanentJobFailure("sales file has no header row")
    sample = [["" if cell is None else str(cell) for cell in sample_row] for sample_row in body[:SAMPLE_ROW_COUNT]]
    detection = await deps.providers.columns.detect_columns(header, sample)
    mapping = {
        "date": detection.date,
        "menuName": detection.menu_name,
        "quantity": detection.quantity,
        "amount": detection.amount,
    }
    records = parse_sales_rows(rows, mapping)
    if not records:
        raise queue.PermanentJobFailure("sales file has no readable sales row")

    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        await uow.execute(
            update(sales_import)
            .where(sales_import.c.id == import_id, sales_import.c.status == "PARSING")
            .values(
                status="NEEDS_CONFIRMATION",
                pos_system=detection.pos_system,
                detection={
                    "columnMapping": mapping,
                    "menuCount": len({record.menu_name for record in records}),
                    "preview": [
                        {
                            "date": record.sold_on.isoformat(),
                            "menuName": record.menu_name,
                            "quantity": float(record.quantity),
                        }
                        for record in records[:PREVIEW_ROW_COUNT]
                    ],
                },
                period_from=min(record.sold_on for record in records),
                period_to=max(record.sold_on for record in records),
                record_count=len(records),
            )
        )
        await queue.succeed(uow, job)


def latest_prices(records: list[SalesRecord]) -> dict[str, int]:
    """메뉴별로 가장 최근 날짜의 금액 합 ÷ 수량 합. 수량 합이 0 이하면 건너뛴다."""
    latest_day: dict[str, date] = {}
    for record in records:
        if record.amount is not None:
            latest_day[record.menu_name] = max(latest_day.get(record.menu_name, record.sold_on), record.sold_on)
    prices = {}
    for menu_name, day in latest_day.items():
        same_day = [
            record
            for record in records
            if record.menu_name == menu_name and record.sold_on == day and record.amount is not None
        ]
        quantity = sum((record.quantity for record in same_day), Decimal(0))
        amount = sum((record.amount for record in same_day), Decimal(0))
        if quantity > 0 and amount >= 0:
            prices[menu_name] = round_half_up(amount / quantity)
    return prices


async def apply_import(deps: Container, job: queue.Job) -> None:
    store_id, import_id = job.payload["storeId"], job.payload["importId"]
    async with deps.db.read() as uow:
        try:
            row = await _require_import(uow, store_id, import_id)
        except NotFound as error:
            raise queue.PermanentJobFailure("sales import not found") from error
    records: list[SalesRecord] = []
    if row.status == "APPLYING":
        records = parse_sales_rows(await _read_file_rows(deps, row.file_path), row.detection["columnMapping"])

    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        current = await _require_import(uow, store_id, import_id)
        if current.status != "APPLYING":
            await queue.succeed(uow, job)
            return
        prices = latest_prices(records)
        menu_names = sorted({record.menu_name for record in records})
        menus = await catalog.upsert_imported_menus(
            uow,
            store_id,
            [catalog.MenuImportDraft(name, deps.policy.menu_unit_for(name), prices.get(name)) for name in menu_names],
        )
        totals: dict[tuple[str, date], Decimal] = {}
        for record in records:
            cell = (menus[record.menu_name].id, record.sold_on)
            totals[cell] = totals.get(cell, Decimal(0)) + record.quantity
        if totals:
            upsert = pg_insert(daily_sales)
            await uow.execute(
                upsert.on_conflict_do_update(
                    index_elements=["menu_id", "sold_on"], set_={"quantity": upsert.excluded.quantity}
                ),
                [
                    {"menu_id": menu_id, "sold_on": sold_on, "quantity": max(quantity, Decimal(0))}
                    for (menu_id, sold_on), quantity in totals.items()
                ],
            )
        await uow.execute(update(sales_import).where(sales_import.c.id == import_id).values(status="COMPLETED"))
        if await catalog.menus_without_recipe(uow, store_id):
            await stores.set_generation_status(uow, store_id, "GENERATING")
            await queue.enqueue(
                uow, "GENERATE_RECIPES", {"storeId": store_id, "importId": import_id}, f"recipe:{import_id}"
            )
        else:
            await stores.set_generation_status(uow, store_id, "READY")
        today = deps.clock.context(store_id).local_date
        for target_date in (today, today + timedelta(days=1)):
            await queue.enqueue_forecast(uow, store_id, target_date, "DATA_CHANGED", job.id)
        await queue.succeed(uow, job)
