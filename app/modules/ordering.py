from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import insert, select, update

from app.bootstrap import Container
from app.infrastructure.db.tables import ingredient, purchase_order, purchase_order_line, supplier
from app.infrastructure.db.uow import UoW
from app.modules import catalog, forecasting, inventory, receipts, stores, suppliers
from app.shared import texts
from app.shared.types import (
    KST,
    InvalidRequest,
    NotFound,
    StoreContext,
    business_window,
    ceil_int,
    day_code,
    hours_for,
    new_id,
    round_half_up,
)

LOW_STOCK_SEARCH_DAYS = 14


@dataclass(frozen=True)
class OrderPlanningLine:
    ingredient: catalog.IngredientSnapshot
    supplier: suppliers.SupplierSnapshot | None
    quantity: Decimal
    unit: str
    stock: Decimal | None
    price: Decimal | None
    event_diff: Decimal
    product_name: str


@dataclass(frozen=True)
class OrderPlanSnapshot:
    target_date: date
    lines: tuple[OrderPlanningLine, ...]
    events: tuple[forecasting.NearbyEvent, ...]
    usual_total: int | None
    total: int | None

    @property
    def supplier_count(self) -> int:
        return len({line.supplier.id for line in self.lines if line.supplier is not None})


@dataclass(frozen=True)
class PurchaseOrderSnapshot:
    id: str
    target_date: date
    supplier: suppliers.SupplierSnapshot
    message_text: str
    total_amount: int | None


@dataclass(frozen=True)
class LowStockItem:
    ingredient: catalog.IngredientSnapshot
    stock: Decimal
    expected_usage: Decimal
    ordered: bool


@dataclass(frozen=True)
class PurchaseOrderInput:
    target_date: date
    suppliers: tuple[tuple[str, tuple[tuple[str, Decimal], ...]], ...]


def money_total(lines: list[tuple[Decimal, Decimal | None]]) -> int | None:
    """단가를 아는 줄만 더한다. 단가를 아는 줄이 하나도 없으면 None."""
    known = [round_half_up(quantity * price) for quantity, price in lines if price is not None]
    return sum(known) if known else None


def plan(
    catalog_snapshot: catalog.CatalogSnapshot,
    forecasts: list[forecasting.ForecastSnapshot],
    stock_by_ingredient: dict[str, inventory.InventorySnapshot],
    latest_purchase: dict[str, receipts.LatestPurchaseFact],
    price_by_ingredient: dict[str, Decimal | None],
    supplier_by_id: dict[str, suppliers.SupplierSnapshot],
    events: tuple[forecasting.NearbyEvent, ...],
    order_day: str,
    target_date: date,
) -> OrderPlanSnapshot:
    orderable = [
        item
        for item in catalog_snapshot.ingredients
        if item.id not in latest_purchase or supplier_by_id[latest_purchase[item.id].supplier_id].orders_on(order_day)
    ]

    def need(planned_by_menu: dict[str, Decimal], ingredient_id: str) -> Decimal:
        return sum(
            (
                planned_by_menu[line.menu_id] * line.quantity
                for line in catalog_snapshot.recipe_lines
                if line.ingredient_id == ingredient_id and line.menu_id in planned_by_menu
            ),
            Decimal(0),
        )

    def shortage(planned_by_menu: dict[str, Decimal], item: catalog.IngredientSnapshot) -> int:
        stock = stock_by_ingredient[item.id].stock_for_planning
        return ceil_int(max(Decimal(0), need(planned_by_menu, item.id) - stock))

    planned = {item.menu.id: item.planned for item in forecasts}
    planned_without_event = {item.menu.id: item.planned_without_event for item in forecasts}
    usual = {item.menu.id: Decimal(ceil_int(item.baseline)) for item in forecasts}

    lines, usual_lines = [], []
    for item in orderable:
        fact = latest_purchase.get(item.id)
        price = price_by_ingredient.get(item.id)
        quantity = shortage(planned, item)
        usual_lines.append((Decimal(shortage(usual, item)), price))
        if quantity == 0:
            continue
        lines.append(
            OrderPlanningLine(
                ingredient=item,
                supplier=supplier_by_id[fact.supplier_id] if fact else None,
                quantity=Decimal(quantity),
                unit=item.unit,
                stock=stock_by_ingredient[item.id].stock,
                price=price,
                event_diff=Decimal(quantity - shortage(planned_without_event, item)),
                product_name=fact.product_name if fact else item.name,
            )
        )
    lines.sort(key=lambda line: (line.supplier is None, line.supplier.id if line.supplier else "", line.ingredient.id))
    return OrderPlanSnapshot(
        target_date=target_date,
        lines=tuple(lines),
        events=events,
        usual_total=money_total([line for line in usual_lines if line[0] > 0]),
        total=money_total([(line.quantity, line.price) for line in lines]),
    )


