import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.infrastructure.db.uow import Database
from app.infrastructure.files.store import FileStore
from app.infrastructure.providers.ports import Providers
from app.shared.clock import Clock
from app.shared.policy import Policy, load_policy

REQUIRED_ENV = (
    "DATABASE_URL",
    "FILE_STORAGE_ROOT",
    "ANTHROPIC_API_KEY",
    "KAKAO_REST_API_KEY",
    "KMA_SERVICE_KEY",
    "SEOUL_OPENAPI_KEY",
    "VAPID_PRIVATE_KEY",
    "VAPID_PUBLIC_KEY",
    "VAPID_SUBJECT",
    "JWT_SECRET",
)


@dataclass
class Container:
    """유스케이스와 작업 handler가 쓰는 의존 묶음. 테스트는 clock과 providers를 fake로 바꾼다."""

    db: Database
    clock: Clock
    policy: Policy
    files: FileStore
    providers: Providers
    jwt_secret: str
    scheduler_enabled: bool = True


def container_from_env() -> Container:
    env = os.environ
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing:
        raise RuntimeError(f"missing required environment variables: {', '.join(missing)}")
    demo_now = env.get("DEMO_NOW")
    from app.infrastructure.providers.real import real_providers

    policy = load_policy()
    return Container(
        db=Database(env["DATABASE_URL"]),
        clock=Clock(datetime.fromisoformat(demo_now) if demo_now else None),
        policy=policy,
        files=FileStore(Path(env["FILE_STORAGE_ROOT"])),
        providers=real_providers(env, policy.observation_station),
        jwt_secret=env["JWT_SECRET"],
        scheduler_enabled=not demo_now,
    )
