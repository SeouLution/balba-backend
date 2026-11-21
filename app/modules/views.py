import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.bootstrap import Container
from app.infrastructure.db.uow import UoW
from app.modules import catalog, discounts, forecasting, ordering, receipts, sales, stores, suppliers
from app.shared import texts
from app.shared.types import KST, StoreContext, business_window


@dataclass(frozen=True)
class AlertSnapshot:
    id: str
    type: str
    title: str
    message: str | None
    actions: tuple[str, ...]


@dataclass(frozen=True)
class Banner:
    type: str
    title: str
    message: str | None


@dataclass(frozen=True)
class OrderSummary:
    supplier_count: int
    estimated_total: int | None


@dataclass(frozen=True)
class HomeView:
    store_name: str
    view: str
    target_date: date
    weather: forecasting.WeatherSnapshot | None
    events: tuple[forecasting.NearbyEvent, ...]
    banners: tuple[Banner, ...]
    forecast_items: tuple[forecasting.ForecastSnapshot, ...]
    total_menu_count: int
    has_order_key: bool
    order: OrderSummary | None
    cautions: tuple[AlertSnapshot, ...]


@dataclass(frozen=True)
class ForecastAdjustment:
    item: forecasting.ForecastSnapshot
    order: OrderSummary | None


@dataclass(frozen=True)
class IngredientRow:
    ingredient: catalog.IngredientSnapshot
    latest_purchase: receipts.LatestPurchaseFact | None
    supplier: suppliers.SupplierSnapshot | None


@dataclass(frozen=True)
class IngredientListView:
    items: tuple[IngredientRow, ...]
    core_count: int
    later_count: int
    notices: tuple[AlertSnapshot, ...]


def order_summary(plan: ordering.OrderPlanSnapshot) -> OrderSummary | None:
    return OrderSummary(plan.supplier_count, plan.total) if plan.lines else None


async def build_alerts(uow: UoW, deps: Container, ctx: StoreContext) -> list[AlertSnapshot]:
    """알림의 유일한 진입점. 홈의 cautions와 재료 탭의 notices가 같은 결과를 쓴다."""
    analysis = await discounts.analyze(uow, deps, ctx)
    ingredient_by_id = analysis.stock.catalog.ingredient_by_id
    alerts = []
    for ingredient_id in analysis.expiring_ingredient_ids:
        with_discount = ingredient_id in analysis.discounted_ingredient_ids
        title, message = texts.expiry_alert(ingredient_by_id[ingredient_id].name, with_discount)
        alerts.append(
            AlertSnapshot(
                id=f"expiry:{ingredient_id}:{ctx.local_date.isoformat()}",
                type="EXPIRY_IMMINENT",
                title=title,
                message=message,
                actions=("OPEN_DISCOUNTS",) if with_discount else (),
            )
        )
    price_alerts = []
    for change in await receipts.price_changes(uow, ctx.store_id):
        if change.previous <= 0 or change.ingredient_id not in ingredient_by_id:
            continue
        ratio = Decimal(abs(change.current - change.previous)) / Decimal(change.previous)
        if ratio >= deps.policy.price_change_alert_ratio:
            title, message = texts.price_changed_alert(
                ingredient_by_id[change.ingredient_id].name, change.previous, change.current
            )
            price_alerts.append(
                AlertSnapshot(
                    f"price:{change.ingredient_id}:{change.latest_line_id}", "PRICE_CHANGED", title, message, ()
                )
            )
    return alerts + sorted(price_alerts, key=lambda alert: alert.id)


def resolve_view(ctx: StoreContext, deps: Container) -> tuple[str, date]:
    if ctx.local_now.time() < deps.policy.tomorrow_start:
        return "TODAY", ctx.local_date
    return "TOMORROW", ctx.local_date + timedelta(days=1)


