# HF radar MCP server

MCP server for the GCOOS HF radar network. It answers questions about site status,
diagnosis findings, measurements, outages, radial vectors and surface currents.

This package provides three separate stdio MCP servers. Register each desired
command separately in your MCP client:

| Command | Data source | Configuration |
|---|---|---|
| `test-mcp` | GCOOS hfradmin v1 API | `HFR_API_URL`, optional `HFR_API_TOKEN` |
| `test-mcp-archive` | GCOOS HTTP radial archive or HFRNet S3 radials | `HFR_BASE_URL` or `HFR_DATA_PATH` |
| `test-mcp-hfrnet` | HFRNet FastAPI totals datasets | `HFR_API_BASE_URL` |

Each server loads its own client and configuration independently.

## hfradmin API (default)

The server reads the read-only hfradmin JSON API described in
[docs/API_V1.md](docs/API_V1.md). No credentials are needed for now.

```bash
uv run test-mcp
```

| Variable | Default | Purpose |
|---|---|---|
| `HFR_API_URL` | `https://hfr.gcoos.org/api/v1` | API base URL (use it for a staging host) |
| `HFR_API_TOKEN` | unset | Sent as `Authorization: Bearer ...` if the API ever requires it |

Transport is stdio. Authentication, if added, is a change to `HfrApi.headers()` only.

## Tools

| Tool | Answers | API routes |
|---|---|---|
| `network_overview()` | Data freshness and the state of every site (with position, frequency, data age) | `/meta`, `/status` |
| `get_site(site)` | One site: status, TX settings, open outages and findings | `/sites/{site}` |
| `get_findings(status, site, rule, severity, since, limit)` | What is wrong, worst first | `/findings` |
| `get_rules()` | Rule meanings, thresholds, possible causes (cached 1 h) | `/rules` |
| `list_diagnostic_fields(search)` | Field codes for diagnostics (cached 1 h) | `/diagnostics/catalog` |
| `get_diagnostics(site, fields, start, end, resolution, pattern, max_rows)` | Measurement series with a summary; rows thinned to `max_rows` | `/sites/{site}/diagnostics` |
| `get_history(kind, site, start, end, status, limit)` | `outages`, `annotations` or `settings_changes` | `/outages`, `/annotations`, `/sites/{site}/settings-history` |
| `list_radial_files(site, start, end, pattern, limit)` | Coverage and gaps, newest first | `/sites/{site}/radials` |
| `get_radials(site, filename, time, pattern, max_rows)` | Header and vectors of one hourly file (latest by default) | `/radials/{filename}`, `/radials/{filename}/vectors` |
| `get_surface_currents(source, lat, lon, hours, max_points)` | Total-current sources, or one cell's time series | `/totals`, `/totals/{source}/cell` |

Times are ISO 8601 UTC (`2026-11-17T13:00Z` or `2026-11-17`); the server sends them to the
API as `2026-11-17T13:00:00Z`. Radial files reach the database in batches every few hours,
so check `network_overview().freshness` before calling a site down.
`velocity` follows the CODAR convention: positive means flow **toward** the radar site.

## Archive mode (fallback)

Until the API is in production, the previous file-archive server is still available as
`test-mcp-archive`. It reads `.ruv` files from <https://hfr.gcoos.org/data/> (override with
`HFR_BASE_URL`) or from the HFRNet S3 archive (`HFR_DATA_PATH=s3://...`) and offers
`list_sites`, `list_files` and `get_radials`.

```bash
uv run test-mcp-archive
```

GCOOS HTTP access needs neither FastAPI nor AWS credentials. For a mirror, use
`HFR_BASE_URL=https://mirror.example/data/`; it must serve Caddy JSON listings
with `<operator>/<SITE>/raw/{MeasPattern,IdealPattern}/<file>.ruv` paths.

For HFRNet S3 radials, use your AWS credentials or `AWS_PROFILE`:

```bash
HFR_DATA_PATH=s3://arn:aws:s3:us-east-1:579273261343:accesspoint/hfrnet-prod/radials/ \
uv run test-mcp-archive
```

Other S3 roots must use `<SITE>/<YYYY-MM>/<file>.ruv` paths. Access requires
`s3:ListBucket` and `s3:GetObject`. `HFR_DATA_PATH` overrides `HFR_BASE_URL`.

## HFRNet totals datasets (separate server)

The dedicated HFRNet MCP server uses the deployed API at
[cloud.data.ioos.us/hfrnet](https://cloud.data.ioos.us/hfrnet/) by default:

```bash
uv run test-mcp-hfrnet
```

Set `HFR_API_BASE_URL` to use another deployment or a local service, for example
`HFR_API_BASE_URL=http://localhost:5000 uv run test-mcp-hfrnet`.
This server offers `get_dataset_options`, `discover_datasets`,
`get_dataset_metadata`, and `get_latest_time`. Searches default to the latest
available period. These tools return file availability, links, and metadata.

See [docs/HFRNET.md](docs/HFRNET.md) for examples, selectors, limits, and response
semantics. The HFRNet tools are registered only by `test-mcp-hfrnet`.

## Development checks

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

API tests use a fake transport, so they run without network access.
