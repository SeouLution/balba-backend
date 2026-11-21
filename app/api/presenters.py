"""내부 스냅샷을 공개 응답으로 바꾼다. 필수·nullable·생략 규칙은 openapi.yaml을 따른다."""

import logging

from app.modules import catalog, discounts, forecasting, ordering, receipts, sales, stores, suppliers, views
from app.shared import texts
from app.shared.policy import Policy
from app.shared.types import KST, DayHours, hhmm, num

logger = logging.getLogger(__name__)


def _ref(item) -> dict:
    return {"id": item.id, "name": item.name}


def _day_hours(item: DayHours) -> dict:
    if item.closed:
        return {"day": item.day, "closed": True}
    return {"day": item.day, "closed": False, "open": hhmm(item.open), "close": hhmm(item.close)}


def store(item: stores.StoreSnapshot) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "category": item.category,
        "address": {"roadAddress": item.road_address},
        "businessHours": [_day_hours(hours) for hours in item.business_hours],
    }


def candidates(result: catalog.CandidateList) -> dict:
    items = []
    if result.catalog is not None:
        for item in result.catalog.ingredients_by_usage():
            menu_ids = result.catalog.menu_ids_by_ingredient.get(item.id, ())
            items.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "tags": list(item.tags),
                    "usedInMenus": [_ref(result.catalog.menu_by_id[menu_id]) for menu_id in menu_ids],
                    "usedInMenuCount": len(menu_ids),
                }
            )
    return {"generationStatus": result.generation_status, "items": items}


def recipes(snapshot: catalog.CatalogSnapshot | None) -> dict:
    """1.6 화면용. 핵심(CORE) 재료 항목만 내보낸다."""
    if snapshot is None:
        return {"items": []}
    items = []
    for target_menu in snapshot.menus:
        lines = []
        for line in snapshot.lines_by_menu.get(target_menu.id, ()):
            ingredient = snapshot.ingredient_by_id[line.ingredient_id]
            if ingredient.status != "CORE":
                continue
            if not ingredient.usage_unit:
                logger.warning("recipe item skipped: ingredient %s has no usage unit", ingredient.id)
                continue
            lines.append(
                {
                    "ingredient": _ref(ingredient),
                    "quantity": {"value": num(line.quantity), "unit": ingredient.usage_unit},
                }
            )
        items.append({"menu": _ref(target_menu), "unit": target_menu.unit, "items": lines})
    return {"items": items}


def sales_import(view: sales.SalesImportView) -> dict:
    detection = None
    if view.detection is not None:
        mapping = view.detection["columnMapping"]
        detection = {
            "menuCount": view.detection["menuCount"],
            "columnMapping": {key: mapping[key] for key in ("date", "menuName", "quantity")},
            "preview": view.detection["preview"],
        }
    return {
        "id": view.id,
        "status": view.status,
        "posFormat": {"posSystem": view.pos_system} if view.pos_system else None,
        "detection": detection,
        "period": {"from": view.period_from.isoformat(), "to": view.period_to.isoformat()}
        if view.period_from
        else None,
        "recordCount": view.record_count,
    }


def receipt_scan(view: receipts.ReceiptScanView) -> dict:
    body: dict = {"id": view.id, "status": view.status}
    if view.groups is None:
        return body
    for group in view.groups:
        for receipt in group["receipts"]:
            for line in receipt["lines"]:
                if line["quantity"]:
                    line["quantity"] = {"value": num(line["quantity"]["value"]), "unit": line["quantity"]["unit"]}
    body.update(groups=view.groups, questions=view.questions, removedDuplicateTexts=view.notices)
    return body


def supplier_list(rows: list[tuple[suppliers.SupplierSnapshot, int]]) -> dict:
    return {
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "orderDays": list(item.order_days),
                "orderDaysStatus": item.order_days_status,
                "receiptCount": receipt_count,
            }
            for item, receipt_count in rows
        ]
    }


def event_summary(item: forecasting.NearbyEvent) -> dict:
    """홈과 발주 계획이 같이 쓴다. 모르는 place·crowdLevel은 생략하고 나머지 선택 값은 null로 보낸다."""
    body = {
        "id": item.id,
        "name": item.name,
        "distanceMeters": item.distance_meters,
        "startAt": item.start_at.astimezone(KST).isoformat(),
        "endAt": item.end_at.astimezone(KST).isoformat(),
        "expectedVisitors": item.expected_visitors,
        "peakFrom": hhmm(item.peak_from) if item.peak_from else None,
        "peakTo": hhmm(item.peak_to) if item.peak_to else None,
        "detailUrl": item.detail_url,
    }
    if item.place:
        body["place"] = item.place
    if item.crowd_level:
        body["crowdLevel"] = item.crowd_level
    return body


def forecast_item(item: forecasting.ForecastSnapshot, policy: Policy, today_view: bool) -> dict:
    direction, diff = forecasting.comparison(item, policy)
    body = {
        "menu": _ref(item.menu),
        "unit": item.menu.unit,
        "recommended": num(item.recommended),
        "planned": num(item.planned),
        "comparison": {"direction": direction, "diff": diff},
        "eventDiff": num(item.event_diff),
        "previousRecommended": num(item.previous_recommended) if today_view else None,
        "produced": num(item.produced) if today_view else None,
    }
    if item.summary_text:
        body["summaryText"] = item.summary_text
    return body


def _order_summary(summary: views.OrderSummary | None) -> dict | None:
    if summary is None:
        return None
    return {"supplierCount": summary.supplier_count, "estimatedTotal": summary.estimated_total}


