"""PLAN 1.E: one full order produces at most 3 log lines and at most 2 KB,
protecting the 5 GB log budget (PLAN 1.C log volume policy).
"""

from __future__ import annotations

import asyncio
import io
import logging
from types import SimpleNamespace

from app import orders

MAX_LOG_LINES = 3
MAX_LOG_BYTES = 2048


class _FakeConnection:
    def execute(self, *args, **kwargs):
        return SimpleNamespace(fetchone=lambda: None)


class _FakeEngine:
    def begin(self):
        return self

    def __enter__(self):
        return _FakeConnection()

    def __exit__(self, *exc):
        return False


class _FakeInventory:
    async def reserve(self, items):
        return {"reserved": True}


class _FakeRequest:
    items = [SimpleNamespace(sku="LAPTOP-001", quantity=2, unit_price=2500000)]
    payment_provider = "mock-payment"


def test_single_request_log_volume(span_exporter, metric_reader):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    asyncio.run(orders.process_order(_FakeRequest(), _FakeInventory(), engine=_FakeEngine()))

    raw = stream.getvalue()
    lines = [line for line in raw.splitlines() if line.strip()]

    assert 1 <= len(lines) <= MAX_LOG_LINES
    assert len(raw.encode("utf-8")) <= MAX_LOG_BYTES
