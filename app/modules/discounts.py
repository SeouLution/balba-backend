import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import insert, select

from app.bootstrap import Container
from app.infrastructure.db.tables import discount, menu
from app.infrastructure.db.uow import UoW
from app.modules import catalog, inventory, ordering, receipts, stores
from app.shared import texts
from app.shared.policy import Policy
from app.shared.types import (
    KST,
    InvalidRequest,
    StoreContext,
    business_window,
    floor_int,
    kst_midnight,
    new_id,
    round_half_up,
)


@dataclass(frozen=True)
class DiscountInput:
    menu_id: str
    discount_type: str
    discount_value: Decimal
    start_time: time
    quantity: Decimal
    recommendation_id: str | None = None


@dataclass(frozen=True)
class DiscountEvaluation:
    menu: catalog.MenuSnapshot
    date: date
    discount_type: str
    discount_value: Decimal
    start_time: time
    quantity: Decimal
    original_price: int
    discounted_price: int
    waste_cost_avoided: int | None
    revenue_reduction: int
    promo_text: str


@dataclass(frozen=True)
class DiscountSnapshot:
    id: str
    evaluation: DiscountEvaluation


@dataclass(frozen=True)
class Recommendation:
    id: str
    evaluation: DiscountEvaluation
    expected_savings: int
    reason_text: str


@dataclass(frozen=True)
class SurplusItem:
    ingredient: catalog.IngredientSnapshot
    quantity: Decimal
    waste_cost: int | None
    mitigation: str


@dataclass(frozen=True)
class DiscountRecommendationsView:
    date: date
    base_time: time
    recommendations: tuple[Recommendation, ...]
    surplus_stock: tuple[SurplusItem, ...]


@dataclass(frozen=True)
class StockToday:
    """오늘 영업이 끝났을 때의 재고와 그중 추정 기한이 오늘인 양(사용 단위)."""

    catalog: catalog.CatalogSnapshot
    latest_purchase: dict[str, receipts.LatestPurchaseFact]
    stock: dict[str, Decimal | None]
    expiring_today: dict[str, Decimal]

    def unit_cost(self, ingredient_id: str) -> Decimal | None:
        fact = self.latest_purchase.get(ingredient_id)
        if fact is None or fact.unit_price is None:
            return None
        return Decimal(fact.unit_price) / self.catalog.ingredient_by_id[ingredient_id].content_quantity

    def recipe_uses(self, menu_id: str) -> list[tuple[str, Decimal]]:
        return [
            (line.ingredient_id, line.quantity)
            for line in self.catalog.lines_by_menu.get(menu_id, ())
            if line.quantity > 0
        ]


@dataclass(frozen=True)
class DiscountAnalysis:
    view: DiscountRecommendationsView
    stock: StockToday
    expiring_ingredient_ids: tuple[str, ...]
    discounted_ingredient_ids: tuple[str, ...]


# ───────── 순수 계산 ─────────


def recommendation_id(day: date, menu_id: str) -> str:
    return f"rec_{day.isoformat()}_{menu_id}"


def discounted_price(original_price: int, discount_type: str, discount_value: Decimal) -> int:
    if discount_type == "PERCENT":
        return round_half_up(Decimal(original_price) * (1 - discount_value / 100))
    return original_price - int(discount_value)


def validate_discount_value(discount_type: str, discount_value: Decimal, original_price: int) -> None:
    if discount_type == "PERCENT":
        valid = 0 < discount_value <= 100
    else:
        valid = 0 < discount_value <= original_price and discount_value == discount_value.to_integral_value()
    if not valid:
        raise InvalidRequest("discount value is out of range")


def waste_cost(consumed: dict[str, Decimal], stock: StockToday) -> int | None:
    """소진되는 임박 재고의 원가 합. 단가를 모르는 재료가 있으면 계산할 수 없다."""
    costs = [stock.unit_cost(ingredient_id) for ingredient_id in consumed]
    if any(cost is None for cost in costs):
        return None
    return round_half_up(sum((quantity * cost for quantity, cost in zip(consumed.values(), costs)), Decimal(0)))


