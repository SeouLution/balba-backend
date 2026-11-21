import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from app.bootstrap import Container
from app.infrastructure.db.tables import app_user
from app.shared.types import Conflict, Unauthorized, new_id

TOKEN_LIFETIME = timedelta(days=1)
JWT_HEADER = {"alg": "HS256", "typ": "JWT"}


@dataclass(frozen=True)
class Account:
    user_id: str
    store_id: str
    phone_number: str


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, signing_input: str) -> str:
    return _b64encode(hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest())


def issue_token(deps: Container, account: Account) -> str:
    claims = {"sub": account.user_id, "exp": int((deps.clock.now() + TOKEN_LIFETIME).timestamp())}
    signing_input = ".".join(_b64encode(json.dumps(part).encode()) for part in (JWT_HEADER, claims))
    return f"{signing_input}.{_sign(deps.jwt_secret, signing_input)}"


def verified_user_id(deps: Container, token: str | None) -> str:
    """HS256 고정. 헤더의 alg는 보지 않으므로 alg 바꿔치기가 통하지 않는다."""
    try:
        header, payload, signature = (token or "").split(".")
        if not hmac.compare_digest(signature, _sign(deps.jwt_secret, f"{header}.{payload}")):
            raise Unauthorized("bad signature")
        claims = json.loads(_b64decode(payload))
        if claims["exp"] <= deps.clock.now().timestamp():
            raise Unauthorized("token expired")
        return claims["sub"]
    except (ValueError, KeyError, TypeError) as error:
        raise Unauthorized("malformed token") from error


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)


def _account(row) -> Account:
    return Account(user_id=row.id, store_id=row.store_id, phone_number=row.phone_number)


async def sign_up(deps: Container, phone_number: str, password: str) -> Account:
    salt = secrets.token_bytes(16)
    account = Account(user_id=new_id("usr"), store_id=new_id("st"), phone_number=phone_number)
    try:
        async with deps.db.write() as uow:
            await uow.execute(
                insert(app_user).values(
                    id=account.user_id,
                    phone_number=phone_number,
                    password_hash=f"{salt.hex()}${_hash_password(password, salt).hex()}",
                    store_id=account.store_id,
                    created_at=deps.clock.now(),
                )
            )
    except IntegrityError as error:
        raise Conflict("phone number already registered") from error
    return account


async def log_in(deps: Container, phone_number: str, password: str) -> Account:
    async with deps.db.read() as uow:
        row = (await uow.execute(select(app_user).where(app_user.c.phone_number == phone_number))).first()
    if row is None:
        raise Unauthorized("unknown phone number")
    salt_hex, hash_hex = row.password_hash.split("$")
    if not hmac.compare_digest(_hash_password(password, bytes.fromhex(salt_hex)).hex(), hash_hex):
        raise Unauthorized("wrong password")
    return _account(row)


async def get_account(deps: Container, user_id: str) -> Account:
    async with deps.db.read() as uow:
        row = (await uow.execute(select(app_user).where(app_user.c.id == user_id))).first()
    if row is None:
        raise Unauthorized("user no longer exists")
    return _account(row)
