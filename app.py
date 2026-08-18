"""Self-referential OTel receiver + dashboard for Posit Connect.

Deploy this FastAPI app to the same Connect that emits the telemetry, then point
Connect's collector back at it:

    [OpenTelemetry]
    Enabled = true

    [OTLPEndpoint "self"]
    Endpoint = "https://<connect-host>/content/<this-content-guid>"
    Logs = true
    Traces = true
    Metrics = true

Connect POSTs OTLP/HTTP protobuf to /v1/{metrics,logs,traces}; this app decodes,
stores in DuckDB, and renders a dashboard at /.

Because Connect's [OTLPEndpoint] cannot attach an auth header, the /v1/* routes
must be reachable without authentication — set this content's access to
"Anyone" (anonymous). Pin Min=Max=1 process and disable the idle timeout so the
receiver is always warm and single-writer. See README.md.
"""

from __future__ import annotations

import gzip
import logging

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from google.protobuf import json_format
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2

import otlp
from dashboard import DASHBOARD_HTML
from store import Store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("otel-receiver")

app = FastAPI(title="Connect OTel Receiver + Dashboard")
store = Store()

PROTOBUF = "application/x-protobuf"
JSON = "application/json"


async def _read_body(request: Request) -> tuple[bytes, str]:
    """Return (decompressed bytes, content-type)."""
    raw = await request.body()
    enc = request.headers.get("content-encoding", "").lower()
    if "gzip" in enc:
        raw = gzip.decompress(raw)
    ctype = request.headers.get("content-type", PROTOBUF).split(";")[0].strip().lower()
    return raw, ctype


def _ok_response(ctype: str, empty_proto) -> Response:
    """OTLP wants an (empty) Export*ServiceResponse echoed back in kind."""
    if ctype == JSON:
        return JSONResponse(content=json_format.MessageToDict(empty_proto))
    return Response(content=empty_proto.SerializeToString(), media_type=PROTOBUF)


# --------------------------------------------------------------------------- #
# OTLP/HTTP receivers
# --------------------------------------------------------------------------- #
@app.post("/v1/metrics")
async def ingest_metrics(request: Request):
    body, ctype = await _read_body(request)
    if ctype == JSON:
        req = metrics_service_pb2.ExportMetricsServiceRequest()
        json_format.Parse(body, req)
        body = req.SerializeToString()
    rows = otlp.decode_metrics(body)
    n = store.insert_metrics(rows)
    log.info("metrics: %d data points", n)
    return _ok_response(ctype, metrics_service_pb2.ExportMetricsServiceResponse())


@app.post("/v1/logs")
async def ingest_logs(request: Request):
    body, ctype = await _read_body(request)
    if ctype == JSON:
        req = logs_service_pb2.ExportLogsServiceRequest()
        json_format.Parse(body, req)
        body = req.SerializeToString()
    rows = otlp.decode_logs(body)
    n = store.insert_logs(rows)
    log.info("logs: %d records", n)
    return _ok_response(ctype, logs_service_pb2.ExportLogsServiceResponse())


@app.post("/v1/traces")
async def ingest_traces(request: Request):
    body, ctype = await _read_body(request)
    if ctype == JSON:
        req = trace_service_pb2.ExportTraceServiceRequest()
        json_format.Parse(body, req)
        body = req.SerializeToString()
    rows = otlp.decode_traces(body)
    n = store.insert_spans(rows)
    log.info("traces: %d spans", n)
    return _ok_response(ctype, trace_service_pb2.ExportTraceServiceResponse())


# --------------------------------------------------------------------------- #
# Dashboard JSON API
# --------------------------------------------------------------------------- #
@app.get("/api/summary")
def api_summary():
    store.sweep_retention()
    return store.summary()


@app.get("/api/metrics/names")
def api_metric_names():
    return store.metric_names()


@app.get("/api/metrics/timeseries")
def api_metric_timeseries(name: str, minutes: int = 180):
    return store.metric_timeseries(name, minutes)


@app.get("/api/logs")
def api_logs(limit: int = 200):
    return store.recent_logs(limit)


@app.get("/api/logs/severity")
def api_log_severity():
    return store.log_severity_counts()


@app.get("/api/spans")
def api_spans(limit: int = 200):
    return store.recent_spans(limit)


@app.get("/api/spans/slowest")
def api_spans_slowest(limit: int = 15):
    return store.slowest_spans(limit)


@app.get("/api/spans/stats")
def api_spans_stats(limit: int = 15):
    return store.span_name_stats(limit)


# --------------------------------------------------------------------------- #
# UI + health
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
