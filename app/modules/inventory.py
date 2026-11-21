from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.infrastructure.db.uow import UoW
from app.modules import catalog, receipts, sales, stores


@dataclass(frozen=True)
class InventorySnapshot:
    """asOf 영업이 끝났을 때 남는 양(재료 단위). 확정한 입고가 없으면 stock이 None이다."""

    ingredient_id: str
    as_of: date
    stock: Decimal | None


def evaluate(
    catalog_snapshot: catalog.CatalogSnapshot,
    sales_snapshot: sales.SalesSnapshot,
    lots: list[receipts.ReceiptLot],
    as_of: date,
) -> dict[str, InventorySnapshot]:
    received: dict[str, dict[date, Decimal]] = {}
    for lot in lots:
        if lot.purchased_on <= as_of:
            by_day = received.setdefault(lot.ingredient_id, {})
            by_day[lot.purchased_on] = by_day.get(lot.purchased_on, Decimal(0)) + lot.quantity
    result = {}
    for ingredient in catalog_snapshot.ingredients:
        recipe_uses = [line for line in catalog_snapshot.recipe_lines if line.ingredient_id == ingredient.id]
        stock = _simulate(received.get(ingredient.id), recipe_uses, sales_snapshot, as_of)
        result[ingredient.id] = InventorySnapshot(ingredient_id=ingredient.id, as_of=as_of, stock=stock)
    return result


def _simulate(received_by_day, recipe_uses, sales_snapshot: sales.SalesSnapshot, as_of: date) -> Decimal | None:
    if not received_by_day:
        return None
    stock = Decimal(0)
    day = min(received_by_day)
    while day <= as_of:
        stock += received_by_day.get(day, Decimal(0))
        for line in recipe_uses:
            stock -= (sales_snapshot.sold(line.menu_id, day) or Decimal(0)) * line.quantity
        stock = max(Decimal(0), stock)
        day += timedelta(days=1)
    return stock


async def end_of(uow: UoW, store: stores.StoreSnapshot, as_of: date) -> dict[str, InventorySnapshot]:
    """재고를 읽는 모든 곳이 이 함수만 쓴다. asOf 영업 종료 시점의 재료별 재고."""
    catalog_snapshot = await catalog.snapshot(uow, store.id)
    lots = await receipts.lots(uow, store.id, as_of)
    if not lots:
        return evaluate(catalog_snapshot, sales.SalesSnapshot((), ()), [], as_of)
    sales_snapshot = await sales.read(uow, store.id, min(lot.purchased_on for lot in lots), as_of)
    return evaluate(catalog_snapshot, sales_snapshot, lots, as_of)
