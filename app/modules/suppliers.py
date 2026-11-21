from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, insert, select, update

from app.bootstrap import Container
from app.infrastructure.db.tables import receipt_line, supplier
from app.infrastructure.db.uow import UoW
from app.modules import stores
from app.shared.policy import Policy
from app.shared.types import InvalidRequest, NotFound, day_code, new_id, sort_days


@dataclass(frozen=True)
class SupplierSnapshot:
    id: str
    store_id: str
    name: str
    phone: str | None
    phone_confirmed: bool
    phone_read_on: date | None
    order_days: tuple[str, ...]
    order_days_status: str

    def orders_on(self, day: str) -> bool:
        return self.order_days_status in ("ESTIMATED", "CONFIRMED") and day in self.order_days


@dataclass(frozen=True)
class SupplierDraft:
    source_key: str
    name: str
    phone: str | None
    phone_read_on: date | None


def _snapshot(row) -> SupplierSnapshot:
    return SupplierSnapshot(
        id=row.id,
        store_id=row.store_id,
        name=row.name,
        phone=row.phone,
        phone_confirmed=row.phone_confirmed,
        phone_read_on=row.phone_read_on,
        order_days=tuple(row.order_days),
        order_days_status=row.order_days_status,
    )


async def list_all(uow: UoW, store_id: str) -> list[SupplierSnapshot]:
    rows = (await uow.execute(select(supplier).where(supplier.c.store_id == store_id).order_by(supplier.c.id))).all()
    return [_snapshot(row) for row in rows]


async def resolve(uow: UoW, store_id: str, drafts: list[SupplierDraft]) -> dict[str, SupplierSnapshot]:
    """이름으로 upsert한다. 확인된 전화번호가 있으면 영수증 번호로 덮지 않는다."""
    resolved: dict[str, str] = {}
    for draft in drafts:
        by_name = {item.name: item for item in await list_all(uow, store_id)}
        current = by_name.get(draft.name)
        if current is None:
            supplier_id = new_id("sup")
            await uow.execute(
                insert(supplier).values(
                    id=supplier_id,
                    store_id=store_id,
                    name=draft.name,
                    phone=draft.phone,
                    phone_read_on=draft.phone_read_on if draft.phone else None,
                )
            )
        else:
            supplier_id = current.id
            if draft.phone and not current.phone_confirmed:
                await uow.execute(
                    update(supplier)
                    .where(supplier.c.id == supplier_id)
                    .values(phone=draft.phone, phone_read_on=draft.phone_read_on)
                )
        resolved[draft.source_key] = supplier_id
    by_id = {item.id: item for item in await list_all(uow, store_id)}
    return {source_key: by_id[supplier_id] for source_key, supplier_id in resolved.items()}


async def estimate_order_days(uow: UoW, supplier_id: str, receipt_dates: list[date], policy: Policy) -> None:
    """확인되지 않은 거래처만 영수증 날짜의 요일로 추정한다."""
    distinct_dates = sorted(set(receipt_dates))
    if len(distinct_dates) >= policy.order_days_min_receipt_dates:
        values = {"order_days": sort_days(day_code(day) for day in distinct_dates), "order_days_status": "ESTIMATED"}
    else:
        values = {"order_days": [], "order_days_status": "UNKNOWN"}
    await uow.execute(
        update(supplier)
        .where(supplier.c.id == supplier_id, supplier.c.order_days_status != "CONFIRMED")
        .values(**values)
    )


async def confirm_phone(uow: UoW, supplier_id: str, phone: str) -> None:
    await uow.execute(
        update(supplier)
        .where(supplier.c.id == supplier_id)
        .values(phone=phone, phone_confirmed=True, phone_read_on=None)
    )


async def list_with_receipt_counts(deps: Container, store_id: str) -> list[tuple[SupplierSnapshot, int]]:
    async with deps.db.read() as uow:
        await stores.require(uow, store_id)
        counts = dict(
            (
                await uow.execute(
                    select(receipt_line.c.supplier_id, func.count(func.distinct(receipt_line.c.purchased_on)))
                    .join(supplier, supplier.c.id == receipt_line.c.supplier_id)
                    .where(supplier.c.store_id == store_id)
                    .group_by(receipt_line.c.supplier_id)
                )
            ).all()
        )
        return [(item, counts.get(item.id, 0)) for item in await list_all(uow, store_id)]


async def save_order_days(deps: Container, store_id: str, items: list[tuple[str, list[str]]]) -> None:
    supplier_ids = [supplier_id for supplier_id, _ in items]
    if len(set(supplier_ids)) != len(supplier_ids):
        raise InvalidRequest("supplierId repeats")
    if any(len(set(days)) != len(days) for _, days in items):
        raise InvalidRequest("orderDays repeats a day")
    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        known = {item.id for item in await list_all(uow, store_id)}
        if any(supplier_id not in known for supplier_id in supplier_ids):
            raise NotFound("supplier")
        for supplier_id, days in items:
            await uow.execute(
                update(supplier)
                .where(supplier.c.id == supplier_id)
                .values(order_days=sort_days(days), order_days_status="CONFIRMED")
            )