def expiring_consumption(stock: StockToday, menu_id: str, quantity: Decimal) -> dict[str, Decimal]:
    return {
        ingredient_id: min(quantity * usage, stock.expiring_today[ingredient_id])
        for ingredient_id, usage in stock.recipe_uses(menu_id)
        if stock.expiring_today.get(ingredient_id, Decimal(0)) > 0
    }


def evaluate(
    payload: DiscountInput, target_menu: catalog.MenuSnapshot, stock: StockToday, today: date
) -> DiscountEvaluation:
    """할인가·덜 받는 금액·버릴 원가. dryRun과 실제 생성, 추천이 같이 쓴다."""
    price = discounted_price(target_menu.price, payload.discount_type, payload.discount_value)
    uses = stock.recipe_uses(target_menu.id)
    consumed = expiring_consumption(stock, target_menu.id, payload.quantity)
    if consumed:
        avoided = waste_cost(consumed, stock)
    elif uses and all(stock.stock.get(ingredient_id) is not None for ingredient_id, _ in uses):
        avoided = 0
    else:
        avoided = None
    return DiscountEvaluation(
        menu=target_menu,
        date=today,
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
        start_time=payload.start_time,
        quantity=payload.quantity,
        original_price=target_menu.price,
        discounted_price=price,
        waste_cost_avoided=avoided,
        revenue_reduction=round_half_up(Decimal(target_menu.price - price) * payload.quantity),
        promo_text=texts.promo_text(target_menu.name),
    )


def recommended_start(window: tuple[datetime, datetime], today: date, policy: Policy) -> time | None:
    start, end = window
    candidate = max(
        start,
        datetime.combine(today, policy.discount_base_time, KST),
        end - timedelta(minutes=policy.discount_start_minutes_before_close),
    )
    return None if candidate >= end else candidate.time().replace(tzinfo=None)


def _candidate(target_menu: catalog.MenuSnapshot, stock: StockToday, start_time: time, today: date, policy: Policy):
    uses = stock.recipe_uses(target_menu.id)
    if (
        target_menu.price is None
        or not uses
        or any(stock.stock.get(ingredient_id) is None for ingredient_id, _ in uses)
    ):
        return None
    impending = [
        (ingredient_id, usage) for ingredient_id, usage in uses if stock.expiring_today.get(ingredient_id, 0) > 0
    ]
    if not impending or any(stock.unit_cost(ingredient_id) is None for ingredient_id, _ in impending):
        return None
    quantity = min(
        floor_int(min(stock.stock[ingredient_id] / usage for ingredient_id, usage in uses)),
        max(floor_int(stock.expiring_today[ingredient_id] / usage) for ingredient_id, usage in impending),
    )
    if quantity < 1:
        return None
    payload = DiscountInput(
        target_menu.id, "PERCENT", policy.discount_recommended_percent, start_time, Decimal(quantity)
    )
    evaluation = evaluate(payload, target_menu, stock, today)
    savings = evaluation.waste_cost_avoided - evaluation.revenue_reduction
    if savings <= 0:
        return None
    consumed = expiring_consumption(stock, target_menu.id, Decimal(quantity))
    costliest = max(
        consumed, key=lambda ingredient_id: (consumed[ingredient_id] * stock.unit_cost(ingredient_id), ingredient_id)
    )
    reason = texts.discount_reason(stock.catalog.ingredient_by_id[costliest].name)
    return Recommendation(recommendation_id(today, target_menu.id), evaluation, savings, reason)


def recommend(
    stock: StockToday, start_time: time | None, today: date, policy: Policy
) -> tuple[list[Recommendation], set[str]]:
    """기대 절감이 가장 큰 메뉴부터 고르고, 고른 만큼 재고를 빼고 다시 계산한다. 같은 재고를 두 번 세지 않는다."""
    if start_time is None:
        return [], set()
    working = StockToday(stock.catalog, stock.latest_purchase, dict(stock.stock), dict(stock.expiring_today))
    remaining = {item.id: item for item in stock.catalog.menus}
    chosen: list[Recommendation] = []
    discounted_ingredient_ids: set[str] = set()
    while remaining:
        candidates = [
            found
            for target_menu in remaining.values()
            if (found := _candidate(target_menu, working, start_time, today, policy))
        ]
        if not candidates:
            break
        best = min(candidates, key=lambda item: (-item.expected_savings, item.evaluation.menu.id))
        chosen.append(best)
        del remaining[best.evaluation.menu.id]
        for ingredient_id, usage in working.recipe_uses(best.evaluation.menu.id):
            used = best.evaluation.quantity * usage
            if working.expiring_today.get(ingredient_id, 0) > 0:
                discounted_ingredient_ids.add(ingredient_id)
                working.expiring_today[ingredient_id] = max(Decimal(0), working.expiring_today[ingredient_id] - used)
            working.stock[ingredient_id] -= used
    return chosen, discounted_ingredient_ids


