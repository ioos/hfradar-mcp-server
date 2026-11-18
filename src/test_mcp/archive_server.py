"""MCP server for CODAR HF radar radial archives.

GCOOS HTTP layout: <operator>/<SITE>/raw/{MeasPattern,IdealPattern}/<filename>.ruv
HFRNet S3 layout: <SITE>/<YYYY-MM>/<filename>.ruv (root points to radials/).
Files are hourly CODAR SeaSonde LLUV radial maps.
"""

import re
import time
from datetime import datetime, timezone
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from test_mcp.storage import archive_from_env

archive = archive_from_env()
TTL = 600  # seconds to cache directory listings
PATTERNS = {"measured": "MeasPattern", "ideal": "IdealPattern"}
RUV = re.compile(r"RDL[mi]_([A-Z0-9]+)_(\d{4})_(\d{2})_(\d{2})_(\d{2})(\d{2})\.ruv$")
NAMES = {
    "LOND": "lon", "LATD": "lat", "VELO": "velocity_cm_s", "BEAR": "bearing_deg",
    "RNGE": "range_km", "HEAD": "direction_deg", "VELU": "u_cm_s", "VELV": "v_cm_s",
    "VFLG": "flag", "ESPC": "spatial_quality", "ETMP": "temporal_quality", "EASN": "snr_db",
}
HEADER_KEYS = [
    "Site", "TimeStamp", "TimeCoverage", "Origin", "PatternType", "PatternDate",
    "RangeResolutionKMeters", "AngularResolution", "TransmitCenterFreqMHz",
    "AverageSignalToNoise", "CurrentVelocityLimit", "LLUVDataQuality",
]

mcp = MCPServer("gcoos-hfr")
_cache: dict[str, tuple[float, list[dict]]] = {}


def ls(path: str) -> list[dict]:
    """Cached directory listing relative to the configured archive root."""
    hit = _cache.get(path)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    entries = archive.list(path)
    _cache[path] = (time.time(), entries)
    return entries


def sites() -> dict[str, str | None]:
    """SITE -> operator (unknown for the HFRNet layout)."""
    if archive.layout == "gcoos":
        return {
            site["name"].rstrip("/").upper(): op["name"].rstrip("/")
            for op in ls("") if op["is_dir"]
            for site in ls(op["name"].rstrip("/") + "/") if site["is_dir"]
        }
    return {entry["name"].rstrip("/").upper(): None for entry in ls("") if entry["is_dir"]}


def files(site: str, pattern: str) -> list[tuple[datetime, str, int]]:
    """Sorted (time, url, size) for every radial file of a site/pattern."""
    site = site.upper()
    known = sites()
    if site not in known:
        raise ToolError(f"Unknown site {site!r}. Known: {', '.join(sorted(known))}")
    if pattern not in PATTERNS:
        raise ToolError(f"pattern must be one of {list(PATTERNS)}")
    try:
        if archive.layout == "gcoos":
            paths = [f"{known[site]}/{site}/raw/{PATTERNS[pattern]}/"]
        else:
            paths = [f"{site}/{entry['name']}" for entry in ls(f"{site}/")
                     if entry["is_dir"] and re.fullmatch(r"\d{4}-\d{2}/", entry["name"])]
        entries = [(path, entry) for path in paths for entry in ls(path)]
    except ToolError as exc:
        raise ToolError(f"Could not list {pattern}-pattern data for {site}: {exc}") from exc
    out = []
    file_prefix = "RDLm_" if pattern == "measured" else "RDLi_"
    for path, e in entries:
        if e["is_dir"] or not e["name"].startswith(file_prefix):
            continue
        if (m := RUV.fullmatch(e["name"])) and m[1] == site:
            y, mo, d, hh, mm = map(int, m.groups()[1:])
            out.append((datetime(y, mo, d, hh, mm, tzinfo=timezone.utc), archive.url(path + e["name"]), e["size"]))
    return sorted(out)


def parse_time(s: str) -> datetime:
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ToolError(f"Bad time {s!r}; use ISO 8601 like 2026-11-17T13:00Z")
    return t.replace(tzinfo=t.tzinfo or timezone.utc).astimezone(timezone.utc)