async def plan_order(uow: UoW, deps: Container, ctx: StoreContext, target_date: date) -> OrderPlanSnapshot:
    """발주 계획의 유일한 진입점. 홈·준비량 조정·발주 상세가 모두 이것을 쓴다."""
    store = await stores.require(uow, ctx.store_id)
    window = business_window(store.business_hours, target_date)
    if window is None:
        return OrderPlanSnapshot(target_date, (), (), None, None)
    forecast_context = await forecasting.context(uow, store, target_date, deps.policy)
    factor_events = forecasting.events_during(forecast_context.events, window) if deps.policy.event_factor != 1 else ()
    return plan(
        await catalog.snapshot(uow, store.id),
        await forecasting.read(uow, store.id, target_date),
        await inventory.end_of(uow, store, ctx.local_date),
        await receipts.latest(uow, store.id),
        await receipts.latest_prices(uow, store.id),
        {item.id: item for item in await suppliers.list_all(uow, store.id)},
        factor_events,
        day_code(ctx.local_date),
        target_date,
    )


async def find_low_stock(
    uow: UoW, deps: Container, store: stores.StoreSnapshot, today: date, target_date: date
) -> list[LowStockItem]:
    days = [
        day
        for offset in range(LOW_STOCK_SEARCH_DAYS)
        if not hours_for(store.business_hours, day := target_date + timedelta(days=offset)).closed
    ][: deps.policy.low_stock_days]
    quantities = await forecasting.expected_quantities(uow, store, days, deps.policy)
    catalog_snapshot = await catalog.snapshot(uow, store.id)
    stock_by_ingredient = await inventory.end_of(uow, store, today)
    latest_purchase = await receipts.latest(uow, store.id)
    sent_rows = (
        await uow.execute(
            select(purchase_order_line.c.ingredient_id, purchase_order.c.sent_at)
            .join(purchase_order, purchase_order.c.id == purchase_order_line.c.purchase_order_id)
            .join(supplier, supplier.c.id == purchase_order.c.supplier_id)
            .where(supplier.c.store_id == store.id, purchase_order.c.sent_at.is_not(None))
        )
    ).all()
    last_sent: dict[str, date] = {}
    for row in sent_rows:
        sent_on = row.sent_at.astimezone(KST).date()
        last_sent[row.ingredient_id] = max(last_sent.get(row.ingredient_id, sent_on), sent_on)

    items = []
    for item in catalog_snapshot.ingredients:
        stock = stock_by_ingredient[item.id].stock
        expected = sum(
            (
                quantities[day].get(line.menu_id, Decimal(0)) * line.quantity
                for day in days
                for line in catalog_snapshot.recipe_lines
                if line.ingredient_id == item.id
            ),
            Decimal(0),
        )
        if stock is None or stock >= expected:
            continue
        ordered = item.id in last_sent and last_sent[item.id] > latest_purchase[item.id].purchased_on
        items.append(LowStockItem(item, stock, expected, ordered))
    return sorted(items, key=lambda low: (low.stock / low.expected_usage, low.ingredient.id))


