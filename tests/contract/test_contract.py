"""openapi.yaml과 런타임 라우트, 공통 오류 계약."""

import re

import pytest

from app.main import create_app
from tests import helpers
from tests.conftest import OPERATIONS
from tests.fakes import JPEG_HEAD

QUANTITY = {"json": {"quantity": 1}}
MINIMAL_REQUESTS = {
    "putPushToken": {"json": {"token": helpers.PUSH_TOKEN}},
    "saveRecipes": {"json": {"items": [{"menuId": "mn_x", "items": []}]}},
    "createSalesImport": {"files": {"file": ("sales.csv", b"a,b\n1,2\n", "text/csv")}},
    "adjustForecast": QUANTITY,
    "setProduction": QUANTITY,
    "createPurchaseOrders": {
        "json": {"targetDate": "2026-11-11", "suppliers": [{"supplierId": "sup_x", "lines": []}]},
    },
    "recordSent": {"json": {"channel": "COPY"}},
    "saveOrderDays": {"json": {"items": []}},
    "createReceiptScan": {"files": [("images", ("1.jpg", JPEG_HEAD + b"{}", "image/jpeg"))]},
}
PATH_VALUES = {
    "storeId": "st_missing",
    "importId": "imp_missing",
    "scanId": "scan_missing",
    "menuId": "mn_missing",
    "purchaseOrderId": "po_missing",
    "date": "2026-11-11",
    "targetDate": "2026-11-11",
}


def path_kwargs(operation_id: str, **overrides) -> dict:
    names = re.findall(r"{(\w+)}", OPERATIONS[operation_id][1])
    return {name: overrides.get(name, PATH_VALUES[name]) for name in names}


def test_runtime_routes_match_openapi_one_to_one():
    app = create_app()
    runtime = {
        (method.upper(), path, operation["operationId"])
        for path, item in app.openapi()["paths"].items()
        if path.startswith("/v1")
        for method, operation in item.items()
    }
    declared = {(method, "/v1" + path, operation_id) for operation_id, (method, path, _) in OPERATIONS.items()}
    assert len(declared) == 25
    assert runtime == declared


@pytest.mark.parametrize("operation_id", sorted(op for op in OPERATIONS if "{storeId}" in OPERATIONS[op][1]))
async def test_store_routes_need_owner_cookie(api, operation_id):
    request = {**path_kwargs(operation_id), **MINIMAL_REQUESTS.get(operation_id, {})}
    await api.call(operation_id, 401, headers={}, **request)
    await api.call(operation_id, 404, headers=await api.new_account_cookie(), **request)


@pytest.mark.parametrize(
    "operation_id",
    [
        "getSalesImport",
        "confirmSalesImport",
        "getReceiptScan",
        "confirmReceiptScan",
        "adjustForecast",
        "setProduction",
        "recordSent",
    ],
)
async def test_child_resource_of_another_store_is_not_found(api, operation_id):
    store_id = (await api.call("createStore", 201, json=helpers.store_body())).json()["id"]
    kwargs = path_kwargs(operation_id, storeId=store_id)
    await api.call(operation_id, 404, **kwargs, **MINIMAL_REQUESTS.get(operation_id, {}))


async def test_errors_expose_only_a_code(api):
    store_id = (await api.call("createStore", 201, json=helpers.store_body())).json()["id"]
    malformed = await api.client.put(
        f"/stores/{store_id}/push-token", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert (malformed.status_code, malformed.json()) == (422, {"code": "INVALID_REQUEST"})
    missing = await api.client.put(f"/stores/{store_id}/push-token", json={})
    assert (missing.status_code, missing.json()) == (422, {"code": "INVALID_REQUEST"})
    unknown = await api.client.get("/nowhere")
    assert (unknown.status_code, unknown.json()) == (404, {"code": "NOT_FOUND"})
    not_allowed = await api.client.delete(f"/stores/{store_id}/home")
    assert (not_allowed.status_code, not_allowed.json()) == (405, {"code": "INVALID_REQUEST"})
    assert not_allowed.headers["allow"] == "GET"
    bad_date = await api.client.get(f"/stores/{store_id}/order-plans/20261111")
    assert (bad_date.status_code, bad_date.json()) == (422, {"code": "INVALID_REQUEST"})


async def test_unexpected_failure_is_internal_error(api, deps, monkeypatch):
    store_id = (await api.call("createStore", 201, json=helpers.store_body())).json()["id"]

    def broken_read():
        raise RuntimeError("database password=secret is unreachable")

    monkeypatch.setattr(deps.db, "read", broken_read)
    response = await api.call("getHome", 500, storeId=store_id)
    assert response.json() == {"code": "INTERNAL_ERROR"}
    assert "secret" not in response.text


@pytest.mark.parametrize("quantity", [True, "3", -1, None])
async def test_quantity_must_be_a_non_negative_number(api, quantity):
    store_id = (await api.call("createStore", 201, json=helpers.store_body())).json()["id"]
    await api.call(
        "adjustForecast", 422, storeId=store_id, date="2026-11-11", menuId="mn_x", json={"quantity": quantity}
    )


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
async def test_non_finite_quantity_is_rejected(api, literal):
    store_id = (await api.call("createStore", 201, json=helpers.store_body())).json()["id"]
    response = await api.client.put(
        f"/stores/{store_id}/forecasts/2026-11-11/menus/mn_x",
        content=f'{{"quantity": {literal}}}'.encode(),
        headers={"content-type": "application/json"},
    )
    assert (response.status_code, response.json()) == (422, {"code": "INVALID_REQUEST"})


async def test_unknown_json_keys_are_ignored(api):
    body = {**helpers.store_body(), "futureField": {"anything": 1}}
    await api.call("createStore", 201, json=body)