# ───────── 조회 시 계산 ─────────


async def stock_today(
    uow: UoW, store: stores.StoreSnapshot, today: date
) -> tuple[StockToday, dict[str, inventory.InventorySnapshot]]:
    snapshots = await inventory.end_of(uow, store, today)
    stock = StockToday(
        catalog=await catalog.snapshot(uow, store.id),
        latest_purchase=await receipts.latest(uow, store.id),
        stock={ingredient_id: item.stock for ingredient_id, item in snapshots.items()},
        expiring_today={
            ingredient_id: expiring
            for ingredient_id, item in snapshots.items()
            if item.stock is not None and (expiring := item.expiring_between(today, today)) > 0
        },
    )
    return stock, snapshots


async def analyze(uow: UoW, deps: Container, ctx: StoreContext) -> DiscountAnalysis:
    store = await stores.require(uow, ctx.store_id)
    today, policy = ctx.local_date, deps.policy
    stock, snapshots = await stock_today(uow, store, today)
    expiring_ids = tuple(sorted(stock.expiring_today))
    window = business_window(store.business_hours, today)
    if window is None or ctx.local_now.time() < policy.discount_base_time:
        empty = DiscountRecommendationsView(today, policy.discount_base_time, (), ())
        return DiscountAnalysis(empty, stock, expiring_ids, ())

    recommendations, discounted_ids = recommend(stock, recommended_start(window, today, policy), today, policy)
    reduced_ids = (await ordering.plan_order(uow, deps, ctx, today + timedelta(days=1))).reduced_ingredient_ids
    window_start, window_end = ordering.surplus_window(today, policy)
    surplus = []
    for ingredient_id in sorted(snapshots):
        item = snapshots[ingredient_id]
        target = stock.catalog.ingredient_by_id[ingredient_id]
        if item.stock is None:
            continue
        if ingredient_id in stock.expiring_today:
            quantity = stock.expiring_today[ingredient_id]
            cost = waste_cost({ingredient_id: quantity}, stock)
            mitigation = "DISCOUNT_RECOMMENDED" if ingredient_id in discounted_ids else "NONE"
            surplus.append(SurplusItem(target, quantity, cost, mitigation))
        elif (quantity := item.expiring_between(window_start, window_end)) > 0:
            surplus.append(
                SurplusItem(target, quantity, None, "ORDER_REDUCED" if ingredient_id in reduced_ids else "NONE")
            )
    view = DiscountRecommendationsView(today, policy.discount_base_time, tuple(recommendations), tuple(surplus))
    return DiscountAnalysis(view, stock, expiring_ids, tuple(sorted(discounted_ids)))


async def get_recommendations(deps: Container, store_id: str) -> DiscountRecommendationsView:
    ctx = deps.clock.context(store_id)
    async with deps.db.read() as uow:
        return (await analyze(uow, deps, ctx)).view


# ───────── 생성·이력 ─────────


def _start_inside_window(start_time: time, window: tuple[datetime, datetime], today: date) -> bool:
    start, end = window
    moment = datetime.combine(today, start_time, KST)
    if moment < start and end.date() > today:
        moment += timedelta(days=1)
    return start <= moment < end


