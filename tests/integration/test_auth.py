from datetime import timedelta

import pytest

from tests import helpers

CREDENTIALS = {"phoneNumber": "01012345678", "password": "password123"}


def auth_header(response) -> dict:
    return {"cookie": f"access_token={response.cookies['access_token']}"}


async def test_signup_login_me_logout(api, deps):
    signed_up = await api.call("signUp", 201, json=CREDENTIALS)
    body = signed_up.json()
    assert body["storeId"] is None and body["phoneNumber"] == "01012345678"
    set_cookie = signed_up.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "max-age=86400" in set_cookie

    logged_in = await api.call("logIn", 200, json=CREDENTIALS)
    assert logged_in.json() == body
    me = await api.call("getMe", 200, headers=auth_header(logged_in))
    assert me.json() == body

    logged_out = await api.call("logOut", 204)
    assert "max-age=0" in logged_out.headers["set-cookie"].lower()

    deps.clock.fixed += timedelta(days=1)
    await api.call("getMe", 401, headers=auth_header(logged_in))


async def test_duplicate_phone_is_conflict(api):
    await api.call("signUp", 201, json=CREDENTIALS)
    response = await api.call("signUp", 409, json={**CREDENTIALS, "password": "different123"})
    assert response.json() == {"code": "CONFLICT"}


@pytest.mark.parametrize(
    "body",
    [
        {"phoneNumber": "010-1234-5678", "password": "password123"},
        {"phoneNumber": "01012345678", "password": "abc"},
        {"phoneNumber": "01012345678"},
    ],
)
async def test_invalid_signup_is_rejected(api, body):
    await api.call("signUp", 422, json=body)


async def test_wrong_credentials_and_bad_tokens_are_unauthorized(api):
    signed_up = await api.call("signUp", 201, json=CREDENTIALS)
    await api.call("logIn", 401, json={**CREDENTIALS, "password": "wrongpass1"})
    await api.call("logIn", 401, json={**CREDENTIALS, "phoneNumber": "01099999999"})
    await api.call("getMe", 401)
    await api.call("getMe", 401, headers={"cookie": "access_token=garbage"})
    header, payload, _ = signed_up.cookies["access_token"].split(".")
    await api.call("getMe", 401, headers={"cookie": f"access_token={header}.{payload}.forged"})


async def test_create_store_issues_store_id(api):
    signed_up = await api.call("signUp", 201, json=CREDENTIALS)
    user_id = signed_up.json()["userId"]
    await api.call("getUserStore", 401, userId=user_id)
    owner = auth_header(signed_up)
    assert (await api.call("getUserStore", 200, userId=user_id, headers=owner)).json() == {"storeId": None}
    created = await api.call("createStore", 201, json=helpers.store_body(), headers=auth_header(signed_up))
    store_id = created.json()["id"]
    assert (await api.call("logIn", 200, json=CREDENTIALS)).json()["storeId"] == store_id
    assert (await api.call("getUserStore", 200, userId=user_id, headers=owner)).json() == {"storeId": store_id}
    await api.call("getUserStore", 404, userId="usr_other", headers=owner)
    await api.call("getHome", 200, storeId=store_id, headers=owner)
    await api.call("getHome", 404, storeId=store_id, headers=await api.new_account_cookie())
    await api.call("getHome", 401, storeId=store_id, headers={})
    await api.call("createStore", 409, json=helpers.store_body(), headers=auth_header(signed_up))
    await api.call("createStore", 401, json=helpers.store_body(), headers={})
