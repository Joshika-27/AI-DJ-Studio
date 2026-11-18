"""Module 1 automated tests: Backend foundation, environment config, structured logging, CORS, error handling, and health checks."""

import json
import logging
import time

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
import pytest

from app.core.config import Settings
from app.core.exceptions import AppException
from app.core.logging import (
    StructuredJsonFormatter,
    clear_log_context,
    sanitize_log_payload,
    set_log_context,
)
from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    test_settings = Settings(
        APP_NAME="AI DJ Studio Test",
        APP_ENV="testing",
        APP_VERSION="1.0.0",
        LOG_LEVEL="INFO",
        CORS_ORIGINS="http://localhost:5173,http://localhost:3000",
    )
    app = create_app(settings_override=test_settings)

    class SampleInput(BaseModel):
        bpm: float = Field(gt=0)

    @app.get("/api/v1/test-domain-error")
    async def trigger_domain_error() -> None:
        raise AppException(
            message="Simulated domain error",
            status_code=409,
            code="ROOM_FULL",
            details={"maxParticipants": 50},
        )

    @app.get("/api/v1/lounges/{lounge_id}/test-http-error")
    async def trigger_http_error(lounge_id: str) -> None:
        raise HTTPException(status_code=403, detail=f"Forbidden in lounge {lounge_id}")

    @app.post("/api/v1/test-validation")
    async def trigger_validation_error(payload: SampleInput) -> dict:
        return {"bpm": payload.bpm}

    return TestClient(app, raise_server_exceptions=False)


def test_health_endpoint_returns_200_and_fast_latency(client: TestClient) -> None:
    """Verify GET /api/v1/health responds with 200 OK and sub-50ms latency (REQ-074, REQ-079)."""
    start = time.perf_counter()
    response = client.get("/api/v1/health")
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    assert response.status_code == 200
    assert elapsed_ms < 200.0
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "ai-dj-studio"
    assert data["version"] == "1.0.0"
    assert data["wsGateway"] == "ready"
    assert isinstance(data["uptimeSeconds"], (int, float))
    assert data["correlationId"].startswith("corr_")
    assert response.headers["X-Correlation-ID"] == data["correlationId"]
    assert "X-Process-Time-Ms" in response.headers


def test_ws_gateway_health_endpoint(client: TestClient) -> None:
    """Verify GET /api/v1/health/ws reports gateway readiness (REQ-079)."""
    response = client.get("/api/v1/health/ws")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["gateway"] == "ws_queue_state_gateway"
    assert data["wsGateway"] == "ready"


def test_correlation_id_and_lounge_id_propagation(client: TestClient) -> None:
    """Verify custom X-Correlation-ID and X-Lounge-ID headers propagate into response and error payloads (REQ-077)."""
    response = client.get(
        "/api/v1/health",
        headers={
            "X-Correlation-ID": "corr_custom_991",
            "X-Lounge-ID": "lg_77120",
        },
    )
    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "corr_custom_991"
    assert response.headers["X-Lounge-ID"] == "lg_77120"
    assert response.json()["correlationId"] == "corr_custom_991"


def test_cors_preflight_configuration(client: TestClient) -> None:
    """Verify CORS preflight allows configured frontend origins and actual requests expose correlation headers."""
    preflight = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization,Content-Type,X-Correlation-ID",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "GET" in preflight.headers.get("access-control-allow-methods", "")

    actual = client.get(
        "/api/v1/health",
        headers={"Origin": "http://localhost:5173"},
    )
    assert actual.status_code == 200
    assert actual.headers.get("access-control-allow-origin") == "http://localhost:5173"
    exposed = actual.headers.get("access-control-expose-headers", "")
    assert "X-Correlation-ID" in exposed
    assert "X-Lounge-ID" in exposed
    assert "X-Process-Time-Ms" in exposed


def test_centralized_error_handling_for_domain_http_and_validation(client: TestClient) -> None:
    """Verify AppException, HTTPException, and RequestValidationError return unified JSON envelopes."""
    # 1. Domain AppException
    res_domain = client.get(
        "/api/v1/test-domain-error",
        headers={"X-Correlation-ID": "corr_err_1", "X-Lounge-ID": "lg_77120"},
    )
    assert res_domain.status_code == 409
    body_domain = res_domain.json()
    assert body_domain["error"]["code"] == "ROOM_FULL"
    assert body_domain["error"]["details"] == {"maxParticipants": 50}
    assert body_domain["correlationId"] == "corr_err_1"
    assert body_domain["loungeId"] == "lg_77120"

    # 2. HTTPException with path-extracted loungeId
    res_http = client.get("/api/v1/lounges/lg_88200/test-http-error")
    assert res_http.status_code == 403
    body_http = res_http.json()
    assert body_http["error"]["code"] == "FORBIDDEN"
    assert body_http["loungeId"] == "lg_88200"

    # 3. RequestValidationError (BPM <= 0)
    res_val = client.post("/api/v1/test-validation", json={"bpm": -10})
    assert res_val.status_code == 400
    body_val = res_val.json()
    assert body_val["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(body_val["error"]["details"], list)


def test_structured_json_formatter_and_sensitive_redaction() -> None:
    """Verify structured JSON log formatting and automatic redaction of sensitive keys (REQ-073, REQ-077)."""
    try:
        set_log_context(correlation_id="corr_log_42", lounge_id="lg_77120")
        formatter = StructuredJsonFormatter()
        record = logging.LogRecord(
            name="aidj_studio.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="Queue vote processed",
            args=(),
            exc_info=None,
        )
        record.event = "lounge.queue.vote"
        record.payload = {
            "trackId": "t_2003",
            "password": "my_plain_password",
            "accessToken": "eyJhbGciOiJIUzI1Ni...",
            "nested": {"jwt_secret_key": "top_secret", "bpm": 124},
        }
        formatted = formatter.format(record)
        parsed = json.loads(formatted)

        assert parsed["correlationId"] == "corr_log_42"
        assert parsed["loungeId"] == "lg_77120"
        assert parsed["event"] == "lounge.queue.vote"
        assert parsed["payload"]["trackId"] == "t_2003"
        assert parsed["payload"]["password"] == "[REDACTED]"
        assert parsed["payload"]["accessToken"] == "[REDACTED]"
        assert parsed["payload"]["nested"]["jwt_secret_key"] == "[REDACTED]"
        assert parsed["payload"]["nested"]["bpm"] == 124
    finally:
        clear_log_context()


def test_settings_environment_loading_and_no_hardcoded_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Settings loads from environment variables and has no hardcoded secret defaults (REQ-073)."""
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("JWT_REFRESH_SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    default_settings = Settings(_env_file=None)
    assert default_settings.JWT_SECRET_KEY is None
    assert default_settings.JWT_REFRESH_SECRET_KEY is None
    assert default_settings.DATABASE_URL is None
    assert default_settings.LLM_API_KEY is None

    monkeypatch.setenv("APP_PORT", "9001")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173 , https://aidj.local ")
    env_settings = Settings(_env_file=None)
    assert env_settings.APP_PORT == 9001
    assert env_settings.LOG_LEVEL == "DEBUG"
    assert env_settings.cors_origins_list == ["http://localhost:5173", "https://aidj.local"]

    with pytest.raises(ValueError):
        Settings(_env_file=None, LOG_LEVEL="INVALID_LEVEL")

    assert sanitize_log_payload([{"token": "abc"}, {"safe": 1}]) == [
        {"token": "[REDACTED]"},
        {"safe": 1},
    ]
