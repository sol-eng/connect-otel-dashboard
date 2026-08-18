"""DuckDB persistence for received OTLP signals.

Single-writer by design: this app must be deployed to Connect with
Max Processes = 1 (see README), so one guarded connection is sufficient and
correct. A retention sweep keeps the file bounded.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import duckdb

DB_PATH = os.environ.get("OTEL_DB_PATH", "otel.duckdb")
# Drop rows older than this many hours on each retention sweep.
RETENTION_HOURS = int(os.environ.get("OTEL_RETENTION_HOURS", "72"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    ts TIMESTAMPTZ,
    metric_name VARCHAR,
    description VARCHAR,
    unit VARCHAR,
    metric_type VARCHAR,
    value DOUBLE,
    count BIGINT,
    sum DOUBLE,
    scope VARCHAR,
    service_name VARCHAR,
    service_namespace VARCHAR,
    node_name VARCHAR,
    attributes JSON,
    resource_attributes JSON
);
CREATE TABLE IF NOT EXISTS logs (
    ts TIMESTAMPTZ,
    severity_number INTEGER,
    severity_text VARCHAR,
    body VARCHAR,
    scope VARCHAR,
    service_name VARCHAR,
    service_namespace VARCHAR,
    node_name VARCHAR,
    trace_id VARCHAR,
    span_id VARCHAR,
    attributes JSON,
    resource_attributes JSON
);
CREATE TABLE IF NOT EXISTS spans (
    ts TIMESTAMPTZ,
    trace_id VARCHAR,
    span_id VARCHAR,
    parent_span_id VARCHAR,
    name VARCHAR,
    kind VARCHAR,
    start_ts TIMESTAMPTZ,
    end_ts TIMESTAMPTZ,
    duration_ms DOUBLE,
    status_code VARCHAR,
    status_message VARCHAR,
    scope VARCHAR,
    service_name VARCHAR,
    service_namespace VARCHAR,
    node_name VARCHAR,
    attributes JSON,
    resource_attributes JSON
);
"""

_METRIC_COLS = ["ts", "metric_name", "description", "unit", "metric_type", "value",
                "count", "sum", "scope", "service_name", "service_namespace",
                "node_name", "attributes", "resource_attributes"]
_LOG_COLS = ["ts", "severity_number", "severity_text", "body", "scope",
             "service_name", "service_namespace", "node_name", "trace_id",
             "span_id", "attributes", "resource_attributes"]
_SPAN_COLS = ["ts", "trace_id", "span_id", "parent_span_id", "name", "kind",
              "start_ts", "end_ts", "duration_ms", "status_code", "status_message",
              "scope", "service_name", "service_namespace", "node_name",
              "attributes", "resource_attributes"]


class Store:
    def __init__(self, path: str = DB_PATH):
        self._lock = threading.Lock()
        self._con = duckdb.connect(path)
        self._con.execute(_SCHEMA)

    def _insert(self, table: str, cols: list[str], rows: list[dict]) -> int:
        if not rows:
            return 0
        placeholders = ", ".join(["?"] * len(cols))
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        data = [[r.get(c) for c in cols] for r in rows]
        with self._lock:
            self._con.executemany(sql, data)
        return len(rows)

    def insert_metrics(self, rows: list[dict]) -> int:
        return self._insert("metrics", _METRIC_COLS, rows)

    def insert_logs(self, rows: list[dict]) -> int:
        return self._insert("logs", _LOG_COLS, rows)

    def insert_spans(self, rows: list[dict]) -> int:
        return self._insert("spans", _SPAN_COLS, rows)

    def query(self, sql: str, params: list | None = None) -> list[dict]:
        with self._lock:
            cur = self._con.execute(sql, params or [])
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def sweep_retention(self) -> None:
        cutoff = f"now() - INTERVAL {RETENTION_HOURS} HOUR"
        with self._lock:
            for tbl in ("metrics", "logs", "spans"):
                self._con.execute(f"DELETE FROM {tbl} WHERE ts < {cutoff}")

    # --- dashboard queries ------------------------------------------------- #
    def summary(self) -> dict[str, Any]:
        rows = self.query("""
            SELECT
              (SELECT COUNT(*) FROM metrics) AS metric_points,
              (SELECT COUNT(DISTINCT metric_name) FROM metrics) AS metric_names,
              (SELECT COUNT(*) FROM logs) AS log_records,
              (SELECT COUNT(*) FROM spans) AS spans,
              (SELECT COUNT(DISTINCT trace_id) FROM spans) AS traces,
              (SELECT COUNT(DISTINCT service_name) FROM (
                  SELECT service_name FROM metrics
                  UNION SELECT service_name FROM logs
                  UNION SELECT service_name FROM spans)) AS services,
              (SELECT COUNT(DISTINCT node_name) FROM (
                  SELECT node_name FROM metrics
                  UNION SELECT node_name FROM logs
                  UNION SELECT node_name FROM spans)) AS nodes,
              (SELECT max(ts) FROM (
                  SELECT ts FROM metrics UNION ALL
                  SELECT ts FROM logs UNION ALL
                  SELECT ts FROM spans)) AS last_seen
        """)
        return rows[0] if rows else {}

    def metric_names(self) -> list[dict]:
        return self.query("""
            SELECT metric_name, any_value(metric_type) AS metric_type,
                   any_value(unit) AS unit, any_value(description) AS description,
                   COUNT(*) AS points
            FROM metrics GROUP BY metric_name ORDER BY metric_name
        """)

    def metric_timeseries(self, name: str, minutes: int = 180) -> list[dict]:
        # Bucket to 1-minute resolution, summing across attribute sets so a
        # single line reflects the whole metric family.
        return self.query("""
            SELECT time_bucket(INTERVAL '1 minute', ts) AS bucket,
                   sum(value) AS value, sum(count) AS count
            FROM metrics
            WHERE metric_name = ? AND ts > now() - (? * INTERVAL '1 minute')
            GROUP BY bucket ORDER BY bucket
        """, [name, minutes])

    def recent_logs(self, limit: int = 200) -> list[dict]:
        return self.query("""
            SELECT ts, severity_text, severity_number, service_name, node_name,
                   body, trace_id
            FROM logs ORDER BY ts DESC LIMIT ?
        """, [limit])

    def log_severity_counts(self) -> list[dict]:
        return self.query("""
            SELECT severity_text, COUNT(*) AS n
            FROM logs GROUP BY severity_text ORDER BY n DESC
        """)

    def recent_spans(self, limit: int = 200) -> list[dict]:
        return self.query("""
            SELECT ts, name, kind, duration_ms, status_code, service_name,
                   node_name, trace_id, span_id
            FROM spans ORDER BY ts DESC LIMIT ?
        """, [limit])

    def slowest_spans(self, limit: int = 15) -> list[dict]:
        return self.query("""
            SELECT name, service_name, duration_ms, status_code, trace_id
            FROM spans WHERE duration_ms IS NOT NULL
            ORDER BY duration_ms DESC LIMIT ?
        """, [limit])

    def span_name_stats(self, limit: int = 15) -> list[dict]:
        return self.query("""
            SELECT name,
                   COUNT(*) AS calls,
                   avg(duration_ms) AS avg_ms,
                   quantile_cont(duration_ms, 0.95) AS p95_ms,
                   sum(CASE WHEN status_code = 'ERROR' THEN 1 ELSE 0 END) AS errors
            FROM spans WHERE duration_ms IS NOT NULL
            GROUP BY name ORDER BY calls DESC LIMIT ?
        """, [limit])