def require_today_or_tomorrow(ctx: StoreContext, target_date: date) -> None:
    if target_date not in (ctx.local_date, ctx.local_date + timedelta(days=1)):
        raise InvalidRequest("date must be today or tomorrow")


async def get_order_plan(deps: Container, store_id: str, target_date: date) -> OrderPlanSnapshot:
    ctx = deps.clock.context(store_id)
    async with deps.db.read() as uow:
        await stores.require(uow, store_id)
        require_today_or_tomorrow(ctx, target_date)
        return await plan_order(uow, deps, ctx, target_date)


def _validate_order_shape(payload: PurchaseOrderInput) -> None:
    supplier_ids = [supplier_id for supplier_id, _ in payload.suppliers]
    ingredient_ids = [ingredient_id for _, lines in payload.suppliers for ingredient_id, _ in lines]
    if (
        not supplier_ids
        or len(set(supplier_ids)) != len(supplier_ids)
        or len(set(ingredient_ids)) != len(ingredient_ids)
    ):
        raise InvalidRequest("suppliers and ingredients must be unique and non-empty")


async def create_purchase_orders(
    deps: Container, store_id: str, payload: PurchaseOrderInput
) -> list[PurchaseOrderSnapshot]:
    _validate_order_shape(payload)
    ctx = deps.clock.context(store_id)
    async with deps.db.write() as uow:
        store = await stores.lock(uow, store_id)
        catalog_snapshot = await catalog.snapshot(uow, store_id)
        supplier_by_id = {item.id: item for item in await suppliers.list_all(uow, store_id)}
        for supplier_id, lines in payload.suppliers:
            if supplier_id not in supplier_by_id:
                raise NotFound("supplier")
            if any(ingredient_id not in catalog_snapshot.ingredient_by_id for ingredient_id, _ in lines):
                raise NotFound("ingredient")
        require_today_or_tomorrow(ctx, payload.target_date)
        target_hours = hours_for(store.business_hours, payload.target_date)
        if target_hours.closed:
            raise InvalidRequest("target date is a closed day")
        latest_purchase = await receipts.latest(uow, store_id)
        price_by_ingredient = await receipts.latest_prices(uow, store_id)
        order_day = day_code(ctx.local_date)

        created_ids = []
        for supplier_id, lines in sorted(payload.suppliers):
            target_supplier = supplier_by_id[supplier_id]
            if not target_supplier.orders_on(order_day):
                raise InvalidRequest("today is not an order day of the supplier")
            kept = []
            for ingredient_id, quantity in lines:
                item = catalog_snapshot.ingredient_by_id[ingredient_id]
                fact = latest_purchase.get(ingredient_id)
                if fact is None or fact.supplier_id != supplier_id:
                    raise InvalidRequest("ingredient cannot be ordered from the supplier")
                if quantity > 0:
                    kept.append((item, fact, quantity))
            if not kept:
                raise InvalidRequest("supplier has no line to order")
            order_id = new_id("po")
            await uow.execute(
                insert(purchase_order).values(
                    id=order_id,
                    supplier_id=supplier_id,
                    target_date=payload.target_date,
                    message_text=texts.purchase_order_message(
                        store.name,
                        payload.target_date,
                        ctx.local_date,
                        target_hours.open.hour,
                        [(fact.product_name, quantity, item.unit) for item, fact, quantity in kept],
                    ),
                    total_amount=money_total(
                        [(quantity, price_by_ingredient.get(item.id)) for item, _, quantity in kept]
                    ),
                    created_at=ctx.now,
                )
            )
            await uow.execute(
                insert(purchase_order_line),
                [
                    {"purchase_order_id": order_id, "ingredient_id": item.id, "quantity": quantity}
                    for item, _, quantity in kept
                ],
            )
            created_ids.append(order_id)
        rows = (await uow.execute(select(purchase_order).where(purchase_order.c.id.in_(created_ids)))).all()
        return sorted(
            (
                PurchaseOrderSnapshot(
                    row.id, row.target_date, supplier_by_id[row.supplier_id], row.message_text, row.total_amount
                )
                for row in rows
            ),
            key=lambda order: order.supplier.id,
        )


