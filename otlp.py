"""OTLP/HTTP protobuf decoding into flat rows for storage.

Connect's embedded OpenTelemetry Collector fans out signals to configured
[OTLPEndpoint] destinations over OTLP/HTTP using protobuf-encoded bodies
(Content-Type: application/x-protobuf), optionally gzip-compressed. This module
turns those Export*ServiceRequest messages into flat dicts that map cleanly onto
the DuckDB schema in store.py.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.common.v1 import common_pb2

# Severity number -> text, per the OTLP logs spec (used when severity_text is empty).
_SEVERITY = {
    0: "UNSPECIFIED",
    1: "TRACE", 2: "TRACE2", 3: "TRACE3", 4: "TRACE4",
    5: "DEBUG", 6: "DEBUG2", 7: "DEBUG3", 8: "DEBUG4",
    9: "INFO", 10: "INFO2", 11: "INFO3", 12: "INFO4",
    13: "WARN", 14: "WARN2", 15: "WARN3", 16: "WARN4",
    17: "ERROR", 18: "ERROR2", 19: "ERROR3", 20: "ERROR4",
    21: "FATAL", 22: "FATAL2", 23: "FATAL3", 24: "FATAL4",
}

_SPAN_KIND = {0: "UNSPECIFIED", 1: "INTERNAL", 2: "SERVER", 3: "CLIENT", 4: "PRODUCER", 5: "CONSUMER"}
_STATUS = {0: "UNSET", 1: "OK", 2: "ERROR"}


def _any_value(v: common_pb2.AnyValue) -> Any:
    """Convert an OTLP AnyValue to a native Python value."""
    kind = v.WhichOneof("value")
    if kind is None:
        return None
    if kind == "string_value":
        return v.string_value
    if kind == "bool_value":
        return v.bool_value
    if kind == "int_value":
        return v.int_value
    if kind == "double_value":
        return v.double_value
    if kind == "bytes_value":
        return v.bytes_value.hex()
    if kind == "array_value":
        return [_any_value(x) for x in v.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: _any_value(kv.value) for kv in v.kvlist_value.values}
    return None


def _attrs(kvs) -> dict[str, Any]:
    return {kv.key: _any_value(kv.value) for kv in kvs}


def _ts(nanos: int) -> _dt.datetime:
    """OTLP unix-nanos -> tz-aware UTC datetime (0 -> epoch)."""
    if not nanos:
        return _dt.datetime.fromtimestamp(0, tz=_dt.timezone.utc)
    return _dt.datetime.fromtimestamp(nanos / 1e9, tz=_dt.timezone.utc)


def _resource_fields(res_attrs: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Pull the resource attributes Connect stamps on every signal."""
    return (
        res_attrs.get("service.name"),
        res_attrs.get("service.namespace"),
        res_attrs.get("connect.node.name") or res_attrs.get("host.name"),
    )


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def decode_metrics(body: bytes) -> list[dict]:
    req = metrics_service_pb2.ExportMetricsServiceRequest()
    req.ParseFromString(body)
    rows: list[dict] = []
    for rm in req.resource_metrics:
        r_attr = _attrs(rm.resource.attributes)
        svc, ns, node = _resource_fields(r_attr)
        for sm in rm.scope_metrics:
            scope = sm.scope.name
            for metric in sm.metrics:
                rows.extend(
                    _metric_points(metric, scope, svc, ns, node, r_attr)
                )
    return rows


