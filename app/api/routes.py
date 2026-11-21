"""30개 operation. 검증된 입력을 유스케이스에 한 번 넘기고 결과를 응답으로 바꾼다."""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from app.api import presenters
from app.api import schemas as dto
from app.bootstrap import Container
from app.modules import accounts, catalog, forecasting, inventory, ordering, receipts, sales, stores, suppliers, views
from app.shared.types import DayHours, InternalFailure, NotFound, parse_hhmm

router = APIRouter(prefix="/v1")


def get_container(request: Request) -> Container:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise InternalFailure("application is not started")
    return container


Deps = Annotated[Container, Depends(get_container)]
IsoDatePath = dto.IsoDate
AccessToken = Annotated[str | None, Cookie()]


async def _require_store_owner(storeId: str, deps: Deps, access_token: AccessToken = None) -> None:
    account = await accounts.get_account(deps, accounts.verified_user_id(deps, access_token))
    if account.store_id != storeId:
        raise NotFound("store")


OwnsStore = Depends(_require_store_owner)


def _json(body: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status_code)


def _empty(status_code: int = 204) -> Response:
    return Response(status_code=status_code)


AUTH_COOKIE = "access_token"


def _account_json(account: accounts.Account) -> dict:
    return {"userId": account.user_id, "storeId": account.store_id, "phoneNumber": account.phone_number}


def _logged_in(deps: Container, account: accounts.Account, status_code: int) -> JSONResponse:
    response = _json(_account_json(account), status_code)
    response.set_cookie(
        AUTH_COOKIE,
        accounts.issue_token(deps, account),
        max_age=int(accounts.TOKEN_LIFETIME.total_seconds()),
        httponly=True,
        secure=True,
        samesite="none",
    )
    return response


@router.post("/auth/signup", operation_id="signUp")
async def sign_up(body: dto.CredentialsIn, deps: Deps):
    return _logged_in(deps, await accounts.sign_up(deps, body.phoneNumber, body.password), 201)


@router.post("/auth/login", operation_id="logIn")
async def log_in(body: dto.CredentialsIn, deps: Deps):
    return _logged_in(deps, await accounts.log_in(deps, body.phoneNumber, body.password), 200)


@router.post("/auth/logout", operation_id="logOut")
async def log_out():
    response = _empty()
    response.delete_cookie(AUTH_COOKIE, httponly=True, secure=True, samesite="none")
    return response


@router.get("/users/me", operation_id="getMe")
async def get_me(deps: Deps, access_token: AccessToken = None):
    user_id = accounts.verified_user_id(deps, access_token)
    return _json(_account_json(await accounts.get_account(deps, user_id)))


@router.get("/users/{userId}/store", operation_id="getUserStore")
async def get_user_store(userId: str, deps: Deps, access_token: AccessToken = None):
    if accounts.verified_user_id(deps, access_token) != userId:
        raise NotFound("user")
    return _json({"storeId": await accounts.store_id_of(deps, userId)})


def _store_input(body: dto.StoreCreateIn) -> stores.StoreCreateInput:
    hours = tuple(
        DayHours(
            day=item.day,
            closed=item.closed,
            open=parse_hhmm(item.open) if item.open else None,
            close=parse_hhmm(item.close) if item.close else None,
        )
        for item in body.businessHours
    )
    return stores.StoreCreateInput(body.name, body.category, body.address.roadAddress, hours)


@router.post("/stores", operation_id="createStore")
async def create_store(body: dto.StoreCreateIn, deps: Deps, access_token: AccessToken = None):
    account = await accounts.get_account(deps, accounts.verified_user_id(deps, access_token))
    return _json(presenters.store(await stores.create_store(deps, account.user_id, _store_input(body))), 201)


@router.put("/stores/{storeId}", operation_id="updateStore", dependencies=[OwnsStore])
async def update_store(storeId: str, body: dto.StoreCreateIn, deps: Deps):
    return _json(presenters.store(await stores.update_store(deps, storeId, _store_input(body))))


