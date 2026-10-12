"""Contrato A5 (ronda 5, bug 4 verificado en vivo 2026-10-12).

El grupo `/otel/labs/ecs-cluster/{ServiceName}` del exporter
`awscloudwatchlogs` NUNCA llego a existir: el SDK solo escribia logs a
stdout (structlog StreamHandler) y no mandaba ninguna senal de logs al
pipeline `logs` del collector. Fix: `configure_telemetry` anade un
`LoggingHandler` OTLP al root logger (el stdout se conserva para el driver
de plataforma / filelog de GKE).

Estos tests usan SOLO exportadores in-memory (sin red, sin Docker), como el
resto de la suite offline.
"""

from __future__ import annotations

import logging
from pathlib import Path

from opentelemetry import _logs, trace
from opentelemetry.sdk._logs import LoggingHandler
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util._once import Once

from app import telemetry

REPO_ROOT = Path(__file__).resolve().parents[1]


def _reset_logs() -> None:
    """El SDK de logs solo permite setear el provider global una vez; para
    pruebas repetibles se resetea el estado de ``opentelemetry._logs._internal``
    (los nombres del modulo ``_logs`` son reexports, no el estado)."""
    from opentelemetry._logs import _internal as logs_internal

    logs_internal._LOGGER_PROVIDER = None
    logs_internal._LOGGER_PROVIDER_SET_ONCE = Once()


def _configure_with_in_memory_sinks():
    """configure_telemetry con los tres overrides in-memory (span/metric/log)."""
    _reset_logs()
    log_exporter = InMemoryLogRecordExporter()
    tracer, _meter, _logger = telemetry.configure_telemetry(
        "service-a",
        span_exporter=InMemorySpanExporter(),
        metric_reader=InMemoryMetricReader(),
        log_exporter=log_exporter,
    )
    return tracer, log_exporter


def test_log_records_reach_otlp_pipeline_with_trace_id():
    """Un log dentro de un span activo se exporta por OTLP con ese trace_id.

    Es exactamente lo que A5 necesita: el exporter awscloudwatchlogs
    serializa el LogRecord como JSON con `trace_id` top-level y el
    verificador lo empareja con la traza inyectada.
    """
    try:
        tracer, log_exporter = _configure_with_in_memory_sinks()
        with tracer.start_as_current_span("POST /orders") as span:
            logging.getLogger("service-a").info(
                "Order completed: %s", "ord-123"
            )
        _logs.get_logger_provider().force_flush()

        wanted = format(span.get_span_context().trace_id, "032x")
        records = log_exporter.get_finished_logs()
        bodies = [r.log_record.body for r in records]
        assert "Order completed: ord-123" in bodies, bodies
        match = [
            r for r in records
            if r.log_record.body == "Order completed: ord-123"
        ][0]
        assert format(match.log_record.trace_id, "032x") == wanted
        assert match.log_record.span_id != 0
    finally:
        _reset_logs()


def test_opentelemetry_internal_logs_are_not_reexported():
    """Anti feedback-loop: los fallos de transporte del propio exporter se
    loguean por stdlib; re-exportarlos alimentaria mas fallos."""
    try:
        _tracer, log_exporter = _configure_with_in_memory_sinks()
        logging.getLogger("opentelemetry.exporter.otlp.proto.grpc.exporter").warning(
            "Failed to export metrics"
        )
        _logs.get_logger_provider().force_flush()
        bodies = [r.log_record.body for r in log_exporter.get_finished_logs()]
        assert "Failed to export metrics" not in bodies
    finally:
        _reset_logs()


def test_disabled_engine_adds_no_otlp_handler(monkeypatch):
    """OTEL_ENABLED=false (modo benchmark) no crea sinks OTLP de ningun tipo."""
    monkeypatch.setenv("OTEL_ENABLED", "false")
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        _tracer, _meter, _logger = telemetry.configure_telemetry("service-a")
        assert not any(isinstance(h, LoggingHandler) for h in root.handlers)
    finally:
        root.handlers = before
        _reset_logs()


def test_enabled_engine_installs_otlp_handler_alongside_stdout(monkeypatch):
    """El sink stdout (structlog JSON) se conserva; el OTLP es un SEGUNDO
    camino, no un reemplazo (el lado GCP filelog depende del stdout)."""
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        _configure_with_in_memory_sinks()
        assert any(isinstance(h, LoggingHandler) for h in root.handlers)
        assert any(
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, LoggingHandler)
            for h in root.handlers
        )
    finally:
        root.handlers = before
        _reset_logs()


def test_service_b_telemetry_is_parity_copy():
    """service-b comparte el mismo modulo telemetry.py (fix aplica a ambos
    servicios; la prueba de paridad es por hash del archivo)."""
    a = (REPO_ROOT / "service-a" / "app" / "telemetry.py").read_bytes()
    b = (REPO_ROOT / "service-b" / "app" / "telemetry.py").read_bytes()
    assert a == b
