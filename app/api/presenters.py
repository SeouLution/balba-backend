"""내부 스냅샷을 공개 응답으로 바꾼다. 필수·nullable·생략 규칙은 openapi.yaml을 따른다."""

from app.modules import catalog, forecasting, inventory, ordering, receipts, sales, stores, suppliers, views
from app.shared import texts
from app.shared.policy import Policy
from app.shared.types import KST, MENU_UNIT, DayHours, hhmm, num


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


def recipes(result: catalog.RecipeList) -> dict:
    snapshot = result.catalog
    items = []
    for target_menu in snapshot.menus if snapshot else ():
        lines = []
        for line in snapshot.lines_by_menu.get(target_menu.id, ()):
            ingredient = snapshot.ingredient_by_id[line.ingredient_id]
            lines.append(
                {"ingredient": _ref(ingredient), "quantity": {"value": num(line.quantity), "unit": ingredient.unit}}
            )
        items.append({"menu": _ref(target_menu), "unit": MENU_UNIT, "items": lines})
    return {"generationStatus": result.generation_status, "items": items}


def sales_import(view: sales.SalesImportView) -> dict:
    detection = None
    if view.detection is not None:
        mapping = view.detection["columnMapping"]
        detection = {
            "menuCount": view.detection["menuCount"],
            "columns": view.detection.get("columns", []),
            "columnMapping": {key: mapping[key] for key in sales.MAPPING_KEYS},
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
        "unit": MENU_UNIT,
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
        "businessHours": [_day_hours(hours) for hours in view.business_hours],
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
        key = line.supplier.id if line.supplier else None
        group = groups.setdefault(key, {"supplier": _ref(line.supplier) if line.supplier else None, "lines": []})
        group["lines"].append(
            {
                "ingredient": _ref(line.ingredient),
                "quantity": {"value": num(line.quantity), "unit": line.unit},
                "price": {"unitPrice": num(line.price)},
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
        body: dict = {"id": item.id, "name": item.name, "unit": item.unit}
        body["price"] = {"unitPrice": num(row.unit_price)} if row.has_price_line else None
        body["supplier"] = _ref(row.supplier) if row.supplier else None
        items.append(body)
    return {"items": items}


def ingredient(item: catalog.IngredientSnapshot) -> dict:
    return {"id": item.id, "name": item.name, "unit": item.unit, "price": None, "supplier": None}


def manual_purchase(lot: receipts.ReceiptLot, item: catalog.IngredientSnapshot) -> dict:
    return {
        "id": lot.receipt_line_id,
        "ingredient": _ref(item),
        "purchasedOn": lot.purchased_on.isoformat(),
        "quantity": {"value": num(lot.quantity), "unit": item.unit},
        "unitPrice": num(lot.unit_price),
        "source": lot.source,
        "isStockReset": lot.is_stock_reset,
    }


def stock_list(as_of, rows: list[tuple[catalog.IngredientSnapshot, inventory.InventorySnapshot]]) -> dict:
    return {
        "asOf": as_of.isoformat(),
        "items": [
            {
                "ingredient": _ref(item),
                "stock": None if snapshot.stock is None else {"value": num(snapshot.stock), "unit": item.unit},
                "startedOn": snapshot.started_on.isoformat() if snapshot.started_on else None,
                "warning": texts.MISSING_PURCHASE_WARNING if snapshot.missing_purchase else None,
            }
            for item, snapshot in rows
        ],
    }