def fmt(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%MZ")


def num(v: str) -> float | int | str:
    try:
        f = float(v)
        return int(f) if f.is_integer() and "." not in v else f
    except ValueError:
        return v


def parse_ruv(text: str) -> tuple[dict, list[dict]]:
    """Header dict and rows of the first (radial vector) LLUV table."""
    header: dict[str, str] = {}
    cols: list[str] = []
    rows: list[dict] = []
    in_table = False
    for line in text.splitlines():
        if not line.strip() or line.startswith("%%"):
            continue
        if line.startswith("%"):
            key, _, val = line[1:].partition(":")
            if key == "TableStart" and not in_table and not rows:
                in_table, cols = True, header.get("TableColumnTypes", "").split()
            elif key == "TableEnd" and in_table:
                break
            else:
                header.setdefault(key, val.strip())
        elif in_table:
            rows.append({NAMES.get(c, c.lower()): num(v) for c, v in zip(cols, line.split())})
    return header, rows


@mcp.tool()
def list_sites() -> list[dict]:
    """List HF radar sites and their operators in the configured archive."""
    return [{"site": s, "operator": op} for s, op in sorted(sites().items())]


@mcp.tool()
def list_files(
    site: str,
    pattern: Literal["measured", "ideal"] = "measured",
    start: str | None = None,
    end: str | None = None,
    limit: int = 48,
) -> dict:
    """List hourly radial files for a site, newest last.

    start/end are ISO 8601 UTC (e.g. 2026-11-17T00:00Z). Returns at most `limit`
    files (the most recent ones in the range), plus the full coverage of the archive.
    """
    fl = files(site, pattern)
    lo = parse_time(start) if start else None
    hi = parse_time(end) if end else None
    sel = [f for f in fl if (lo is None or f[0] >= lo) and (hi is None or f[0] <= hi)]
    return {
        "site": site.upper(),
        "pattern": pattern,
        "archive_first": fmt(fl[0][0]) if fl else None,
        "archive_last": fmt(fl[-1][0]) if fl else None,
        "matched": len(sel),
        "files": [{"time": fmt(t), "url": u, "bytes": n} for t, u, n in sel[-limit:]],
    }


@mcp.tool()
def get_radials(
    site: str,
    time: str | None = None,
    pattern: Literal["measured", "ideal"] = "measured",
    max_rows: int = 500,
    include_flagged: bool = False,
) -> dict:
    """Radial surface-current velocities for one hour from one site (default: latest file).

    Picks the file nearest `time` (ISO 8601 UTC, must be within 30 min). Each row has
    lon, lat, velocity_cm_s (CODAR convention: positive = flowing toward the site),
    bearing_deg (true, site->cell), range_km, direction_deg (true direction the
    radial component points), u_cm_s, v_cm_s and quality columns.
    Rows with a non-zero vector flag are dropped unless include_flagged. If more than
    max_rows remain, they are evenly subsampled. Also returns a summary and key header metadata.
    """
    fl = files(site, pattern)
    if not fl:
        raise ToolError(f"No {pattern} files for {site}")
    if time:
        want = parse_time(time)
        t, url, _ = min(fl, key=lambda f: abs((f[0] - want).total_seconds()))
        if abs((t - want).total_seconds()) > 1800:
            raise ToolError(f"No file within 30 min of {time}; nearest is {fmt(t)}")
    else:
        t, url, _ = fl[-1]

    header, rows = parse_ruv(archive.read(url).decode("latin-1"))
    total = len(rows)
    if not include_flagged:
        rows = [r for r in rows if r.get("flag", 0) == 0]
    speeds = [abs(r["velocity_cm_s"]) for r in rows if isinstance(r.get("velocity_cm_s"), (int, float))]
    step = max(1, -(-len(rows) // max_rows)) if max_rows > 0 else 1
    return {
        "site": site.upper(),
        "time": fmt(t),
        "url": url,
        "header": {k: header[k] for k in HEADER_KEYS if k in header},
        "summary": {
            "rows_in_file": total,
            "rows_after_flag_filter": len(rows),
            "rows_returned": len(rows[::step]),
            "mean_abs_velocity_cm_s": round(sum(speeds) / len(speeds), 2) if speeds else None,
            "max_abs_velocity_cm_s": max(speeds) if speeds else None,
        },
        "rows": rows[::step],
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