def _metric_points(metric, scope, svc, ns, node, r_attr) -> list[dict]:
    out: list[dict] = []
    name, desc, unit = metric.name, metric.description, metric.unit
    kind = metric.WhichOneof("data")

    def base(dp_attr, ts_nanos):
        return {
            "ts": _ts(ts_nanos),
            "metric_name": name,
            "description": desc,
            "unit": unit,
            "scope": scope,
            "service_name": svc,
            "service_namespace": ns,
            "node_name": node,
            "attributes": json.dumps(dp_attr),
            "resource_attributes": json.dumps(r_attr),
        }

    if kind == "gauge":
        for dp in metric.gauge.data_points:
            row = base(_attrs(dp.attributes), dp.time_unix_nano)
            row.update(metric_type="gauge", value=_num(dp), count=None, sum=None)
            out.append(row)
    elif kind == "sum":
        mtype = "counter" if metric.sum.is_monotonic else "updowncounter"
        for dp in metric.sum.data_points:
            row = base(_attrs(dp.attributes), dp.time_unix_nano)
            row.update(metric_type=mtype, value=_num(dp), count=None, sum=None)
            out.append(row)
    elif kind == "histogram":
        for dp in metric.histogram.data_points:
            row = base(_attrs(dp.attributes), dp.time_unix_nano)
            cnt = dp.count
            s = dp.sum if dp.HasField("sum") else None
            avg = (s / cnt) if (s is not None and cnt) else None
            row.update(metric_type="histogram", value=avg, count=cnt, sum=s)
            out.append(row)
    elif kind == "exponential_histogram":
        for dp in metric.exponential_histogram.data_points:
            row = base(_attrs(dp.attributes), dp.time_unix_nano)
            cnt = dp.count
            s = dp.sum if dp.HasField("sum") else None
            avg = (s / cnt) if (s is not None and cnt) else None
            row.update(metric_type="exp_histogram", value=avg, count=cnt, sum=s)
            out.append(row)
    elif kind == "summary":
        for dp in metric.summary.data_points:
            row = base(_attrs(dp.attributes), dp.time_unix_nano)
            cnt = dp.count
            s = dp.sum
            avg = (s / cnt) if cnt else None
            row.update(metric_type="summary", value=avg, count=cnt, sum=s)
            out.append(row)
    return out


def _num(dp) -> float | None:
    """Number data point value: as_double or as_int."""
    which = dp.WhichOneof("value")
    if which == "as_double":
        return dp.as_double
    if which == "as_int":
        return float(dp.as_int)
    return None


# --------------------------------------------------------------------------- #
# Logs
# --------------------------------------------------------------------------- #
def decode_logs(body: bytes) -> list[dict]:
    req = logs_service_pb2.ExportLogsServiceRequest()
    req.ParseFromString(body)
    rows: list[dict] = []
    for rl in req.resource_logs:
        r_attr = _attrs(rl.resource.attributes)
        svc, ns, node = _resource_fields(r_attr)
        for sl in rl.scope_logs:
            scope = sl.scope.name
            for lr in sl.log_records:
                sev_text = lr.severity_text or _SEVERITY.get(lr.severity_number, "UNSPECIFIED")
                body_val = _any_value(lr.body)
                if not isinstance(body_val, str):
                    body_val = json.dumps(body_val)
                rows.append({
                    "ts": _ts(lr.time_unix_nano or lr.observed_time_unix_nano),
                    "severity_number": lr.severity_number,
                    "severity_text": sev_text,
                    "body": body_val,
                    "scope": scope,
                    "service_name": svc,
                    "service_namespace": ns,
                    "node_name": node,
                    "trace_id": lr.trace_id.hex() or None,
                    "span_id": lr.span_id.hex() or None,
                    "attributes": json.dumps(_attrs(lr.attributes)),
                    "resource_attributes": json.dumps(r_attr),
                })
    return rows


# --------------------------------------------------------------------------- #
# Traces
# --------------------------------------------------------------------------- #
def decode_traces(body: bytes) -> list[dict]:
    req = trace_service_pb2.ExportTraceServiceRequest()
    req.ParseFromString(body)
    rows: list[dict] = []
    for rs in req.resource_spans:
        r_attr = _attrs(rs.resource.attributes)
        svc, ns, node = _resource_fields(r_attr)
        for ss in rs.scope_spans:
            scope = ss.scope.name
            for sp in ss.spans:
                start, end = sp.start_time_unix_nano, sp.end_time_unix_nano
                dur_ms = (end - start) / 1e6 if (start and end and end >= start) else None
                rows.append({
                    "ts": _ts(start),
                    "trace_id": sp.trace_id.hex(),
                    "span_id": sp.span_id.hex(),
                    "parent_span_id": sp.parent_span_id.hex() or None,
                    "name": sp.name,
                    "kind": _SPAN_KIND.get(sp.kind, "UNSPECIFIED"),
                    "start_ts": _ts(start),
                    "end_ts": _ts(end),
                    "duration_ms": dur_ms,
                    "status_code": _STATUS.get(sp.status.code, "UNSET"),
                    "status_message": sp.status.message or None,
                    "scope": scope,
                    "service_name": svc,
                    "service_namespace": ns,
                    "node_name": node,
                    "attributes": json.dumps(_attrs(sp.attributes)),
                    "resource_attributes": json.dumps(r_attr),
                })
    return rows
