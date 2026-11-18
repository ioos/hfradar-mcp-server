# HFRNet totals MCP server

This standalone server exposes four tools backed by the HFRNet FastAPI service.
It runs separately from the GCOOS hfradmin and radial archive servers.

The server uses the deployed API at
[cloud.data.ioos.us/hfrnet](https://cloud.data.ioos.us/hfrnet/) by default:

```bash
uv run test-mcp-hfrnet
```

`HFR_API_BASE_URL` defaults to `https://cloud.data.ioos.us/hfrnet`. Override it
in your MCP client's server environment to use another deployment. The API
address includes any proxy prefix, such as `/hfrnet`.
The API uses its own storage credentials; this MCP client calls its HTTP routes.

For local development, run FastAPI from the `hfrnet-web` project and override
the endpoint:

```bash
HFR_API_BASE_URL=http://localhost:5000 uv run test-mcp-hfrnet
```

In that local stack, port 5000 serves FastAPI and port 3000 serves the web app.

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

| Tool | What it does |
|---|---|
| `get_dataset_options()` | Supported totals products, resolutions, regions, and search rules |
| `discover_datasets(prod, ...)` | File IDs, times, and access URLs; latest period by default |
| `get_dataset_metadata(dataset_id, ...)` | Native NetCDF schema, units, velocity mappings, coordinate samples |
| `get_latest_time(prod, ...)` | Latest timestamp; `available=false` when the bounded search finds none |

Only this server reads `HFR_API_BASE_URL`. It does not load the GCOOS hfradmin
client or radial archive configuration. GCOOS settings (`HFR_API_URL`,
`HFR_API_TOKEN`, `HFR_BASE_URL`, and `HFR_DATA_PATH`) do not configure this server.

Run `python -m test_mcp.hfrnet_server` as an alternative to the installed command.
All transport is stdio; register each desired server separately in your MCP client.
