from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.bootstrap import Container
from app.infrastructure.db.uow import UoW
from app.modules import catalog, receipts, sales, stores
from app.shared.types import InvalidRequest


@dataclass(frozen=True)
class InventorySnapshot:
    """asOf 영업이 끝났을 때 남는 양(재료 단위). 입고가 없으면 stock이 None이고, 음수면 입고 누락을 뜻한다."""

    ingredient_id: str
    as_of: date
    stock: Decimal | None
    started_on: date | None = None

    @property
    def missing_purchase(self) -> bool:
        return self.stock is not None and self.stock < 0

    @property
    def stock_for_planning(self) -> Decimal:
        return max(Decimal(0), self.stock or Decimal(0))


def ledger(lots: list[receipts.ReceiptLot], as_of: date) -> list[receipts.ReceiptLot]:
    ordered = sorted(
        (lot for lot in lots if lot.purchased_on <= as_of), key=lambda lot: (lot.purchased_on, lot.receipt_line_id)
    )
    resets = [index for index, lot in enumerate(ordered) if lot.is_stock_reset]
    return ordered[resets[-1] :] if resets else ordered


def evaluate(
    catalog_snapshot: catalog.CatalogSnapshot,
    sales_snapshot: sales.SalesSnapshot,
    lots: list[receipts.ReceiptLot],
    as_of: date,
) -> dict[str, InventorySnapshot]:
    lots_by_ingredient: dict[str, list[receipts.ReceiptLot]] = {}
    for lot in lots:
        lots_by_ingredient.setdefault(lot.ingredient_id, []).append(lot)
    result = {}
    for ingredient in catalog_snapshot.ingredients:
        recipe_uses = [line for line in catalog_snapshot.recipe_lines if line.ingredient_id == ingredient.id]
        kept = ledger(lots_by_ingredient.get(ingredient.id, []), as_of)
        if not kept:
            result[ingredient.id] = InventorySnapshot(ingredient.id, as_of, None)
            continue
        started_on = kept[0].purchased_on
        received = sum((lot.quantity for lot in kept), Decimal(0))
        stock = received - _usage(recipe_uses, sales_snapshot, started_on, as_of)
        result[ingredient.id] = InventorySnapshot(ingredient.id, as_of, stock, started_on)
    return result


def _usage(recipe_uses, sales_snapshot: sales.SalesSnapshot, start: date, end: date) -> Decimal:
    used = Decimal(0)
    day = start
    while day <= end:
        for line in recipe_uses:
            used += (sales_snapshot.sold(line.menu_id, day) or Decimal(0)) * line.quantity
        day += timedelta(days=1)
    return used


async def end_of(uow: UoW, store: stores.StoreSnapshot, as_of: date) -> dict[str, InventorySnapshot]:
    """재고를 읽는 모든 곳이 이 함수만 쓴다. asOf 영업 종료 시점의 재료별 재고."""
    catalog_snapshot = await catalog.snapshot(uow, store.id)
    lots = await receipts.lots(uow, store.id, as_of)
    if not lots:
        return evaluate(catalog_snapshot, sales.SalesSnapshot((), ()), [], as_of)
    sales_snapshot = await sales.read(uow, store.id, min(lot.purchased_on for lot in lots), as_of)
    return evaluate(catalog_snapshot, sales_snapshot, lots, as_of)


async def get_stock(
    deps: Container, store_id: str, as_of: date | None
) -> tuple[date, list[tuple[catalog.IngredientSnapshot, InventorySnapshot]]]:
    ctx = deps.clock.context(store_id)
    as_of = as_of or ctx.local_date
    if as_of > ctx.local_date:
        raise InvalidRequest("asOf cannot be in the future")
    async with deps.db.read() as uow:
        store = await stores.require(uow, store_id)
        catalog_snapshot = await catalog.snapshot(uow, store_id)
        stock_by_ingredient = await end_of(uow, store, as_of)
        return as_of, [(item, stock_by_ingredient[item.id]) for item in catalog_snapshot.ingredients]
