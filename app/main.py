import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from scalar_fastapi import get_scalar_api_reference

from app.api import errors
from app.api.routes import router
from app.bootstrap import Container, container_from_env
from app.infrastructure.jobs.loop import JobRunner

OPENAPI_SPEC_PATH = Path(__file__).resolve().parents[1] / "openapi.yaml"


def create_app(container: Container | None = None, run_jobs: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 필수 환경변수가 없으면 여기서 실패해 서버가 뜨지 않는다.
        app.state.container = container or container_from_env()
        runner = JobRunner(app.state.container)
        loop_task = None
        if run_jobs:
            await runner.recover()
            loop_task = asyncio.create_task(runner.run_forever())
        yield
        if loop_task is not None:
            runner.stop()
            await loop_task
        if container is None:
            await app.state.container.db.dispose()

    app = FastAPI(title="balba-backend", lifespan=lifespan)
    app.state.container = container
    errors.install(app)

    origins = [origin.strip() for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if origin.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/openapi.yaml", include_in_schema=False)
    def openapi_spec() -> FileResponse:
        return FileResponse(OPENAPI_SPEC_PATH, media_type="application/yaml")

    @app.get("/scalar", include_in_schema=False)
    def scalar_reference() -> HTMLResponse:
        return get_scalar_api_reference(openapi_url="/openapi.yaml", title="SeouLution API")

    @app.get("/health")
    def health() -> dict[str, str]:
        # 배포 스크립트와 워크플로가 이 응답으로 기동 여부와 배포된 버전을 확인한다.
        return {"status": "ok", "version": os.getenv("GIT_SHA", "dev")}

    app.include_router(router)
    return app


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = create_app()
