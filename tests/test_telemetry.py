"""OpenTelemetry (telemetry.py): spans and metrics of real tool calls, captured in memory.

The SDK's providers are global and can be set once per process, so this module installs in-memory
ones for the whole test session. They only collect; nothing is sent anywhere."""
import json
import shutil
import sqlite3

import anyio
import pytest
from fixture import at
from mcp import Client
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from poland_transit_mcp import telemetry
from poland_transit_mcp.city import City
from poland_transit_mcp.pool import CityPool
from poland_transit_mcp.server import create_server

SPANS = InMemorySpanExporter()
READER = InMemoryMetricReader()
_installed = False


@pytest.fixture(scope="module", autouse=True)
def providers():
    global _installed
    if not _installed:
        tp = TracerProvider()
        tp.add_span_processor(SimpleSpanProcessor(SPANS))
        trace.set_tracer_provider(tp)
        metrics.set_meter_provider(MeterProvider(metric_readers=[READER]))
        _installed = True


@pytest.fixture()
def fresh_cities(city_dir, tmp_path):
    """Two copies of the synthetic city, nothing loaded yet."""
    for cid in ("alpha", "beta"):
        f = tmp_path / f"{cid}.sqlite"
        shutil.copy(city_dir / "testowo.sqlite", f)
        db = sqlite3.connect(f)
        db.execute("UPDATE meta SET value = ? WHERE key = 'city'", (cid,))
        db.commit()
        db.close()
    return {cid: City.open(tmp_path, cid) for cid in ("alpha", "beta")}


def call(server, name, args):
    async def go():
        async with Client(server) as c:
            r = await c.call_tool(name, args)
            return r.content[0].text, r.is_error
    return anyio.run(go)


def metric_points(name):
    data = READER.get_metrics_data()
    out = []
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    out.extend(m.data.data_points)
    return out


def recorded_text(spans) -> str:
    """Everything a span records: names, attributes, events, status messages."""
    parts = []
    for s in spans:
        parts += [s.name, json.dumps(dict(s.attributes or {}), ensure_ascii=False, default=str),
                  s.status.description or ""]
        for e in s.events:
            parts += [e.name, json.dumps(dict(e.attributes or {}), ensure_ascii=False, default=str)]
    return "\n".join(parts)


def test_a_trip_is_one_trace_from_the_mcp_request_to_the_search(fresh_cities):
    SPANS.clear()
    server = create_server(CityPool(fresh_cities, 1000, overhead_mb=0))
    text, err = call(server, "plan_trip", {"city": "alpha", "from": at(2, 1), "to": "Ełcka",
                                           "depart_at": "2026-11-10 07:58"})
    assert not err, text
    spans = SPANS.get_finished_spans()
    by_name = {}
    for s in spans:
        by_name.setdefault(s.name, []).append(s)
    request, tool = by_name["tools/call plan_trip"][0], by_name["tool plan_trip"][0]
    assert request.attributes["gen_ai.tool.name"] == "plan_trip"  # the MCP SDK's own span
    assert tool.parent.span_id == request.context.span_id  # ours nests under it, across the worker thread
    assert tool.attributes["poland_transit.city"] == "alpha"
    assert tool.attributes["poland_transit.outcome"] == "ok"
    assert tool.attributes["poland_transit.response.bytes"] == len(text.encode())
    assert {s.attributes["poland_transit.city.part"] for s in by_name["city.load"]} >= {"graph", "timetable", "day", "routing_tables"}
    search = by_name["routing.search"][0]
    assert search.attributes["poland_transit.routing.journeys"] >= 1
    assert search.attributes["poland_transit.direction"] in ("forward", "backward")
    assert by_name["walk.search"]
    ours = [s for s in spans if s.instrumentation_scope.name == telemetry.NAME]  # not the SDK's handshake spans
    assert {s.context.trace_id for s in ours} == {request.context.trace_id}, [s.name for s in ours]  # one trace


def test_no_place_names_or_coordinates_are_recorded(fresh_cities):
    SPANS.clear()
    server = create_server(CityPool(fresh_cities, 1000, overhead_mb=0))
    start = at(2, 1)
    call(server, "plan_trip", {"city": "alpha", "from": start, "to": "Ełcka", "depart_at": "2026-11-10 07:58"})
    text, err = call(server, "plan_trip", {"city": "alpha", "from": start, "to": "Zupełnie Nieznane Miejsce",
                                           "depart_at": "2026-11-10 07:58"})
    assert err and "Nieznane" in text  # the caller still gets a readable message
    recorded = recorded_text(SPANS.get_finished_spans())
    for quoted in ("Ełcka", "Nieznane", f"{start[0]:.4f}", f"{start[1]:.4f}", str(start[0]), str(start[1])):
        assert quoted not in recorded, quoted
    rejected = [s for s in SPANS.get_finished_spans() if s.name == "tool plan_trip"][-1]
    assert rejected.attributes["poland_transit.outcome"] == "rejected"
    assert rejected.status.status_code == trace.StatusCode.ERROR


def test_tool_metrics_by_tool_city_and_outcome(fresh_cities):
    server = create_server(CityPool(fresh_cities, 1000, overhead_mb=0))
    call(server, "stops_near", {"city": "beta", "point": at(2, 1), "date": "2026-11-10"})
    call(server, "departures", {"city": "nowhere", "stop_id": "x"})
    points = {(p.attributes["poland_transit.tool"], p.attributes.get("poland_transit.city"),
               p.attributes["poland_transit.outcome"]): p for p in metric_points("poland_transit.tool.duration")}
    assert points[("stops_near", "beta", "ok")].count >= 1
    assert list(points[("stops_near", "beta", "ok")].explicit_bounds) == telemetry.CALL_BUCKETS  # seconds, not ms
    assert points[("departures", None, "rejected")].count >= 1
    loads = {(p.attributes["poland_transit.city"], p.attributes["poland_transit.city.part"])
             for p in metric_points("poland_transit.city.loads")}
    assert ("beta", "graph") in loads


def test_unloads_are_counted_and_marked_on_the_trace(fresh_cities):
    SPANS.clear()
    server = create_server(CityPool(fresh_cities, 0.000001, overhead_mb=0))  # room for one city at a time
    for cid in ("alpha", "beta"):
        text, err = call(server, "stops_near", {"city": cid, "point": at(2, 1), "date": "2026-11-10"})
        assert not err, text
    events = [e for s in SPANS.get_finished_spans() for e in s.events if e.name == "city.unload"]
    assert {e.attributes["poland_transit.city"] for e in events} >= {"alpha"}
    assert any(p.attributes["poland_transit.city"] == "alpha" for p in metric_points("poland_transit.city.unloads"))


def test_memory_gauges(fresh_cities):
    pool = CityPool(fresh_cities, 1000, overhead_mb=0)
    telemetry.observe_memory(pool)
    pool.use("alpha").graph
    pool.release()
    assert metric_points("poland_transit.memory.city_data")[0].value > 0
    assert metric_points("poland_transit.memory.budget")[0].value == pool.budget
    assert metric_points("poland_transit.cities.in_memory")[0].value == 1
    rss = metric_points("poland_transit.memory.rss")
    assert not rss or rss[0].value > 10_000_000  # Linux only


def test_setup_is_off_without_configuration(monkeypatch):
    for k in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
              "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "OTEL_TRACES_EXPORTER", "OTEL_METRICS_EXPORTER", "OTEL_SDK_DISABLED"):
        monkeypatch.delenv(k, raising=False)
    assert telemetry.setup().startswith("telemetry: off")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert telemetry.setup() == "telemetry: off (OTEL_SDK_DISABLED)"