async def list_purchase_orders(deps: Container, store_id: str) -> list[PurchaseOrderSnapshot]:
    async with deps.db.read() as uow:
        await stores.require(uow, store_id)
        supplier_by_id = {item.id: item for item in await suppliers.list_all(uow, store_id)}
        rows = (
            await uow.execute(
                select(purchase_order)
                .join(supplier, supplier.c.id == purchase_order.c.supplier_id)
                .where(supplier.c.store_id == store_id)
                .order_by(purchase_order.c.created_at.desc(), purchase_order.c.id.desc())
            )
        ).all()
        return [
            PurchaseOrderSnapshot(
                row.id, row.target_date, supplier_by_id[row.supplier_id], row.message_text, row.total_amount
            )
            for row in rows
        ]


def normalized_phone(raw: str) -> str:
    """공백·하이픈·괄호를 지우고 선행 +는 보존한다. 나머지는 숫자 7~15자리여야 한다."""
    compact = "".join(char for char in raw if char not in " \t-()")
    digits = compact[1:] if compact.startswith("+") else compact
    if not digits.isascii() or not digits.isdigit() or not 7 <= len(digits) <= 15:
        raise InvalidRequest("recipientPhone is not a phone number")
    return compact


async def record_sent(
    deps: Container, store_id: str, purchase_order_id: str, channel: str, recipient_phone: str | None
) -> None:
    ctx = deps.clock.context(store_id)
    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        row = (
            await uow.execute(
                select(purchase_order, supplier.c.phone, supplier.c.phone_confirmed)
                .join(supplier, supplier.c.id == purchase_order.c.supplier_id)
                .where(purchase_order.c.id == purchase_order_id, supplier.c.store_id == store_id)
            )
        ).first()
        if row is None:
            raise NotFound("purchase order")
        phone = normalized_phone(recipient_phone) if recipient_phone is not None else None
        same_phone = phone is None or (row.phone_confirmed and row.phone == phone)
        if phone is not None:
            await suppliers.confirm_phone(uow, row.supplier_id, phone)
        if row.sent_at is None or row.sent_channel != channel or not same_phone:
            await uow.execute(
                update(purchase_order)
                .where(purchase_order.c.id == purchase_order_id)
                .values(sent_channel=channel, sent_at=ctx.now)
            )


async def sent_order_lines(uow: UoW, store_id: str, target_date: date) -> list[tuple[str, Decimal, str]]:
    """대상일에 쓰려고 보낸 발주서의 품목. 입고 예정 안내용이며 입고가 아니다."""
    rows = (
        await uow.execute(
            select(ingredient.c.id, ingredient.c.name, ingredient.c.unit, purchase_order_line.c.quantity)
            .join(purchase_order, purchase_order.c.id == purchase_order_line.c.purchase_order_id)
            .join(supplier, supplier.c.id == purchase_order.c.supplier_id)
            .join(ingredient, ingredient.c.id == purchase_order_line.c.ingredient_id)
            .where(
                supplier.c.store_id == store_id,
                purchase_order.c.target_date == target_date,
                purchase_order.c.sent_at.is_not(None),
            )
            .order_by(ingredient.c.id)
        )
    ).all()
    totals: dict[str, tuple[str, Decimal, str]] = {}
    for row in rows:
        name, quantity, unit = totals.get(row.id, (row.name, Decimal(0), row.unit))
        totals[row.id] = (name, quantity + row.quantity, unit)
    return list(totals.values())