async def _banners(
    uow: UoW,
    ctx: StoreContext,
    store: stores.StoreSnapshot,
    view: str,
    forecasts: list[forecasting.ForecastSnapshot],
    ranked_menu_ids: list[str],
    forecast_context: forecasting.ForecastContext,
    sales_snapshot: sales.SalesSnapshot,
) -> list[Banner]:
    banners = []
    first_at = store.first_forecast_at
    if first_at is not None and first_at.astimezone(KST).date() == ctx.local_date and forecasts:
        banners.append(
            Banner("FIRST_FORECAST", *texts.first_forecast_banner(math.ceil(sales_snapshot.covered_day_count() / 7)))
        )
    if view != "TODAY":
        return banners

    changed = [item for item in forecasts if item.previous_recommended is not None]
    if changed:
        biggest = max(changed, key=lambda item: (abs(item.recommended - item.previous_recommended), item.menu.id))
        diff = biggest.recommended - biggest.previous_recommended
        banners.append(
            Banner("OVERNIGHT_CHANGE", texts.overnight_change_title(biggest.menu.name, diff, biggest.menu.unit), None)
        )
    elif forecast_context.weather is not None and forecast_context.weather.change_text:
        banners.append(Banner("OVERNIGHT_CHANGE", texts.overnight_change_title(None, Decimal(0), ""), None))

    today_events = forecasting.events_during(
        forecast_context.events, business_window(store.business_hours, ctx.local_date)
    )
    if today_events:
        nearest = today_events[0]
        by_menu_id = {item.menu.id: item for item in forecasts}
        top_menu_name = by_menu_id[ranked_menu_ids[0]].menu.name if ranked_menu_ids else None
        banners.append(
            Banner(
                "EVENT_TODAY",
                *texts.event_today_banner(
                    nearest.start_at,
                    nearest.end_at,
                    nearest.peak_from.hour if nearest.peak_from else None,
                    nearest.peak_to.hour if nearest.peak_to else None,
                    top_menu_name,
                ),
            )
        )

    delivered = await ordering.sent_order_lines(uow, ctx.store_id, ctx.local_date)
    if delivered:
        banners.append(Banner("DELIVERY_EXPECTED", *texts.delivery_expected_banner(delivered)))
    return banners


async def get_home(deps: Container, store_id: str) -> HomeView:
    ctx = deps.clock.context(store_id)
    async with deps.db.read() as uow:
        store = await stores.require(uow, store_id)
        view, target_date = resolve_view(ctx, deps)
        menus = (await catalog.snapshot(uow, store_id)).menus
        forecasts = await forecasting.read(uow, store_id, target_date)
        forecast_context = await forecasting.context(uow, store, target_date, deps.policy)
        sales_snapshot = await sales.read(
            uow,
            store_id,
            target_date - timedelta(days=forecasting.SALES_LOOKBACK_DAYS),
            target_date - timedelta(days=1),
        )
        ranked = forecasting.rank_by_recent_sales(sales_snapshot, [item.menu.id for item in forecasts])
        by_menu_id = {item.menu.id: item for item in forecasts}
        order = None
        if view == "TOMORROW":
            order = order_summary(await ordering.plan_order(uow, deps, ctx, target_date))
        return HomeView(
            store_name=store.name,
            view=view,
            target_date=target_date,
            weather=forecast_context.weather,
            events=forecast_context.events,
            banners=tuple(await _banners(uow, ctx, store, view, forecasts, ranked, forecast_context, sales_snapshot)),
            forecast_items=tuple(by_menu_id[menu_id] for menu_id in ranked[:3]),
            total_menu_count=len(menus),
            has_order_key=view == "TOMORROW",
            order=order,
            cautions=tuple(await build_alerts(uow, deps, ctx)),
        )


async def adjust_forecast(
    deps: Container, store_id: str, target_date: date, menu_id: str, quantity: Decimal
) -> ForecastAdjustment:
    ctx = deps.clock.context(store_id)
    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        await catalog.require_menu(uow, store_id, menu_id)
        ordering.require_today_or_tomorrow(ctx, target_date)
        item = await forecasting.set_adjusted(uow, store_id, target_date, menu_id, quantity)
        order = None
        if target_date != ctx.local_date:
            order = order_summary(await ordering.plan_order(uow, deps, ctx, target_date))
        return ForecastAdjustment(item, order)


async def list_ingredients(deps: Container, store_id: str, status: str | None) -> IngredientListView:
    ctx = deps.clock.context(store_id)
    async with deps.db.read() as uow:
        await stores.require(uow, store_id)
        catalog_snapshot = await catalog.snapshot(uow, store_id)
        latest_purchase = await receipts.latest(uow, store_id)
        supplier_by_id = {item.id: item for item in await suppliers.list_all(uow, store_id)}
        ordered = sorted(catalog_snapshot.ingredients_by_usage(), key=lambda item: item.status != "CORE")
        rows = [
            IngredientRow(
                ingredient=item,
                latest_purchase=latest_purchase.get(item.id),
                supplier=supplier_by_id[latest_purchase[item.id].supplier_id] if item.id in latest_purchase else None,
            )
            for item in ordered
            if status is None or item.status == status
        ]
        return IngredientListView(
            items=tuple(rows),
            core_count=sum(1 for item in catalog_snapshot.ingredients if item.status == "CORE"),
            later_count=sum(1 for item in catalog_snapshot.ingredients if item.status == "LATER"),
            notices=tuple(await build_alerts(uow, deps, ctx)),
        )
