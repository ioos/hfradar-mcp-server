# HF radar MCP server

Read CODAR SeaSonde radial data from GCOOS HTTP or HFRNet S3 archives, and
optionally discover HFRNet totals datasets through a separate FastAPI service.

| Data | Source | Configuration |
|---|---|---|
| Radial vectors (`.ruv`) | GCOOS HTTP (default), or HFRNet S3 | `HFR_BASE_URL` or `HFR_DATA_PATH` |
| Totals datasets (`.nc`): availability and metadata | HFRNet FastAPI | `HFR_API_BASE_URL` |

These interfaces are independent and can be used together. GCOOS HTTP access
works without running FastAPI or configuring AWS credentials. The API is needed
only for the totals dataset tools; `HFR_API_BASE_URL` does not select the radial
archive.

## Radial archives: GCOOS over HTTP (default)

Use the public archive at <https://hfr.gcoos.org/data/>. No AWS credentials needed.

```bash
uv run test-mcp
```

For a mirror, set `HFR_BASE_URL`:

```bash
HFR_BASE_URL=https://mirror.example/data/ uv run test-mcp
```

Mirrors must serve Caddy JSON listings with the layout
`<operator>/<SITE>/raw/{MeasPattern,IdealPattern}/<file>.ruv`.

## Radial archives: HFRNet over S3

Use your AWS credentials or `AWS_PROFILE` to access the radial archive:

```bash
HFR_DATA_PATH=s3://arn:aws:s3:us-east-1:579273261343:accesspoint/hfrnet-prod/radials/ \
uv run test-mcp
```

S3 support is included. Other buckets or access point URIs work with the layout
`<SITE>/<YYYY-MM>/<file>.ruv` under the configured root. Access requires
`s3:ListBucket` and `s3:GetObject`; only the configured prefix is used.

`HFR_DATA_PATH` overrides `HFR_BASE_URL` for radial tools. All tools use the same
stdio MCP server.

## Optional HFRNet totals API (latest datasets)

Run the FastAPI service from the `hfrnet-web` project, then start this MCP server:

```bash
HFR_API_BASE_URL=http://localhost:5000 uv run test-mcp
```

`HFR_API_BASE_URL` defaults to `http://localhost:5000`. In the local HFRNet
development stack, port 3000 serves the web app and port 5000 serves FastAPI.
Set the variable in your MCP client's server environment when launching from a
client. The API address can include a proxy prefix, such as `https://host/api`.
The API uses its own storage credentials; this MCP client calls its HTTP routes.

Example questions:

- "What hourly 6 km totals data is available most recently for the West Coast?"
- "When was the latest 25-hour average available?"
- "What variables and velocity units are in the latest dataset?"

The tools use `/datasets/options`, `/datasets`, `/datasets/{id}/metadata`, and
`/latest`. Discover supported selectors first, search for actual datasets, then
pass an exact returned `dataset_id` to the metadata tool. `discover_datasets`
defaults to the latest available period when no times are supplied. For example:

```python
get_dataset_options()
discover_datasets(prod="h_6km", regions=["USWC"], lookback_periods=48)
get_dataset_metadata(dataset_id="<returned dataset_id>", coordinate_limit=10)
get_latest_time(prod="a_6km", bbox=[-130, 30, -115, 50])
```

Choose region codes or a bounding box in `[west, south, east, north]` order.
For historical discovery, supply `time` or both `start_time` and `end_time`,
using ISO 8601 with an explicit timezone. For pagination, repeat the same
selectors with the returned `next_cursor` as `cursor`.

Latest searches look back at most `lookback_periods` product periods (default
168, maximum 744), including the current period. An empty result means no file
matched that search window. Multi-region discovery finds the newest period
with any matching regional file. `get_latest_time(method="all")` uses the API's
configured region exclusions. The MCP server does not cache these API responses.

These API tools describe file availability, access links, and native metadata;
they do not retrieve scientific velocity arrays. Filename times and configured
regional boxes do not establish valid observation coverage. Coordinate samples
retain stored values and require their units, calendar, and scale/missing-value
attributes for interpretation. `coordinate_limit=0` requests schema only, but
the API still downloads the complete file for inspection (maximum 256 MiB).

## Tools

| Tool | Data source | What it does |
|---|---|---|
| `list_sites()` | Radial archive (HTTP or S3) | Sites and operators (`operator` is null for HFRNet) |
| `list_files(site, ...)` | Radial archive (HTTP or S3) | Hourly files, with time filtering and archive coverage |
| `get_radials(site, ...)` | Radial archive (HTTP or S3) | Radial vectors, metadata, and summary; latest file by default |
| `get_dataset_options()` | Totals API | Supported products, resolutions, regions, and search rules |
| `discover_datasets(prod, ...)` | Totals API | File IDs, times, and access URLs; latest period by default |
| `get_dataset_metadata(dataset_id, ...)` | Totals API | Native NetCDF schema, units, velocity mappings, coordinate samples |
| `get_latest_time(prod, ...)` | Totals API | Latest timestamp; `available=false` when the bounded search finds none |

Radial tool times are ISO 8601 UTC, for example `2026-11-17T13:00Z`.
`velocity_cm_s` in radial results follows the CODAR convention: a positive value
means flow **toward** the radar site.

## Development checks

```bash
uv sync
uv run python -m unittest discover -s tests -v
```
