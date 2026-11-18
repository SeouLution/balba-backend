import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from scalar_fastapi import get_scalar_api_reference

app = FastAPI(title="balba-backend")
OPENAPI_SPEC_PATH = Path(__file__).resolve().parents[1] / "openapi.yaml"

_origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
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