@router.put("/stores/{storeId}/push-token", operation_id="putPushToken", dependencies=[OwnsStore])
async def put_push_token(storeId: str, body: dto.PushTokenIn, deps: Deps):
    await stores.register_push_token(deps, storeId, body.token)
    return _empty()


@router.get("/stores/{storeId}/recipes", operation_id="listRecipes", dependencies=[OwnsStore])
async def list_recipes(storeId: str, deps: Deps):
    return _json(presenters.recipes(await catalog.list_recipes(deps, storeId)))


@router.put("/stores/{storeId}/recipes", operation_id="saveRecipes", dependencies=[OwnsStore])
async def save_recipes(storeId: str, body: dto.RecipeBatchIn, deps: Deps):
    replacements = [
        catalog.RecipeReplacement(item.menuId, tuple((line.ingredientId, line.quantity) for line in item.items))
        for item in body.items
    ]
    await catalog.save_recipes(deps, storeId, replacements)
    return _empty()


@router.post("/stores/{storeId}/sales-imports", operation_id="createSalesImport", dependencies=[OwnsStore])
async def create_sales_import(storeId: str, deps: Deps, file: UploadFile = File(...)):
    return _json({"id": await sales.create_import(deps, storeId, file)}, 202)


@router.get("/stores/{storeId}/sales-imports/{importId}", operation_id="getSalesImport", dependencies=[OwnsStore])
async def get_sales_import(storeId: str, importId: str, deps: Deps):
    return _json(presenters.sales_import(await sales.get_import(deps, storeId, importId)))


@router.post(
    "/stores/{storeId}/sales-imports/{importId}/confirm", operation_id="confirmSalesImport", dependencies=[OwnsStore]
)
async def confirm_sales_import(storeId: str, importId: str, deps: Deps):
    await sales.confirm_import(deps, storeId, importId)
    return _empty(202)


@router.put(
    "/stores/{storeId}/sales-imports/{importId}/column-mapping",
    operation_id="updateColumnMapping",
    dependencies=[OwnsStore],
)
async def update_column_mapping(storeId: str, importId: str, body: dto.ColumnMappingIn, deps: Deps):
    changes = body.model_dump(exclude_none=True)
    return _json(presenters.sales_import(await sales.update_column_mapping(deps, storeId, importId, changes)))


@router.get("/stores/{storeId}/home", operation_id="getHome", dependencies=[OwnsStore])
async def get_home(storeId: str, deps: Deps):
    return _json(presenters.home(await views.get_home(deps, storeId), deps.policy))


@router.put(
    "/stores/{storeId}/forecasts/{date}/menus/{menuId}", operation_id="adjustForecast", dependencies=[OwnsStore]
)
async def adjust_forecast(storeId: str, date: IsoDatePath, menuId: str, body: dto.QuantityIn, deps: Deps):
    today = deps.clock.context(storeId).local_date
    result = await views.adjust_forecast(deps, storeId, date, menuId, body.quantity)
    return _json(presenters.forecast_adjustment(result, deps.policy, date == today))


@router.put(
    "/stores/{storeId}/production/{date}/menus/{menuId}", operation_id="setProduction", dependencies=[OwnsStore]
)
async def set_production(storeId: str, date: IsoDatePath, menuId: str, body: dto.QuantityIn, deps: Deps):
    await forecasting.set_production(deps, storeId, date, menuId, body.quantity)
    return _empty()


@router.get("/stores/{storeId}/order-plans/{targetDate}", operation_id="getOrderPlan", dependencies=[OwnsStore])
async def get_order_plan(storeId: str, targetDate: IsoDatePath, deps: Deps):
    return _json(presenters.order_plan(await ordering.get_order_plan(deps, storeId, targetDate)))


@router.get("/stores/{storeId}/purchase-orders", operation_id="listPurchaseOrders", dependencies=[OwnsStore])
async def list_purchase_orders(storeId: str, deps: Deps):
    return _json(presenters.purchase_order_summaries(await ordering.list_purchase_orders(deps, storeId)))


