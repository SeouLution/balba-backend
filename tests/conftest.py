import subprocess
import sys
from datetime import datetime
from os import environ, getenv
from pathlib import Path

import httpx
import pytest
import yaml
from jsonschema import Draft202012Validator
from sqlalchemy import text

from app.bootstrap import Container
from app.infrastructure.db.tables import metadata
from app.infrastructure.db.uow import Database
from app.infrastructure.files.store import FileStore
from app.infrastructure.jobs.loop import JobRunner
from app.main import create_app
from app.shared.clock import Clock
from app.shared.policy import load_policy
from tests.fakes import fake_providers

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/balba_test"
DATABASE_URL = getenv("TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
SPEC = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
OPERATIONS = {
    operation["operationId"]: (method.upper(), path, operation)
    for path, item in SPEC["paths"].items()
    for method, operation in item.items()
    if method in ("get", "put", "post", "delete", "patch")
}


def kst(value: str) -> datetime:
    return datetime.fromisoformat(value + "+09:00")


@pytest.fixture(scope="session")
def migrated_database():
    variables = dict(environ)
    variables["DATABASE_URL"] = DATABASE_URL
    for command in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run([sys.executable, "-m", "alembic", *command], cwd=ROOT, env=variables, check=True)
    return DATABASE_URL


@pytest.fixture
async def deps(migrated_database, tmp_path):
    database = Database(migrated_database)
    async with database.write() as uow:
        tables = ", ".join(table.name for table in metadata.sorted_tables)
        await uow.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    container = Container(
        db=database,
        clock=Clock(kst("2026-11-10T20:30:00")),
        policy=load_policy(),
        files=FileStore(tmp_path / "uploads"),
        providers=fake_providers(),
        jwt_secret="test-secret",
        scheduler_enabled=False,
    )
    yield container
    await database.dispose()


@pytest.fixture
def runner(deps):
    return JobRunner(deps, retry_delays=(0, 0), postpone_seconds=3600)


def response_schema(operation_id: str, status_code: int) -> dict | None:
    responses = OPERATIONS[operation_id][2]["responses"]
    declared = responses.get(str(status_code))
    if declared is None:
        declared = SPEC["components"]["responses"]["Error"]
    content = declared.get("content")
    if content is None:
        return None
    return {"components": SPEC["components"], **content["application/json"]["schema"]}


class Api:
    """openapi.yaml의 operationId로 호출하고, 모든 응답을 그 명세의 스키마로 검증한다."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.signups = 0
        self.store_cookies: dict[str, dict] = {}

    async def new_account_cookie(self) -> dict:
        self.signups += 1
        credentials = {"phoneNumber": f"01090{self.signups:06d}", "password": "password123"}
        signed_up = await self.call("signUp", 201, json=credentials)
        return {"cookie": f"access_token={signed_up.cookies['access_token']}"}

    async def call(self, operation_id: str, expect: int, **kwargs) -> httpx.Response:
        new_owner = operation_id == "createStore" and "headers" not in kwargs
        if new_owner:
            kwargs["headers"] = await self.new_account_cookie()
        method, path, _ = OPERATIONS[operation_id]
        path_params = {key: kwargs.pop(key) for key in list(kwargs) if "{" + key + "}" in path}
        if "storeId" in path_params and "headers" not in kwargs:
            kwargs["headers"] = self.store_cookies.get(path_params["storeId"], {})
        response = await self.client.request(method, path.format(**path_params), **kwargs)
        if operation_id == "createStore" and response.status_code == 201:
            self.store_cookies[response.json()["id"]] = kwargs["headers"]
            if new_owner:
                self.client.headers.update(kwargs["headers"])
        assert response.status_code == expect, f"{operation_id}: {response.status_code} {response.text}"
        schema = response_schema(operation_id, response.status_code)
        if schema is None:
            assert response.content == b"", f"{operation_id}: body must be empty"
        else:
            Draft202012Validator(schema).validate(response.json())
            if response.status_code >= 400:
                assert list(response.json()) == ["code"]
        return response


@pytest.fixture
async def api(deps):
    app = create_app(deps, run_jobs=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/v1") as client:
        yield Api(client)