def home(view: views.HomeView, policy: Policy) -> dict:
    tiles: dict = {"events": [event_summary(item) for item in view.events]}
    if view.weather is not None:
        tiles["weather"] = {
            "temperatureC": num(view.weather.temperature_c),
            "condition": view.weather.condition,
            "text": view.weather.summary_text,
            "changeText": view.weather.change_text,
        }
    body = {
        "storeName": view.store_name,
        "view": view.view,
        "targetDate": view.target_date.isoformat(),
        "tiles": tiles,
        "banners": [{"type": item.type, "title": item.title, "message": item.message} for item in view.banners],
        "forecast": {
            "items": [forecast_item(item, policy, view.view == "TODAY") for item in view.forecast_items],
            "totalMenuCount": view.total_menu_count,
        },
    }
    if view.has_order_key:
        body["order"] = _order_summary(view.order)
    return body


def forecast_adjustment(result: views.ForecastAdjustment, policy: Policy, today_view: bool) -> dict:
    return {"item": forecast_item(result.item, policy, today_view), "order": _order_summary(result.order)}


def order_plan(plan: ordering.OrderPlanSnapshot) -> dict:
    groups: dict[str, dict] = {}
    for line in plan.lines:
        group = groups.setdefault(line.supplier.id, {"supplier": _ref(line.supplier), "lines": []})
        group["lines"].append(
            {
                "ingredient": _ref(line.ingredient),
                "quantity": {"value": num(line.quantity), "unit": line.unit},
                "price": {"unitPrice": line.price},
                "eventDiff": num(line.event_diff),
                "stock": None if line.stock is None else {"value": num(line.stock), "unit": line.unit},
            }
        )
    return {
        "targetDate": plan.target_date.isoformat(),
        "events": [event_summary(item) for item in plan.events],
        "usualTotal": plan.usual_total,
        "suppliers": list(groups.values()),
    }


def purchase_orders(orders: list[ordering.PurchaseOrderSnapshot]) -> dict:
    return {
        "items": [
            {
                "id": order.id,
                "targetDate": order.target_date.isoformat(),
                "supplier": {
                    "id": order.supplier.id,
                    "name": order.supplier.name,
                    "phone": order.supplier.phone,
                    "phoneConfirmed": order.supplier.phone_confirmed,
                    "phoneSourceText": texts.phone_source_text(order.supplier.phone_read_on)
                    if order.supplier.phone and not order.supplier.phone_confirmed and order.supplier.phone_read_on
                    else None,
                },
                "messageText": order.message_text,
            }
            for order in orders
        ]
    }


def purchase_order_summaries(orders: list[ordering.PurchaseOrderSnapshot]) -> dict:
    return {
        "items": [
            {
                "id": order.id,
                "targetDate": order.target_date.isoformat(),
                "supplier": _ref(order.supplier),
                "totalAmount": order.total_amount,
            }
            for order in orders
        ]
    }


def ingredient_list(view: views.IngredientListView) -> dict:
    items = []
    for row in view.items:
        item = row.ingredient
        body: dict = {"id": item.id, "name": item.name, "status": item.status}
        if item.usage_unit:
            body["usageUnit"] = item.usage_unit
        body["purchaseUnit"] = (
            {"label": item.purchase_unit, "contentQuantity": num(item.content_quantity)} if item.purchase_unit else None
        )
        body["price"] = {"unitPrice": row.latest_purchase.unit_price} if row.latest_purchase else None
        body["supplier"] = _ref(row.supplier) if row.supplier else None
        items.append(body)
    return {"items": items, "counts": {"core": view.core_count, "later": view.later_count}}


def _discount_value(evaluation: discounts.DiscountEvaluation) -> dict:
    return {"type": evaluation.discount_type, "value": num(evaluation.discount_value)}


def discount(snapshot: discounts.DiscountSnapshot, policy: Policy) -> dict:
    evaluation = snapshot.evaluation
    body = {
        "id": snapshot.id,
        "date": evaluation.date.isoformat(),
        "menu": _ref(evaluation.menu),
        "unit": evaluation.menu.unit,
        "discount": _discount_value(evaluation),
        "originalPrice": evaluation.original_price,
        "discountedPrice": evaluation.discounted_price,
        "startTime": hhmm(evaluation.start_time),
        "quantity": num(evaluation.quantity),
        "revenueReduction": evaluation.revenue_reduction,
        "promoText": evaluation.promo_text,
        "channels": [
            {"app": channel.app, "launchUrl": channel.launch_url, "supportsPaste": channel.supports_paste}
            for channel in policy.discount_channels
        ],
    }
    if evaluation.waste_cost_avoided is not None:
        body["wasteCostAvoided"] = evaluation.waste_cost_avoided
    return body


def discount_history(snapshots: list[discounts.DiscountSnapshot], policy: Policy) -> dict:
    return {"items": [discount(snapshot, policy) for snapshot in snapshots]}


def discount_recommendations(view: discounts.DiscountRecommendationsView) -> dict:
    return {
        "date": view.date.isoformat(),
        "baseTime": hhmm(view.base_time),
        "recommendations": [
            {
                "id": item.id,
                "menu": _ref(item.evaluation.menu),
                "unit": item.evaluation.menu.unit,
                "discount": _discount_value(item.evaluation),
                "startTime": hhmm(item.evaluation.start_time),
                "quantity": num(item.evaluation.quantity),
                "reasonText": item.reason_text,
                "expectedSavings": item.expected_savings,
            }
            for item in view.recommendations
        ],
    }
