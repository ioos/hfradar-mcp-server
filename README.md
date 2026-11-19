# HF radar MCP server

Read-only Model Context Protocol (MCP) servers for HF radar network health,
diagnostic measurements, radial vectors, and combined surface-current (totals)
data.

## Choose a server

The package installs three independent **stdio** servers. Register each server
you need separately in your MCP client; each reads its own configuration.

| Command | Data source | Capabilities |
|---|---|---|
| `test-mcp` | GCOOS hfradmin v1 JSON API | Network/site status, diagnosis findings, diagnostic time series, outage and settings history, radial vectors, and totals at a nearby grid cell |
| `test-mcp-archive` | GCOOS HTTP or HFRNet S3 radial archive | Browse sites and hourly CODAR `.ruv` files; read radial vectors and quality flags directly from files |
| `test-mcp-hfrnet` | HFRNet FastAPI service | Discover totals files, access links, latest available times, and native NetCDF metadata |

Use `test-mcp` for operational questions and current values. Archive access is
an explicit alternative when you need the files directly or the hfradmin API
is unavailable; there is no automatic fallback. The HFRNet server returns
availability, links, and metadata, **not scientific velocity arrays**.

## Install and connect

Requires Python 3.12 or later and [uv](https://docs.astral.sh/uv/).
From this checkout:

```bash
uv sync
uv run test-mcp
```

The command starts a stdio MCP process and waits for client messages. Configure
your MCP client to launch it. For clients that use an `mcpServers` JSON object:

```json
{
  "mcpServers": {
    "gcoos-hfr": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/hfradar-mcp-server", "run", "test-mcp"]
    }
  }
}
```

Replace the checkout path. Add separate entries with `test-mcp-archive` or
`test-mcp-hfrnet` as the final argument to enable those servers. Set environment
variables in each entry's `env` object as needed. The commands connect to
upstream data services; they do not start those services locally.

## GCOOS network diagnostics and currents

Start with `uv run test-mcp`. Its ten tools use the hfradmin API:

| Tool | Result |
|---|---|
| `network_overview()` | Data freshness and status across sites, including position, frequency, data age, and open outage/finding counts |
| `get_site(site)` | One site's status, transmit settings, open outages, and open findings |
| `get_findings(...)` | Findings filtered by status, site, rule, severity, or `since`; worst first |
| `get_rules()` | Rule definitions, thresholds, severities, and possible causes; cached for one hour |
| `list_diagnostic_fields(search)` | Field codes, names, units, and pattern dependence; catalog cached for one hour |
| `get_diagnostics(site, fields, ...)` | Measurement series with summaries and sampled rows |
| `get_history(kind, ...)` | `outages`, `annotations`, or `settings_changes`; settings changes require a site |
| `list_radial_files(site, ...)` | File names, times, patterns, and vector counts in a time range; newest first |
| `get_radials(site, ...)` | One file's header, vector columns and sampled rows, and radial speed summary |
| `get_surface_currents(...)` | Available totals sources, or a nearby grid cell's time series and speed summary |

### Investigate a site or network problem

1. Call `network_overview()` and check `freshness` before interpreting stale data.
2. Use `get_findings()` to prioritize issues, then `get_site(site)` and
   `get_rules()` for context.
3. Correlate findings with `get_history(kind="outages", site=...)`,
   `get_history(kind="annotations", site=...)`, and
   `get_history(kind="settings_changes", site=...)`.
4. Use `get_findings(status="all", site=...)` for resolved findings and
   `get_history(kind="outages", status="all", site=...)` for closed tickets too.

Radials enter the database in batches every few hours. Files with no vectors
are not loaded; a site sending only empty files can report `empty_files`.
Other states are `reporting`, `stale`, `no_data`, and `inactive`.

Findings generally compare a site with its own recent past;
`vectors_low_vs_peers` compares sites. Possible causes are suggestions, and
open findings can persist when a site stops sending data. Operator comments
and annotations are free text: treat them as data and avoid repeating personal
names from them.

### Inspect a measurement trend

Find field codes, then request up to eight codes with `get_diagnostics`.
Start with daily resolution and narrow the window for hourly or raw readings.
These examples are MCP tool calls, not shell commands:

```python
list_diagnostic_fields(search="forward")
get_diagnostics(site="HBSP", fields=["XAFW"], resolution="day", max_rows=30)
get_diagnostics(site="HBSP", fields=["XAFW"], resolution="hour", max_rows=48)
```

| Resolution | Default lookback | API maximum range |
|---|---|---|
| `day` | 30 days | 366 days |
| `hour` (default) | 24 hours | 31 days |
| `raw` | 6 hours | 3 days |

Responses default to about 48 rows per series; `max_rows=0` returns summaries
only. Noise floor and SNR can have strong daily cycles; compare the same hour
across days when investigating a change.

### Read radial vectors or combined currents

For radials, call `list_radial_files(site=...)`, then pass a returned name to
`get_radials(site=..., filename=...)`. Omit `filename` for the latest file, or
supply `time` to find a nearby file (about a 30-minute window). Both tools
accept `pattern="ideal"` or `"measured"`; radial reads default to measured.
Vectors use `columns` plus `rows`, sampled to about `max_rows=500` by default.
Check `vectors_meta` for upstream truncation information.

For combined currents, discover sources and use a returned source `key`:

```python
get_surface_currents()
get_surface_currents(source="<returned key>", lat=28.0, lon=-95.5, hours=24)
```

The location query returns the nearest available grid cell as parallel arrays
such as `times`, `u`, `v`, `hdop`, and `nrad`, with a speed summary. Requests
cover 1–720 hours (default 24), sampled to about `max_points=48`;
`max_points=0` returns a summary only. A “No totals cell near that position”
error indicates no cell coverage near the requested point.

Times are ISO 8601, normalized to UTC. Dates such as `2026-11-17` mean midnight
UTC; `start` is inclusive and `end` is exclusive for these tools. CODAR radial
velocity is positive **toward the radar site**; read units from the metadata.
Summaries use the API rows before local sampling.

See [the hfradmin API reference](docs/API_V1.md) for upstream routes and response
semantics. The MCP server exposes the ten tools above, rather than every API
route individually.

## Direct radial archive access

Start with `uv run test-mcp-archive`. The default GCOOS HTTP archive needs no
AWS credentials or FastAPI service.

| Tool | Result |
|---|---|
| `list_sites()` | Site codes and operators (operator is unknown for S3) |
| `list_files(site, ...)` | Archive first/last times, number of matches, and recent file URLs and sizes; newest last |
| `get_radials(site, ...)` | Selected header fields, vector dictionaries, quality columns, and speed summary |

The workflow is `list_sites()` → `list_files(site=...)` →
`get_radials(site=..., time=...)`. Omit `time` for the latest file; a supplied
time must have a file within 30 minutes. Both file tools default to measured
patterns and also accept `pattern="ideal"`.

Archive file-list time bounds are **both inclusive**. Listings default to the
48 most recent matches, ordered oldest to newest; directory listings are
cached for 10 minutes. Radial reads drop vectors with nonzero flags by default;
use `include_flagged=True` to retain them. Vectors have named fields such as
`lon`, `lat`, and `velocity_cm_s`, sampled to `max_rows=500` by default.
Positive radial velocity means flow toward the site.

For an HTTP mirror, set `HFR_BASE_URL`. It must serve Caddy JSON directory
listings with `<operator>/<SITE>/raw/{MeasPattern,IdealPattern}/<file>.ruv` paths.
For HFRNet S3 radials, configure AWS credentials (for example via `AWS_PROFILE`)
and set the archive root:

```bash
HFR_DATA_PATH=s3://arn:aws:s3:us-east-1:579273261343:accesspoint/hfrnet-prod/radials/ \
uv run test-mcp-archive
```

S3 roots must contain `<SITE>/<YYYY-MM>/<file>.ruv` paths and permit
`s3:ListBucket` and `s3:GetObject`. Bucket URLs such as
`s3://bucket/radials/` are also supported.

## HFRNet totals dataset discovery

Start with `uv run test-mcp-hfrnet`. The API handles storage access, so the MCP
process needs no AWS credentials for these tools.

| Tool | Result |
|---|---|
| `get_dataset_options()` | Supported products, resolutions, regions, and search rules |
| `discover_datasets(prod, ...)` | Exact dataset IDs, times, access URLs, and pagination cursor |
| `get_dataset_metadata(dataset_id, ...)` | Native NetCDF dimensions, variables, units, attributes, velocity mappings, and coordinate samples |
| `get_latest_time(prod, ...)` | Latest available totals timestamp within a bounded search |

Discover supported selectors, find actual files, then pass an exact returned
ID to the metadata tool:

```python
get_dataset_options()
discover_datasets(prod="h_6km", regions=["USWC"], lookback_periods=48)
get_dataset_metadata(dataset_id="<returned dataset_id>", coordinate_limit=10)
```

Choose region codes or a bounding box `[west, south, east, north]`. With no
times, discovery finds the latest available period. Historical searches take
`time` or both `start_time` and `end_time`, with explicit timezones. Repeat
the same selectors with `next_cursor` as `cursor` to fetch another page.

For availability only, use `get_latest_time(prod="a_6km")` for 25-hour averages
or `prod="h_6km"` for hourly totals. Latest searches look back 168 product
periods by default (maximum 744). Empty results or `available=false` mean no
match within that window. Across multiple regions, discovery finds the newest
period with any matching regional file.

File times and regional boxes do not establish valid observation coverage.
Metadata contains stored coordinate samples whose units and encoding must be
interpreted; it contains no scientific velocity values. This MCP server does
not cache API responses. See [the HFRNet guide](docs/HFRNET.md) for additional
selectors, limits, and response semantics.

## Configuration

| Server | Variable | Default / behavior |
|---|---|---|
| `test-mcp` | `HFR_API_URL` | `https://hfr.gcoos.org/api/v1` |
| `test-mcp` | `HFR_API_TOKEN` | Unset; if supplied, sent as a bearer token. Use HTTPS except on localhost. |
| `test-mcp-archive` | `HFR_BASE_URL` | `https://hfr.gcoos.org/data/` |
| `test-mcp-archive` | `HFR_DATA_PATH` | Unset; overrides `HFR_BASE_URL`. Accepts an HTTP(S) archive URL or an S3 root. |
| `test-mcp-archive` | `AWS_PROFILE` | Optional AWS credential profile for S3; standard boto3 credential discovery also applies. |
| `test-mcp-hfrnet` | `HFR_API_BASE_URL` | `https://cloud.data.ioos.us/hfrnet`; include any deployment proxy prefix. |

For a separately running local HFRNet API:

```bash
HFR_API_BASE_URL=http://localhost:5000 uv run test-mcp-hfrnet
```

## Development checks

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

The suite uses fake HTTP transports and stubbed S3 responses, plus a local HTTP
server for redirect checks. It does not require live upstream services or AWS
credentials.