@router.post("/stores/{storeId}/purchase-orders", operation_id="createPurchaseOrders", dependencies=[OwnsStore])
async def create_purchase_orders(storeId: str, body: dto.PurchaseOrderCreateIn, deps: Deps):
    payload = ordering.PurchaseOrderInput(
        target_date=body.targetDate,
        suppliers=tuple(
            (item.supplierId, tuple((line.ingredientId, line.quantity) for line in item.lines))
            for item in body.suppliers
        ),
    )
    return _json(presenters.purchase_orders(await ordering.create_purchase_orders(deps, storeId, payload)), 201)


@router.post(
    "/stores/{storeId}/purchase-orders/{purchaseOrderId}/send-records",
    operation_id="recordSent",
    dependencies=[OwnsStore],
)
async def record_sent(storeId: str, purchaseOrderId: str, body: dto.SendRecordIn, deps: Deps):
    await ordering.record_sent(deps, storeId, purchaseOrderId, body.channel, body.recipientPhone)
    return _empty()


@router.get("/stores/{storeId}/ingredients", operation_id="listIngredients", dependencies=[OwnsStore])
async def list_ingredients(storeId: str, deps: Deps):
    return _json(presenters.ingredient_list(await views.list_ingredients(deps, storeId)))


@router.post("/stores/{storeId}/ingredients", operation_id="createIngredient", dependencies=[OwnsStore])
async def create_ingredient(storeId: str, body: dto.IngredientCreateIn, deps: Deps):
    return _json(presenters.ingredient(await catalog.create_ingredient(deps, storeId, body.name, body.unit)), 201)


@router.post("/stores/{storeId}/purchases", operation_id="createPurchase", dependencies=[OwnsStore])
async def create_purchase(storeId: str, body: dto.ManualPurchaseIn, deps: Deps):
    lot, item = await receipts.record_manual_purchase(
        deps, storeId, body.ingredientId, body.purchasedOn, body.quantity, body.isStockReset
    )
    return _json(presenters.manual_purchase(lot, item), 201)


@router.get("/stores/{storeId}/stock", operation_id="getStock", dependencies=[OwnsStore])
async def get_stock(storeId: str, deps: Deps, asOf: Annotated[dto.IsoDate | None, Query()] = None):
    as_of, rows = await inventory.get_stock(deps, storeId, asOf)
    return _json(presenters.stock_list(as_of, rows))


@router.get("/stores/{storeId}/suppliers", operation_id="listSuppliers", dependencies=[OwnsStore])
async def list_suppliers(storeId: str, deps: Deps):
    return _json(presenters.supplier_list(await suppliers.list_with_receipt_counts(deps, storeId)))


@router.put("/stores/{storeId}/suppliers/order-days", operation_id="saveOrderDays", dependencies=[OwnsStore])
async def save_order_days(storeId: str, body: dto.OrderDaysIn, deps: Deps):
    await suppliers.save_order_days(deps, storeId, [(item.supplierId, list(item.orderDays)) for item in body.items])
    return _empty()


@router.post("/stores/{storeId}/receipt-scans", operation_id="createReceiptScan", dependencies=[OwnsStore])
async def create_receipt_scan(storeId: str, deps: Deps, images: list[UploadFile] = File(...)):
    return _json({"id": await receipts.create_scan(deps, storeId, images)}, 202)


@router.get("/stores/{storeId}/receipt-scans/{scanId}", operation_id="getReceiptScan", dependencies=[OwnsStore])
async def get_receipt_scan(storeId: str, scanId: str, deps: Deps):
    return _json(presenters.receipt_scan(await receipts.get_scan(deps, storeId, scanId)))


@router.post(
    "/stores/{storeId}/receipt-scans/{scanId}/confirm", operation_id="confirmReceiptScan", dependencies=[OwnsStore]
)
async def confirm_receipt_scan(storeId: str, scanId: str, deps: Deps, body: dto.ReceiptConfirmIn | None = None):
    answers = [(item.questionId, item.answer) for item in body.answers] if body else []
    await receipts.confirm_scan(deps, storeId, scanId, answers)
    return _empty()
