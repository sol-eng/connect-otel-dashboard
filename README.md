# Connect OTel Receiver + Dashboard

A single FastAPI app, publishable to Posit Connect, that acts as a
**self-referential OpenTelemetry receiver**: the same Connect that emits the
telemetry is pointed back at this content, which ingests OTLP metrics, logs, and
traces, stores them in DuckDB, and renders a dashboard.

```
Posit Connect (embedded OTel Collector)
        │  OTLP/HTTP  (protobuf, optionally gzip)
        ▼
  this app  ──  POST /v1/metrics  /v1/logs  /v1/traces
        │           │
        │           ▼  decode (otlp.py) → DuckDB (store.py)
        └──  GET /  ──  dashboard (dashboard.py, Plotly)
```

Connect gained OpenTelemetry support in **2026.02.0**. Its collector fans out
signals over **OTLP/HTTP** to any endpoint configured under `[OTLPEndpoint]`;
here that endpoint is this content's own public URL.

## Files

| File | Role |
|------|------|
| `app.py` | FastAPI: `/v1/*` OTLP receivers, `/api/*` JSON, `/` dashboard, `/healthz` |
| `otlp.py` | OTLP protobuf → flat rows (metrics/logs/traces) |
| `store.py` | DuckDB schema, inserts, retention sweep, dashboard queries |
| `dashboard.py` | Single-page Plotly dashboard (dark/light, validated palette) |
| `test_ingest.py` | End-to-end smoke test with synthetic Connect-shaped OTLP |
| `requirements.txt` | Runtime deps for Connect |

## Run locally

```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
# in another shell:
python test_ingest.py        # posts synthetic OTLP, asserts it lands
# open http://127.0.0.1:8000/
```

## Deploy to Connect

The entrypoint is the ASGI object `app:app`. Deploy either way below — both
produce identical content.

### Option A — CLI (`rsconnect-python`)

```bash
rsconnect deploy fastapi \
  --entrypoint app:app \
  --title "OTel Receiver + Dashboard" \
  .
```

### Option B — Git-backed publishing

This repo ships a committed `manifest.json`, so Connect can pull and build it
directly from Git — no local checkout or `rsconnect` install required.

1. In Connect, click **Publish → Import from Git**.
2. Repository URL: `https://github.com/sol-eng/connect-otel-dashboard.git`
3. Branch: `main`. Connect detects the `manifest.json` at `[root directory]`.
4. Give it a title and click **Deploy Content**.

Regenerate `manifest.json` after changing the entrypoint or dependencies:

```bash
rsconnect write-manifest fastapi --entrypoint app:app \
  --exclude 'circle-of-observability.*' --exclude 'test_ingest.py' \
  --exclude '*.duckdb' --exclude '*.duckdb.wal' --overwrite .
```

Commit the updated `manifest.json`; Connect redeploys from the tracked file.

### Required content settings (either option)

In the content's **Access** settings, set access to **Anyone – no login
required** (see the auth constraint below), and in **Runtime** settings pin:

- **Min processes = 1** and **Max processes = 1** — always warm, single-writer.
- **Idle timeout** raised high / effectively disabled — so the receiver is not
  reaped between pushes.

## Point Connect at itself

In `/etc/rstudio-connect/rstudio-connect.gcfg`:

```ini
[OpenTelemetry]
Enabled = true

[OTLPEndpoint "self"]
Endpoint = "https://<connect-host>/content/<this-content-guid>"   # NO /v1 suffix
Logs    = true
Traces  = true
Metrics = true
```

Connect's exporter appends `/v1/metrics`, `/v1/logs`, `/v1/traces` to `Endpoint`.
Connect strips the `/content/<guid>` prefix before proxying, so the app sees the
routes at `/v1/*`. Restart Connect after editing the config.

To confirm the collector delivers, watch the app logs — each push logs a line
(`metrics: N data points`), and the dashboard tiles increment.

## Constraints & caveats (why the runtime settings above matter)

These fall out of how Connect content runs; none are bugs, they shape the design:

1. **Anonymous ingest.** `[OTLPEndpoint]` exposes only `Endpoint/Logs/Traces/
   Metrics` — no header or token field — so Connect cannot authenticate its own
   push. The `/v1/*` routes must accept unauthenticated POSTs, i.e. the content
   is set to anonymous access. Restrict at the network layer if that matters.
2. **Single, warm process.** Idle content is reaped (drops pushes) and multiple
   replicas split in-memory/file state. Min=Max=1 + no idle timeout keeps one
   always-on writer. For multi-node/HA, swap DuckDB for external Postgres in
   `store.py` and drop the single-process constraint.
3. **Storage durability.** DuckDB persists to `otel.duckdb` in the content's
   working directory (survives restarts on the same node, not guaranteed across
   redeploys or off-host/K8s execution). `OTEL_RETENTION_HOURS` (default 72)
   bounds the file; a sweep runs on each `/api/summary` call.
4. **Self-feedback loop.** Pointing Connect at itself means this app's own
   `/v1/*` request handling is itself instrumented and pushed back. The volume
   is bounded and useful as a liveness signal; filter by `content.guid` if you
   want to exclude it.

For production-grade retention/HA, the more robust pattern is to point
`[OTLPEndpoint]` at a real collector / Prometheus+Grafana+Tempo+Loki (LGTM)
stack and query that instead — this self-referential app is ideal for a single
node, a demo, or a lightweight always-on view without extra infrastructure.

## Config knobs (env vars)

| Var | Default | Meaning |
|-----|---------|---------|
| `OTEL_DB_PATH` | `otel.duckdb` | DuckDB file path |
| `OTEL_RETENTION_HOURS` | `72` | Drop rows older than this on each sweep |
