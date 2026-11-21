import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.shared.types import InternalFailure, InvalidRequest, NotFound

logger = logging.getLogger("app.api")


def _error(status_code: int, code: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"code": code}, status_code=status_code, headers=headers)


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def log_request(request: Request, call_next):
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            # 예상하지 못한 오류. 내부 내용은 로그에만 남기고 밖에는 code만 보낸다.
            logger.exception("unhandled error requestId=%s path=%s", request_id, request.url.path)
            response = _error(500, "INTERNAL_ERROR")
        route = request.scope.get("route")
        logger.info(
            "request requestId=%s operationId=%s storeId=%s status=%s ms=%d",
            request_id,
            getattr(route, "operation_id", None),
            request.path_params.get("storeId"),
            response.status_code,
            (time.monotonic() - started) * 1000,
        )
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, error: RequestValidationError):
        return _error(422, "INVALID_REQUEST")

    @app.exception_handler(InvalidRequest)
    async def on_invalid_request(request: Request, error: InvalidRequest):
        return _error(422, "INVALID_REQUEST")

    @app.exception_handler(NotFound)
    async def on_not_found(request: Request, error: NotFound):
        return _error(404, "NOT_FOUND")

    @app.exception_handler(InternalFailure)
    async def on_internal_failure(request: Request, error: InternalFailure):
        logger.error("internal failure path=%s reason=%s", request.url.path, error)
        return _error(500, "INTERNAL_ERROR")

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, error: StarletteHTTPException):
        if error.status_code == 404:
            return _error(404, "NOT_FOUND")
        if error.status_code == 405:
            return _error(405, "INVALID_REQUEST", error.headers)
        if error.status_code >= 500:
            return _error(500, "INTERNAL_ERROR")
        return _error(422, "INVALID_REQUEST")
