"""Shared pytest fixtures for the offline OTel test suite (PLAN 1.E).

All tests run with no network and no Docker: spans are collected with an
in-memory exporter and metrics with an in-memory reader. The OTel global
providers are reset around every test because the SDK allows setting them only
once per process.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util._once import Once

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_A_DIR = REPO_ROOT / "service-a"
SERVICE_B_DIR = REPO_ROOT / "service-b"

if str(SERVICE_A_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_A_DIR))

# Modules that cache a tracer created before any provider was set. Their
# ``tracer`` is a ProxyTracer that caches the first real tracer it resolves, so
# it must be refreshed whenever a new provider is installed.
_TRACER_MODULES = ("app.orders", "app.payment", "app.persistence", "app.service")


def _reset_providers() -> None:
    """Reset the global tracer/meter providers and the metrics singleton."""
    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    metrics._internal._METER_PROVIDER = None
    metrics._internal._METER_PROVIDER_SET_ONCE = Once()

    metrics_module = sys.modules.get("app.metrics")
    if metrics_module is not None:
        metrics_module._metrics = None


def _rebind_module_tracers() -> None:
    """Rebind app modules to a real tracer from the current provider.

    Modules create their tracer at import time (a ProxyTracer). Once a real
    provider is installed, ``trace.get_tracer`` returns a concrete tracer, so
    replacing the module attribute keeps every custom span flowing to the
    provider installed by the fixture.
    """
    for name in _TRACER_MODULES:
        module = sys.modules.get(name)
        if module is None:
            continue
        module.tracer = trace.get_tracer("service-a")


@pytest.fixture(autouse=True)
def reset_otel_state():
    """Reset global OTel state before and after every test."""
    _reset_providers()
    yield
    _reset_providers()


@pytest.fixture
def span_exporter(reset_otel_state):
    """Install a TracerProvider exporting to an in-memory exporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _rebind_module_tracers()
    return exporter


@pytest.fixture
def metric_reader(reset_otel_state):
    """Install a MeterProvider backed by an in-memory reader."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    return reader


@pytest.fixture
def otel(span_exporter, metric_reader):
    """Both exporters, for tests that need spans and metrics together."""
    return SimpleNamespace(span_exporter=span_exporter, metric_reader=metric_reader)
