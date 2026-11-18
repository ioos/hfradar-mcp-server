"""Standalone MCP server for totals discovery through the HFRNet FastAPI service."""

import re
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, FiniteFloat

from test_mcp.hfrnet_client import DatasetAPI


api = DatasetAPI.from_env()
mcp = MCPServer("hfrnet-totals")

BBox = Annotated[list[FiniteFloat], Field(min_length=4, max_length=4)]
Lookback = Annotated[int, Field(ge=1, le=744)]


def validate_bbox(bbox: list[float] | None) -> None:
    if bbox is None:
        return
    if len(bbox) != 4:
        raise ToolError("bbox must contain [west, south, east, north]")
    west, south, east, north = bbox
    if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= north <= 90):
        raise ToolError("Invalid west, south, east, north bounding box")


@mcp.tool()
def get_dataset_options() -> dict:
    """Get supported totals products, resolutions, region codes, boxes, and search rules.

    Call before selecting a product or region. Support does not establish file
    availability; use discover_datasets to find actual files.
    """
    return api.get("/datasets/options")


@mcp.tool()
def discover_datasets(
    prod: str = "a_6km",
    regions: Annotated[list[str], Field(min_length=1, max_length=7)] | None = None,
    bbox: BBox | None = None,
    time: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    lookback_periods: Lookback = 168,
    limit: Annotated[int, Field(ge=1, le=500)] = 100,
    cursor: str | None = None,
) -> dict:
    """Find totals datasets; defaults to the latest available period, not the current clock hour.

    Returns exact dataset IDs, nominal/observation/creation times, file access
    URLs, and next_cursor. Products include h_6km (hourly) and a_6km (25-hour
    average); get_dataset_options lists all selectors. Choose regions OR bbox
    [west,south,east,north]; west > east crosses the date line. Omit geography
    to search all regions. Boxes select candidate regions, not valid observations.

    Omit all times for latest=true: newest period with ANY matching regional
    file within lookback_periods (1..744 product periods, including current).
    Latest across regions does not mean a latest file for each region.
    Otherwise choose time OR both start_time/end_time (inclusive, <=744 periods).
    Times require an explicit timezone, e.g. 2026-11-18T00:00:00Z.
    Repeat identical selectors with next_cursor as cursor to fetch another page.
    Empty datasets means nothing matched the bounded search, not an empty archive.

    Filename times are not verified NetCDF coverage. Use get_dataset_metadata
    for native variables/units; these results contain no current velocity values.
    """
    validate_bbox(bbox)
    return api.get("/datasets", {
        "prod": prod, "regions": regions, "bbox": bbox, "time": time,
        "start_time": start_time, "end_time": end_time,
        "latest": "true" if all(t is None for t in (time, start_time, end_time)) else None,
        "lookback_periods": lookback_periods, "limit": limit, "cursor": cursor,
    })


@mcp.tool()
def get_dataset_metadata(
    dataset_id: str,
    coordinate_limit: Annotated[int, Field(ge=0, le=1000)] = 10,
) -> dict:
    """Inspect an exact dataset ID returned by discover_datasets; retain the ID verbatim.

    Returns native NetCDF dimensions, variables, units, attributes, velocity
    mappings, and bounded coordinate samples. Samples are stored values; use
    their units, calendar, scale/offset, and missing-value attributes to interpret
    them. No scientific velocity values are returned. coordinate_limit=0 requests
    schema only, though the API still downloads the file (maximum 256 MiB).
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", dataset_id):
        raise ToolError("Use a dataset_id returned by discover_datasets")
    return api.get(f"/datasets/{dataset_id}/metadata", {"coordinate_limit": coordinate_limit})


@mcp.tool()
def get_latest_time(
    prod: str = "a_6km",
    bbox: BBox | None = None,
    method: Literal["any", "all"] = "any",
    lookback_periods: Lookback = 168,
) -> dict:
    """Get the latest available totals timestamp via /latest, without downloading datasets.

    bbox is [west,south,east,north]; omit for the API's default geography.
    method=any finds a period with any matching regional file; all requires all
    selected regions except the API's configured exclusions. Search is bounded
    by lookback_periods (1..744 product periods). available=false means none
    matched within that window. utc_time is YYYYMMDDHHMMSS UTC, timestamp is Unix
    seconds. Use discover_datasets for exact file IDs/links and explicit regions.
    This tool reports availability, not current velocity values.
    """
    validate_bbox(bbox)
    params = {"prod": prod, "method": method, "lookback_periods": lookback_periods}
    if bbox is not None:
        west, south, east, north = bbox
        params.update(lon1=west, lat1=north, lon2=east, lat2=south)
    result = api.get("/latest", params, allow_no_content=True)
    return {"available": bool(result), **result}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
