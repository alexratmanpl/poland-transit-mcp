"""OpenTelemetry: traces and metrics of what the server does. Off unless configured.

The MCP SDK already opens a server span per request (`tools/call plan_trip`, with
`gen_ai.tool.name`) and continues a client's trace from the request's `_meta`. This module adds
what happens inside it:

Spans (tracer `poland-transit-mcp`), nested under the SDK's span:
- `tool <name>`: city, search engine, result size, error type
- `city.load`: one part of a city (`graph`, `timetable`, `day`, `routing_tables`), with its size
- `walk.search`: one walking search (its radius)
- `routing.search`: one RAPTOR run (engine, direction, rounds, journeys found)
- `city.unload` events when the memory budget drops a city

Metrics (meter `poland-transit-mcp`):
- `poland_transit.tool.duration` (s): tool, city, engine, outcome
- `poland_transit.routing.search.duration` (s): engine, direction
- `poland_transit.city.load.duration` (s) and `poland_transit.city.loads`: city, part
- `poland_transit.city.unloads`: city
- `poland_transit.memory.rss`, `poland_transit.memory.city_data`, `poland_transit.memory.budget` (By),
  `poland_transit.cities.in_memory`

Privacy: no place names, stop names, coordinates or other query text is recorded, only cities,
tool names, counts, sizes and times. Users' trips are locations.

Turning it on (standard OpenTelemetry variables; needs the `otel` extra):
- `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` sends traces and metrics over OTLP/HTTP,
  to any collector (OpenTelemetry Collector, Jaeger, Grafana Tempo/Mimir, Honeycomb...)
- `OTEL_TRACES_EXPORTER=console` prints spans to stderr (stdout carries MCP messages over stdio)
- `OTEL_SDK_DISABLED=true` turns everything off; `OTEL_SERVICE_NAME` renames the service
"""
from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import metrics, trace

from . import __version__

NAME = "poland-transit-mcp"
tracer = trace.get_tracer(NAME, __version__)
meter = metrics.get_meter(NAME, __version__)

# Bucket bounds in seconds. The SDK's defaults (0, 5, 10, 25 ... 10000) are made for milliseconds:
# with seconds every call would land in the first bucket and no percentile could be read.
CALL_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1, 2, 5, 10]  # 300 ms and 1 s targets
SEARCH_BUCKETS = [0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1]

# instruments are proxies: they do nothing until setup() installs real providers
tool_duration = meter.create_histogram("poland_transit.tool.duration", unit="s", description="Tool call duration",
                                       explicit_bucket_boundaries_advisory=CALL_BUCKETS)
search_duration = meter.create_histogram("poland_transit.routing.search.duration", unit="s",
                                         description="One RAPTOR search",
                                         explicit_bucket_boundaries_advisory=SEARCH_BUCKETS)
load_duration = meter.create_histogram("poland_transit.city.load.duration", unit="s",
                                       description="Loading one part of a city into memory",
                                       explicit_bucket_boundaries_advisory=CALL_BUCKETS)
loads = meter.create_counter("poland_transit.city.loads", description="City parts loaded into memory")
unloads = meter.create_counter("poland_transit.city.unloads", description="Cities unloaded by the memory budget")

STATE = {"enabled": False, "exporters": ""}


def _rss_bytes() -> int | None:
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        return None


def setup(pool=None, engine_name: str = "") -> str:
    """Installs tracer and meter providers when OTEL_* configuration asks for it. Returns a one-line
    status for the start-up log. Without the SDK installed, or without configuration, it is a no-op."""
    env = os.environ
    if env.get("OTEL_SDK_DISABLED", "").lower() == "true":
        return "telemetry: off (OTEL_SDK_DISABLED)"
    otlp = any(env.get(k) for k in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                                    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"))
    trace_exp = env.get("OTEL_TRACES_EXPORTER", "otlp" if otlp else "none").lower()
    metric_exp = env.get("OTEL_METRICS_EXPORTER", "otlp" if otlp else "none").lower()
    if trace_exp == "none" and metric_exp == "none":
        return "telemetry: off (set OTEL_EXPORTER_OTLP_ENDPOINT to send traces and metrics)"
    try:
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError:
        return "telemetry: OTEL_* is set but the OpenTelemetry SDK is not installed (install the `otel` extra)"
    resource = Resource.create({"service.name": env.get("OTEL_SERVICE_NAME", NAME), "service.version": __version__,
                                "poland_transit.search_engine": engine_name})
    used = []
    if trace_exp != "none":
        provider = TracerProvider(resource=resource)
        if trace_exp == "console":
            exporter = ConsoleSpanExporter(out=sys.stderr)  # never stdout: it carries MCP messages
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        used.append(f"traces→{trace_exp}")
    if metric_exp != "none":
        if metric_exp == "console":
            mexp = ConsoleMetricExporter(out=sys.stderr)
        else:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            mexp = OTLPMetricExporter()
        interval = int(env.get("OTEL_METRIC_EXPORT_INTERVAL", "15000"))
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[
            PeriodicExportingMetricReader(mexp, export_interval_millis=interval)]))
        used.append(f"metrics→{metric_exp}")
    if pool is not None:
        observe_memory(pool)
    STATE.update(enabled=True, exporters=", ".join(used))
    endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    return f"telemetry: {STATE['exporters']}" + (f" ({endpoint})" if endpoint and otlp else "")


def observe_memory(pool) -> None:
    """Gauges read when metrics are collected: process memory and the city pool's state."""
    from opentelemetry.metrics import Observation

    def rss(_):
        v = _rss_bytes()
        return [Observation(v)] if v is not None else []

    meter.create_observable_gauge("poland_transit.memory.rss", [rss], unit="By", description="Process resident memory")
    meter.create_observable_gauge("poland_transit.memory.city_data", [lambda _: [Observation(pool.loaded_bytes())]],
                                  unit="By", description="Estimated memory of loaded cities")
    meter.create_observable_gauge("poland_transit.memory.budget", [lambda _: [Observation(pool.budget)]],
                                  unit="By", description="Memory allowed for city data")
    meter.create_observable_gauge("poland_transit.cities.in_memory",
                                  [lambda _: [Observation(sum(1 for c in pool.values() if c.heavy_bytes()))]],
                                  description="Cities with their walking graph or timetable loaded")


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """A span that never records exception messages (they can quote the places asked about)."""
    with tracer.start_as_current_span(name, attributes={k: v for k, v in attributes.items() if v is not None},
                                      record_exception=False, set_status_on_exception=False) as s:
        try:
            yield s
        except Exception as e:
            s.set_attribute("error.type", type(e).__qualname__)
            s.set_status(trace.StatusCode.ERROR)
            raise


@contextmanager
def city_load(city_id: str, part: str) -> Iterator[trace.Span]:
    """A span and the load metrics around loading one part of a city."""
    t = time.perf_counter()
    with span("city.load", **{"poland_transit.city": city_id, "poland_transit.city.part": part}) as s:
        yield s
    attrs = {"poland_transit.city": city_id, "poland_transit.city.part": part}
    load_duration.record(time.perf_counter() - t, attrs)
    loads.add(1, attrs)