async def _evaluate_request(uow: UoW, deps: Container, ctx: StoreContext, payload: DiscountInput) -> DiscountEvaluation:
    store = await stores.require(uow, ctx.store_id)
    target_menu = await catalog.require_menu(uow, ctx.store_id, payload.menu_id)
    window = business_window(store.business_hours, ctx.local_date)
    if target_menu.price is None or window is None or payload.quantity < 1:
        raise InvalidRequest("menu has no price, the store is closed today, or quantity is below 1")
    validate_discount_value(payload.discount_type, payload.discount_value, target_menu.price)
    if not _start_inside_window(payload.start_time, window, ctx.local_date):
        raise InvalidRequest("startTime is outside today's business hours")
    if payload.recommendation_id is not None and payload.recommendation_id != recommendation_id(
        ctx.local_date, payload.menu_id
    ):
        raise InvalidRequest("recommendationId does not match the menu and date")
    analysis = await analyze(uow, deps, ctx)
    for recommended in analysis.view.recommendations:
        same = recommended.evaluation
        if recommended.id == payload.recommendation_id and (
            same.discount_type,
            same.discount_value,
            same.start_time,
            same.quantity,
        ) == (payload.discount_type, payload.discount_value, payload.start_time, payload.quantity):
            return same
    return evaluate(payload, target_menu, analysis.stock, ctx.local_date)


def _preview_id(ctx: StoreContext, payload: DiscountInput) -> str:
    canonical = "|".join(
        str(part)
        for part in (
            ctx.store_id,
            ctx.local_date,
            payload.menu_id,
            payload.discount_type,
            payload.discount_value.normalize(),
            payload.start_time,
            payload.quantity.normalize(),
            payload.recommendation_id,
        )
    )
    return "preview_" + hashlib.sha256(canonical.encode()).hexdigest()[:24]


async def create_discount(deps: Container, store_id: str, payload: DiscountInput, dry_run: bool) -> DiscountSnapshot:
    ctx = deps.clock.context(store_id)
    if dry_run:
        async with deps.db.read() as uow:
            return DiscountSnapshot(_preview_id(ctx, payload), await _evaluate_request(uow, deps, ctx, payload))
    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        evaluation = await _evaluate_request(uow, deps, ctx, payload)
        discount_id = new_id("dc")
        await uow.execute(
            insert(discount).values(
                id=discount_id,
                menu_id=evaluation.menu.id,
                discount_type=evaluation.discount_type,
                discount_value=evaluation.discount_value,
                start_time=evaluation.start_time,
                quantity=evaluation.quantity,
                original_price=evaluation.original_price,
                waste_cost_avoided=evaluation.waste_cost_avoided,
                revenue_reduction=evaluation.revenue_reduction,
                promo_text=evaluation.promo_text,
                created_at=ctx.now,
            )
        )
        return DiscountSnapshot(discount_id, evaluation)


async def list_discounts(deps: Container, store_id: str, time_range: str) -> list[DiscountSnapshot]:
    ctx = deps.clock.context(store_id)
    statement = (
        select(discount, menu.c.store_id, menu.c.name, menu.c.unit, menu.c.price)
        .join(menu, menu.c.id == discount.c.menu_id)
        .where(menu.c.store_id == store_id)
        .order_by(discount.c.created_at.desc(), discount.c.id.desc())
    )
    today_start = kst_midnight(ctx.local_date)
    if time_range == "TODAY":
        statement = statement.where(
            discount.c.created_at >= today_start, discount.c.created_at < today_start + timedelta(days=1)
        )
    elif time_range == "THIS_WEEK":
        week_start = today_start - timedelta(days=ctx.local_date.weekday())
        statement = statement.where(discount.c.created_at >= week_start, discount.c.created_at <= ctx.now)
    async with deps.db.read() as uow:
        await stores.require(uow, store_id)
        rows = (await uow.execute(statement)).all()
    return [
        DiscountSnapshot(
            row.id,
            DiscountEvaluation(
                menu=catalog.MenuSnapshot(row.menu_id, row.store_id, row.name, row.unit, row.price),
                date=row.created_at.astimezone(KST).date(),
                discount_type=row.discount_type,
                discount_value=row.discount_value,
                start_time=row.start_time,
                quantity=row.quantity,
                original_price=row.original_price,
                discounted_price=discounted_price(row.original_price, row.discount_type, row.discount_value),
                waste_cost_avoided=row.waste_cost_avoided,
                revenue_reduction=row.revenue_reduction,
                promo_text=row.promo_text,
            ),
        )
        for row in rows
    ]
