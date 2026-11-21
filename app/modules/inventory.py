from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.infrastructure.db.uow import UoW
from app.modules import catalog, forecasting, receipts, sales, stores
from app.shared.types import DayHours, hours_for


@dataclass(frozen=True)
class InventorySnapshot:
    """asOf 영업이 끝났을 때 남는 양(사용 단위). 계산할 수 없으면 stock이 None이다."""

    ingredient_id: str
    as_of: date
    stock: Decimal | None


def evaluate(
    catalog_snapshot: catalog.CatalogSnapshot,
    sales_snapshot: sales.SalesSnapshot,
    usage_rows: list[forecasting.UsageRow],
    lots: list[receipts.ReceiptLot],
    business_hours: tuple[DayHours, ...],
    as_of: date,
) -> dict[str, InventorySnapshot]:
    usage_by_cell = {(row.menu_id, row.target_date): row for row in usage_rows}
    lots_by_ingredient: dict[str, list[receipts.ReceiptLot]] = {}
    for lot in lots:
        if lot.purchased_on <= as_of:
            lots_by_ingredient.setdefault(lot.ingredient_id, []).append(lot)

    def menu_quantity(menu_id: str, day: date) -> Decimal | None:
        if hours_for(business_hours, day).closed:
            return Decimal(0)
        sold = sales_snapshot.sold(menu_id, day)
        if sold is not None:
            return sold
        row = usage_by_cell.get((menu_id, day))
        if row is not None:
            return row.produced if row.produced is not None else row.planned
        sampled = forecasting.baseline_for(sales_snapshot, menu_id, day)
        return sampled[0] if sampled else None

    result = {}
    for ingredient in catalog_snapshot.ingredients:
        own_lots = lots_by_ingredient.get(ingredient.id, [])
        recipe_uses = [
            line for line in catalog_snapshot.recipe_lines if line.ingredient_id == ingredient.id and line.quantity > 0
        ]
        stock = _simulate(ingredient, own_lots, recipe_uses, menu_quantity, as_of)
        result[ingredient.id] = InventorySnapshot(ingredient_id=ingredient.id, as_of=as_of, stock=stock)
    return result


def _simulate(ingredient, own_lots, recipe_uses, menu_quantity, as_of: date) -> Decimal | None:
    if not ingredient.convertible or not own_lots:
        return None
    received_by_day: dict[date, Decimal] = {}
    for lot in own_lots:
        received = lot.purchase_quantity * ingredient.content_quantity
        received_by_day[lot.purchased_on] = received_by_day.get(lot.purchased_on, Decimal(0)) + received
    stock = Decimal(0)
    day = min(received_by_day)
    while day <= as_of:
        stock += received_by_day.get(day, Decimal(0))
        for line in recipe_uses:
            quantity = menu_quantity(line.menu_id, day)
            if quantity is None:
                return None
            stock -= quantity * line.quantity
        stock = max(Decimal(0), stock)
        day += timedelta(days=1)
    return stock


async def end_of(uow: UoW, store: stores.StoreSnapshot, as_of: date) -> dict[str, InventorySnapshot]:
    """재고를 읽는 모든 곳이 이 함수만 쓴다. asOf 영업 종료 시점의 재료별 재고."""
    catalog_snapshot = await catalog.snapshot(uow, store.id)
    lots = await receipts.lots(uow, store.id, as_of)
    if not lots:
        return evaluate(catalog_snapshot, sales.SalesSnapshot((), ()), [], [], store.business_hours, as_of)
    start = min(lot.purchased_on for lot in lots)
    sales_snapshot = await sales.read(uow, store.id, start - timedelta(days=forecasting.SALES_LOOKBACK_DAYS), as_of)
    usage_rows = await forecasting.usage(uow, store.id, start, as_of)
    return evaluate(catalog_snapshot, sales_snapshot, usage_rows, lots, store.business_hours, as_of)
