"""End-to-end smoke test: build OTLP protobuf payloads that mirror the metric
names/attributes Connect actually emits, POST them at the running receiver, and
assert they land in the store and surface through the JSON API.

Usage:  python test_ingest.py [base_url]   (default http://127.0.0.1:8000)
"""

import sys
import time
import urllib.request

from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.logs.v1 import logs_pb2
from opentelemetry.proto.metrics.v1 import metrics_pb2
from opentelemetry.proto.resource.v1 import resource_pb2
from opentelemetry.proto.trace.v1 import trace_pb2

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
NOW = int(time.time() * 1e9)


def kv(k, v):
    av = common_pb2.AnyValue(string_value=v) if isinstance(v, str) else common_pb2.AnyValue(int_value=v)
    return common_pb2.KeyValue(key=k, value=av)


def resource():
    return resource_pb2.Resource(attributes=[
        kv("service.name", "posit-connect"),
        kv("service.namespace", "posit-connect-ns"),
        kv("connect.node.name", "connect-node-1"),
        kv("host.name", "connect-host-1"),
    ])


def post(path, msg):
    data = msg.SerializeToString()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/x-protobuf"})
    with urllib.request.urlopen(req) as r:
        assert r.status == 200, f"{path} -> {r.status}"
    print(f"POST {path}: {len(data)} bytes -> 200")


def build_metrics():
    def gauge(name, unit, desc, points):
        dps = []
        for attrs, val in points:
            dp = metrics_pb2.NumberDataPoint(time_unix_nano=NOW, as_double=float(val),
                                             attributes=[kv(k, v) for k, v in attrs])
            dps.append(dp)
        return metrics_pb2.Metric(name=name, unit=unit, description=desc,
                                  gauge=metrics_pb2.Gauge(data_points=dps))

    def counter(name, unit, points):
        dps = [metrics_pb2.NumberDataPoint(time_unix_nano=NOW, as_int=int(v),
               attributes=[kv(k, a) for k, a in attrs]) for attrs, v in points]
        return metrics_pb2.Metric(name=name, unit=unit,
               sum=metrics_pb2.Sum(data_points=dps, is_monotonic=True,
                   aggregation_temporality=metrics_pb2.AGGREGATION_TEMPORALITY_CUMULATIVE))

    def histogram(name, unit, count, total):
        dp = metrics_pb2.HistogramDataPoint(time_unix_nano=NOW, count=count, sum=total,
             bucket_counts=[count], explicit_bounds=[])
        return metrics_pb2.Metric(name=name, unit=unit,
               histogram=metrics_pb2.Histogram(data_points=[dp],
                   aggregation_temporality=metrics_pb2.AGGREGATION_TEMPORALITY_CUMULATIVE))

    metrics = [
        gauge("users.active", "", "Distinct active users", [
            ([("window", "24h"), ("role", "publisher")], 12),
            ([("window", "24h"), ("role", "viewer")], 87),
            ([("window", "7d"), ("role", "administrator")], 3),
        ]),
        gauge("content.count", "{app}", "Total content", [
            ([("content.type", "shiny"), ("runtime.language", "python")], 41),
            ([("content.type", "quarto"), ("runtime.language", "r")], 18),
        ]),
        gauge("worker.pool.utilization", "", "Utilization ratio",
              [([("application.type", "shiny")], 0.42)]),
        gauge("license.users.current", "", "Named users", [([], 96)]),
        gauge("license.expiration.days_remaining", "d", "Days left", [([], 214)]),
        counter("job.completion", "{job}", [
            ([("job.status", "succeeded")], 320),
            ([("job.status", "failed")], 7),
        ]),
        counter("requests.rejected", "", [([("rejection.reason", "capacity")], 4)]),
        histogram("http.server.request.duration", "s", 1500, 42.5),
        histogram("job.duration", "s", 327, 9812.0),
    ]
    sm = metrics_pb2.ScopeMetrics(metrics=metrics)
    rm = metrics_pb2.ResourceMetrics(resource=resource(), scope_metrics=[sm])
    return metrics_service_pb2.ExportMetricsServiceRequest(resource_metrics=[rm])


def build_logs():
    recs = []
    for sev_num, sev_txt, body in [
        (9, "INFO", "content deployed: guid=abc123"),
        (13, "WARN", "queue depth elevated on default queue"),
        (17, "ERROR", "job failed: exit_code=1 content=xyz"),
    ]:
        recs.append(logs_pb2.LogRecord(
            time_unix_nano=NOW, severity_number=sev_num, severity_text=sev_txt,
            body=common_pb2.AnyValue(string_value=body),
            attributes=[kv("content.guid", "abc123")]))
    sl = logs_pb2.ScopeLogs(log_records=recs)
    rl = logs_pb2.ResourceLogs(resource=resource(), scope_logs=[sl])
    return logs_service_pb2.ExportLogsServiceRequest(resource_logs=[rl])


def build_traces():
    spans = []
    for name, dur_ns, status in [
        ("HTTP GET /content/:guid", 12_000_000, trace_pb2.Status.STATUS_CODE_OK),
        ("queue.item.process", 240_000_000, trace_pb2.Status.STATUS_CODE_OK),
        ("report.execute", 1_800_000_000, trace_pb2.Status.STATUS_CODE_ERROR),
        ("db.query", 3_500_000, trace_pb2.Status.STATUS_CODE_OK),
    ]:
        spans.append(trace_pb2.Span(
            trace_id=b"0123456789abcdef", span_id=b"01234567", name=name,
            kind=trace_pb2.Span.SPAN_KIND_SERVER,
            start_time_unix_nano=NOW, end_time_unix_nano=NOW + dur_ns,
            status=trace_pb2.Status(code=status),
            attributes=[kv("content.guid", "abc123")]))
    ss = trace_pb2.ScopeSpans(spans=spans)
    rs = trace_pb2.ResourceSpans(resource=resource(), scope_spans=[ss])
    return trace_service_pb2.ExportTraceServiceRequest(resource_spans=[rs])


def get(path):
    import json
    with urllib.request.urlopen(BASE + path) as r:
        return json.load(r)


if __name__ == "__main__":
    post("/v1/metrics", build_metrics())
    post("/v1/logs", build_logs())
    post("/v1/traces", build_traces())

    print("\n--- verifying via API ---")
    s = get("/api/summary")
    print("summary:", s)
    assert s["metric_points"] >= 13, s
    assert s["log_records"] >= 3, s
    assert s["spans"] >= 4, s
    assert s["services"] >= 1, s

    names = [m["metric_name"] for m in get("/api/metrics/names")]
    print("metric names:", names)
    assert "users.active" in names and "job.duration" in names

    ts = get("/api/metrics/timeseries?name=users.active")
    print("users.active buckets:", ts)
    assert ts and ts[0]["value"] > 0

    slow = get("/api/spans/slowest")
    print("slowest span:", slow[0]["name"], slow[0]["duration_ms"], "ms")
    assert slow[0]["name"] == "report.execute"

    sev = get("/api/logs/severity")
    print("severity counts:", sev)

    print("\nALL CHECKS PASSED")
