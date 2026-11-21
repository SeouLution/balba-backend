import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import insert, select

from app.bootstrap import Container
from app.infrastructure.db.tables import discount, menu
from app.infrastructure.db.uow import UoW
from app.modules import catalog, forecasting, receipts, stores
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
class DiscountRecommendationsView:
    date: date
    base_time: time
    recommendations: tuple[Recommendation, ...]


@dataclass(frozen=True)
class RecipeCosts:
    """메뉴 1단위를 만드는 데 든 재료 원가의 입력."""

    catalog: catalog.CatalogSnapshot
    latest_purchase: dict[str, receipts.LatestPurchaseFact]

    def menu_unit_cost(self, menu_id: str) -> Decimal | None:
        """단가와 환산 비율을 아는 재료만 더한다. 하나도 없으면 계산할 수 없다."""
        known = []
        for line in self.catalog.lines_by_menu.get(menu_id, ()):
            fact = self.latest_purchase.get(line.ingredient_id)
            content_quantity = self.catalog.ingredient_by_id[line.ingredient_id].content_quantity
            if line.quantity > 0 and fact is not None and fact.unit_price is not None and content_quantity:
                known.append(line.quantity * Decimal(fact.unit_price) / content_quantity)
        return sum(known, Decimal(0)) if known else None


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


def evaluate(
    payload: DiscountInput, target_menu: catalog.MenuSnapshot, costs: RecipeCosts, today: date
) -> DiscountEvaluation:
    """할인가·덜 받는 금액·할인 수량의 재료 원가. dryRun과 실제 생성, 추천이 같이 쓴다."""
    price = discounted_price(target_menu.price, payload.discount_type, payload.discount_value)
    unit_cost = costs.menu_unit_cost(target_menu.id)
    return DiscountEvaluation(
        menu=target_menu,
        date=today,
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
        start_time=payload.start_time,
        quantity=payload.quantity,
        original_price=target_menu.price,
        discounted_price=price,
        waste_cost_avoided=None if unit_cost is None else round_half_up(payload.quantity * unit_cost),
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


def recommend(
    forecasts: list[forecasting.ForecastSnapshot],
    costs: RecipeCosts,
    start_time: time | None,
    today: date,
    policy: Policy,
) -> list[Recommendation]:
    """추천 준비량보다 더 만든 메뉴마다 그 수량만큼 할인을 추천한다. 기대 절감이 양수인 것만 남긴다."""
    if start_time is None:
        return []
    chosen = []
    for item in forecasts:
        made = item.produced if item.produced is not None else item.planned
        quantity = floor_int(made - item.recommended)
        if item.menu.price is None or quantity < 1:
            continue
        payload = DiscountInput(
            item.menu.id, "PERCENT", policy.discount_recommended_percent, start_time, Decimal(quantity)
        )
        evaluation = evaluate(payload, item.menu, costs, today)
        if evaluation.waste_cost_avoided is None:
            continue
        savings = evaluation.waste_cost_avoided - evaluation.revenue_reduction
        if savings > 0:
            reason = texts.discount_reason(Decimal(quantity), item.menu.unit)
            chosen.append(Recommendation(recommendation_id(today, item.menu.id), evaluation, savings, reason))
    return sorted(chosen, key=lambda item: (-item.expected_savings, item.evaluation.menu.id))


# ───────── 조회 시 계산 ─────────


async def recipe_costs(uow: UoW, store_id: str) -> RecipeCosts:
    return RecipeCosts(await catalog.snapshot(uow, store_id), await receipts.latest(uow, store_id))


async def get_recommendations(deps: Container, store_id: str) -> DiscountRecommendationsView:
    ctx = deps.clock.context(store_id)
    today, policy = ctx.local_date, deps.policy
    async with deps.db.read() as uow:
        store = await stores.require(uow, store_id)
        window = business_window(store.business_hours, today)
        if window is None or ctx.local_now.time() < policy.discount_base_time:
            return DiscountRecommendationsView(today, policy.discount_base_time, ())
        recommendations = recommend(
            await forecasting.read(uow, store_id, today),
            await recipe_costs(uow, store_id),
            recommended_start(window, today, policy),
            today,
            policy,
        )
        return DiscountRecommendationsView(today, policy.discount_base_time, tuple(recommendations))


# ───────── 생성·이력 ─────────


def _start_inside_window(start_time: time, window: tuple[datetime, datetime], today: date) -> bool:
    start, end = window
    moment = datetime.combine(today, start_time, KST)
    if moment < start and end.date() > today:
        moment += timedelta(days=1)
    return start <= moment < end


async def _evaluate_request(uow: UoW, ctx: StoreContext, payload: DiscountInput) -> DiscountEvaluation:
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
    return evaluate(payload, target_menu, await recipe_costs(uow, ctx.store_id), ctx.local_date)


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
            return DiscountSnapshot(_preview_id(ctx, payload), await _evaluate_request(uow, ctx, payload))
    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        evaluation = await _evaluate_request(uow, ctx, payload)
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
