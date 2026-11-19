import io
import json
import unittest
import urllib.error
from urllib.parse import parse_qs, urlsplit

from mcp.server.mcpserver.exceptions import ToolError

from test_mcp import server
from test_mcp.client import DEFAULT_API_URL, HfrApi


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeApi:
    """Stands in for urlopen: routes are {path: json or Exception}; every request is logged."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request, timeout):
        parts = urlsplit(request.full_url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.calls.append((parts.path, query, dict(request.header_items())))
        result = self.routes[parts.path]
        if isinstance(result, Exception):
            raise result
        return FakeResponse(json.dumps(result).encode())

    def query(self, index=-1):
        return self.calls[index][1]


def install(routes, **client_args):
    fake = FakeApi(routes)
    server.api = HfrApi("https://hfr.example/api/v1/", opener=fake, **client_args)
    return fake


def http_error(code, body=b""):
    return urllib.error.HTTPError("https://hfr.example", code, "reason", {}, io.BytesIO(body))


class ClientTests(unittest.TestCase):
    def test_url_headers_and_dropped_params(self):
        fake = install({"/api/v1/sites/HB%2FSP/radials": []}, token="secret")
        server.api.get("sites", "HB/SP", "radials", start=None, limit=3)
        path, query, headers = fake.calls[0]
        self.assertEqual(query, {"limit": "3"})
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(headers["Accept"], "application/json")

    def test_default_url_and_validation(self):
        self.assertEqual(HfrApi().base, DEFAULT_API_URL)
        for bad in ("ftp://x/api", "https://x/api?a=1", "/api/v1", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                HfrApi(bad)

    def test_errors_surface_the_api_message(self):
        install({"/api/v1/status": http_error(503, b'{"error": "database is loading, retry shortly"}')})
        with self.assertRaisesRegex(ToolError, "HTTP 503: database is loading"):
            server.api.get("status")
        install({"/api/v1/x": http_error(404, b'{"error": 404, "message": "No totals cell near that position"}')})
        with self.assertRaisesRegex(ToolError, "HTTP 404: No totals cell near that position"):
            server.api.get("x")
        install({"/api/v1/status": http_error(429)})
        with self.assertRaisesRegex(ToolError, "rate limit"):
            server.api.get("status")
        install({"/api/v1/status": urllib.error.URLError("refused")})
        with self.assertRaisesRegex(ToolError, "Could not reach the hfradmin API"):
            server.api.get("status")

    def test_cache_ttl(self):
        fake = install({"/api/v1/rules": [{"id": "a"}]})
        server.api.get("rules", cache_ttl=60)
        server.api.get("rules", cache_ttl=60)
        server.api.get("rules")
        self.assertEqual(len(fake.calls), 2)


class SiteAndFindingTests(unittest.TestCase):
    def test_network_overview(self):
        status = {"counts": {"reporting": 2, "stale": 1}, "sites": [{"site": "HBSP", "state": "reporting"}]}
        fake = install({
            "/api/v1/meta": {"radials": {"loader_last_run": "2026-11-18T12:00:00Z"}},
            "/api/v1/status": status,
        })
        result = server.network_overview()
        self.assertEqual(result, {"freshness": {"radials": {"loader_last_run": "2026-11-18T12:00:00Z"}}, "status": status})
        self.assertEqual([c[0] for c in fake.calls], ["/api/v1/meta", "/api/v1/status"])

    def test_site_code_is_validated_and_uppercased(self):
        fake = install({"/api/v1/sites/HBSP": {"site": "HBSP"}})
        self.assertEqual(server.get_site("hbsp"), {"site": "HBSP"})
        for bad in ("HB", "HBSPX", "HB/P", ""):
            with self.subTest(bad=bad), self.assertRaises(ToolError):
                server.get_site(bad)
        self.assertEqual(len(fake.calls), 1)

    def test_findings_params(self):
        fake = install({"/api/v1/findings": [{"id": "1"}]})
        server.get_findings(site="hbsp", rule="tx_power_lost", since="2026-09-09", limit=5000)
        self.assertEqual(fake.query(), {
            "status": "open", "site": "HBSP", "rule": "tx_power_lost",
            "since": "2026-09-09T00:00:00Z", "limit": "1000",
        })
        with self.assertRaisesRegex(ToolError, "Bad since"):
            server.get_findings(since="yesterday")

    def test_rules_are_cached(self):
        fake = install({"/api/v1/rules": [{"id": "tx_power_lost"}]})
        server.get_rules()
        server.get_rules()
        self.assertEqual(len(fake.calls), 1)


class DiagnosticsTests(unittest.TestCase):
    CATALOG = {"fields": [{"field": "SNF3", "group": "Noise", "name": "Noise Floors, Loop 3", "unit": "dBm"},
                          {"field": "XAFW", "group": "Transmitter", "name": "Transmitter Forward (W)", "unit": "W"}],
               "groups": ["Noise", "Transmitter"], "notes": []}

    def test_catalog_search(self):
        install({"/api/v1/diagnostics/catalog": self.CATALOG})
        self.assertEqual([f["field"] for f in server.list_diagnostic_fields("forward")], ["XAFW"])
        self.assertEqual(server.list_diagnostic_fields(), self.CATALOG)

    def test_series_are_summarised_and_thinned(self):
        rows = [[f"2026-09-08T{h:02d}:00:00Z", -140 + h, -138 + h, -137 + h, 2] for h in range(24)]
        fake = install({"/api/v1/sites/MBNP/diagnostics": {
            "site": "MBNP", "resolution": "hour",
            "series": [
                {"field": "SNF3", "unit": "dBm", "columns": ["time", "min", "median", "max", "n"], "rows": rows},
                {"field": "TRIP", "columns": ["time", "nonzero_readings", "n"],
                 "rows": [["2026-09-08T00:00:00Z", 0, 2], ["2026-09-08T01:00:00Z", 2, 2]]},
            ],
        }})
        result = server.get_diagnostics("mbnp", ["SNF3", " TRIP "], start="2026-09-08", end="2026-09-09", max_rows=5)
        self.assertEqual(fake.query(), {
            "fields": "SNF3,TRIP", "start": "2026-09-08T00:00:00Z", "end": "2026-09-09T00:00:00Z", "resolution": "hour",
        })
        snf, trip = result["series"]
        self.assertEqual(snf["rows_total"], 24)
        self.assertLessEqual(len(snf["rows"]), 6)
        self.assertEqual(snf["rows"][-1], rows[-1])
        self.assertEqual(snf["summary"]["lowest"], -140)
        self.assertEqual(snf["summary"]["highest"], -114)
        self.assertEqual(snf["summary"]["latest_median"], -115)
        self.assertEqual(trip["summary"], {"points": 2, "from": "2026-09-08T00:00:00Z",
                                           "to": "2026-09-08T01:00:00Z", "nonzero_readings": 2, "readings": 4})
        self.assertEqual(result["site"], "MBNP")

    def test_raw_summary_only_and_defaults(self):
        fake = install({"/api/v1/sites/HBSP/diagnostics": {"series": [
            {"field": "XAFW", "columns": ["time", "value"], "rows": [["t1", 30], ["t2", 36]]}]}})
        result = server.get_diagnostics("HBSP", ["XAFW"], resolution="raw", pattern="ideal", max_rows=0)
        series = result["series"][0]
        self.assertEqual(series["rows"], [])
        self.assertEqual(series["summary"]["latest"], 36)
        self.assertEqual(series["summary"]["median"], 33)
        query = fake.query()
        self.assertEqual(query["pattern"], "ideal")
        span = server._parse(query["end"], "end") - server._parse(query["start"], "start")
        self.assertEqual(span.total_seconds(), 6 * 3600)

    def test_field_count_is_checked(self):
        install({})
        for fields in ([], [" "], list("ABCDEFGHI")):
            with self.subTest(fields=fields), self.assertRaisesRegex(ToolError, "1 to 8"):
                server.get_diagnostics("HBSP", fields)


class HistoryTests(unittest.TestCase):
    def test_kinds_route_to_the_right_endpoint(self):
        fake = install({
            "/api/v1/outages": [], "/api/v1/annotations": [],
            "/api/v1/sites/HBSP/settings-history": {"changes": []},
        })
        server.get_history("outages", site="hbsp", status="all", start="2026-09-01T00:00Z")
        self.assertEqual(fake.query(), {"status": "all", "site": "HBSP", "start": "2026-09-01T00:00:00Z", "limit": "25"})
        server.get_history("annotations", limit=3)
        self.assertEqual(fake.calls[-1][0], "/api/v1/annotations")
        self.assertEqual(fake.query(), {"limit": "3"})
        self.assertEqual(server.get_history("settings_changes", site="HBSP"), {"changes": []})
        with self.assertRaisesRegex(ToolError, "needs a site"):
            server.get_history("settings_changes")


class RadialTests(unittest.TestCase):
    VECTORS = {"columns": ["LOND", "LATD", "VELU", "VELV", "VELO", "HEAD"], "count": 6, "truncated": False,
               "rows": [[-90 + i / 10, 29, 0, 0, v, 90] for i, v in enumerate([10, -20, 30, -40, 50, 60])]}

    def routes(self):
        return {
            "/api/v1/sites/HBSP/radials": {"site": "HBSP", "truncated": False, "count": 2, "radials": [
                {"filename": "RDLm_HBSP_2026_11_17_1300.ruv", "timestamp": "2026-11-17T13:00:00Z", "radial_solutions": 6},
                {"filename": "RDLm_HBSP_2026_11_17_1200.ruv", "timestamp": "2026-11-17T12:00:00Z", "radial_solutions": 6},
            ]},
            "/api/v1/radials/RDLm_HBSP_2026_11_17_1300.ruv": {"Site": "HBSP", "PatternType": "measured"},
            "/api/v1/radials/RDLm_HBSP_2026_11_17_1300.ruv/vectors": self.VECTORS,
            "/api/v1/radials/RDLm_HBSP_2026_11_17_1200.ruv": {"Site": "HBSP"},
            "/api/v1/radials/RDLm_HBSP_2026_11_17_1200.ruv/vectors": self.VECTORS,
        }

    def test_latest_file(self):
        fake = install(self.routes())
        result = server.get_radials("hbsp")
        self.assertEqual(fake.calls[0][1], {"pattern": "measured", "limit": "1"})
        self.assertEqual(result["filename"], "RDLm_HBSP_2026_11_17_1300.ruv")
        self.assertEqual(result["header"]["PatternType"], "measured")
        self.assertEqual(result["rows_in_file"], 6)
        self.assertEqual(result["summary"], {"mean_abs_velocity": 35.0, "max_abs_velocity": 60})
        self.assertEqual(result["vectors_meta"], {"count": 6, "truncated": False})

    def test_nearest_file_to_time_and_thinning(self):
        fake = install(self.routes())
        result = server.get_radials("HBSP", time="2026-11-17T12:10Z", max_rows=3)
        self.assertEqual(result["filename"], "RDLm_HBSP_2026_11_17_1200.ruv")
        self.assertEqual(fake.calls[0][1]["start"], "2026-11-17T11:40:00Z")
        self.assertEqual(fake.calls[0][1]["end"], "2026-11-17T12:41:00Z")
        self.assertEqual(result["rows_returned"], 3)
        self.assertEqual(result["rows"][0], self.VECTORS["rows"][0])

    def test_filename_skips_the_listing(self):
        fake = install(self.routes())
        server.get_radials("HBSP", filename="RDLm_HBSP_2026_11_17_1300.ruv")
        self.assertEqual([c[0] for c in fake.calls], [
            "/api/v1/radials/RDLm_HBSP_2026_11_17_1300.ruv", "/api/v1/radials/RDLm_HBSP_2026_11_17_1300.ruv/vectors"])

    def test_errors(self):
        install({"/api/v1/sites/HBSP/radials": {"radials": [], "count": 0}})
        with self.assertRaisesRegex(ToolError, "No measured radial files for HBSP within 30 min"):
            server.get_radials("HBSP", time="2026-11-17T12:00Z")
        install({"/api/v1/sites/HBSP/radials": {"radials": [{"timestamp": "2026-11-17T12:00:00Z"}]}})
        with self.assertRaisesRegex(ToolError, "Unexpected radial file entry"):
            server.get_radials("HBSP")
        install({"/api/v1/radials/X.ruv": {}, "/api/v1/radials/X.ruv/vectors": {"error": "x"}})
        with self.assertRaisesRegex(ToolError, "Unexpected vectors answer"):
            server.get_radials("HBSP", filename="X.ruv")

    def test_file_listing_passes_through(self):
        fake = install({"/api/v1/sites/HBSP/radials": []})
        server.list_radial_files("hbsp", start="2026-11-17", pattern="ideal", limit=10)
        self.assertEqual(fake.query(), {"start": "2026-11-17T00:00:00Z", "pattern": "ideal", "limit": "10"})


class TotalsTests(unittest.TestCase):
    def test_sources_then_cell(self):
        fake = install({"/api/v1/totals": [{"source": "gcoos"}], "/api/v1/totals/gcoos/cell": {"rows": []}})
        self.assertEqual(server.get_surface_currents(), [{"source": "gcoos"}])
        server.get_surface_currents("gcoos", lat=29.3, lon=-94.8, hours=0)
        self.assertEqual(fake.query(), {"lat": "29.3", "lon": "-94.8", "hours": "1"})
        with self.assertRaisesRegex(ToolError, "lat and lon"):
            server.get_surface_currents("gcoos")
        with self.assertRaisesRegex(ToolError, "within"):
            server.get_surface_currents("gcoos", lat=95, lon=0)


class RegistrationTests(unittest.TestCase):
    def test_tool_names_and_instructions(self):
        import asyncio

        tools = asyncio.run(server.mcp.list_tools())
        self.assertEqual(sorted(t.name for t in tools), sorted([
            "network_overview", "get_site", "get_findings", "get_rules", "list_diagnostic_fields",
            "get_diagnostics", "get_history", "list_radial_files", "get_radials", "get_surface_currents",
        ]))
        self.assertIn("batches every few hours", server.INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
