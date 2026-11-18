"""Centralized exception hierarchy and FastAPI exception handlers."""

from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_correlation_id, get_lounge_id, get_logger

logger = get_logger("exceptions")


class AppException(Exception):
    """Domain-level application exception with structured error code and HTTP status."""

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        code: str = "BAD_REQUEST",
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details


def _build_error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
) -> JSONResponse:
    correlation_id = get_correlation_id()
    lounge_id = get_lounge_id()
    body: Dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "details": details,
        },
        "correlationId": correlation_id,
        "loungeId": lounge_id,
    }
    return JSONResponse(
        status_code=status_code,
        content=body,
        headers={"X-Correlation-ID": correlation_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach centralized exception handlers to the FastAPI instance."""

    @app.exception_handler(AppException)
    async def handle_app_exception(request: Request, exc: AppException) -> JSONResponse:
        logger.warning(
            exc.message,
            extra={
                "event": "http.app_exception",
                "payload": {
                    "path": request.url.path,
                    "method": request.method,
                    "code": exc.code,
                    "statusCode": exc.status_code,
                },
            },
        )
        return _build_error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        code_map = {
            status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
            status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
            status.HTTP_403_FORBIDDEN: "FORBIDDEN",
            status.HTTP_404_NOT_FOUND: "NOT_FOUND",
            status.HTTP_409_CONFLICT: "CONFLICT",
            422: "UNPROCESSABLE_ENTITY",
        }
        code = code_map.get(exc.status_code, f"HTTP_{exc.status_code}")
        message = exc.detail if isinstance(exc.detail, str) else "HTTP error occurred"
        details = exc.detail if not isinstance(exc.detail, str) else None
        logger.warning(
            message,
            extra={
                "event": "http.exception",
                "payload": {
                    "path": request.url.path,
                    "method": request.method,
                    "statusCode": exc.status_code,
                },
            },
        )
        return _build_error_response(
            status_code=exc.status_code,
            code=code,
            message=message,
            details=details,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        sanitized_errors = [
            {
                "loc": list(err.get("loc", [])),
                "msg": err.get("msg", "Invalid value"),
                "type": err.get("type", "value_error"),
            }
            for err in exc.errors()
        ]
        logger.warning(
            "Request validation failed",
            extra={
                "event": "http.validation_error",
                "payload": {
                    "path": request.url.path,
                    "method": request.method,
                    "errors": sanitized_errors,
                },
            },
        )
        return _build_error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="VALIDATION_ERROR",
            message="Request payload validation failed.",
            details=sanitized_errors,
        )

    @app.exception_handler(Exception)
    async def handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Unhandled server exception",
            exc_info=exc,
            extra={
                "event": "http.unhandled_exception",
                "payload": {
                    "path": request.url.path,
                    "method": request.method,
                },
            },
        )
        return _build_error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal server error occurred.",
        )
