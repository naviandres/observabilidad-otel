"""PLAN 1.E: with ``OTEL_ENABLED=false`` the engine creates no exporter and no
span is recorded. The same module is reused by service-b, so its engine is
loaded by path to prove parity without importing the ``app`` package twice.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app import telemetry

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_flag_parses_common_truthy_and_falsy_values(monkeypatch):
    for value in ("false", "FALSE", "0", "no", "off"):
        monkeypatch.setenv("OTEL_ENABLED", value)
        assert telemetry.is_otel_enabled() is False

    for value in ("true", "TRUE", "1", "yes", "on"):
        monkeypatch.setenv("OTEL_ENABLED", value)
        assert telemetry.is_otel_enabled() is True


def test_disabled_engine_records_no_spans(monkeypatch):
    monkeypatch.setenv("OTEL_ENABLED", "false")

    tracer, _meter, _logger = telemetry.configure_telemetry("service-a")

    with tracer.start_as_current_span("should-not-be-recorded") as span:
        recorded = span.is_recording()
        context = span.get_span_context()

    assert recorded is False
    assert context.is_valid is False


def test_service_b_engine_loads_and_is_disabled(monkeypatch):
    monkeypatch.setenv("OTEL_ENABLED", "false")

    path = REPO_ROOT / "service-b" / "app" / "telemetry.py"
    spec = importlib.util.spec_from_file_location("service_b_telemetry", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["service_b_telemetry"] = module
    try:
        spec.loader.exec_module(module)
        tracer, _meter, _logger = module.configure_telemetry("service-b")

        with tracer.start_as_current_span("noop") as span:
            assert span.is_recording() is False
    finally:
        sys.modules.pop("service_b_telemetry", None)
