import http.server
import io
import json
import socketserver
import threading
import unittest
import urllib.error
from unittest.mock import patch

from mcp.server.mcpserver.exceptions import ToolError

from test_mcp import client, server
from test_mcp.client import HfrApi, make_opener


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def install(routes):
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        result = routes.get(request.full_url.split("?")[0].removeprefix("https://hfr.example"), {})
        if isinstance(result, Exception):
            raise result
        return FakeResponse(json.dumps(result).encode())

    server.api = HfrApi("https://hfr.example/api/v1", opener=opener)
    return calls


class InputHardeningTests(unittest.TestCase):
    def test_dot_segments_and_separators_never_reach_the_url(self):
        calls = install({})
        for bad in ("..", ".", "../status", "a/b", "a\\b", "%2e%2e", "a.ruv?x=1", "a\r\nX: 1", "@evil.com/x", "x" * 200):
            with self.subTest(filename=bad), self.assertRaises(ToolError):
                server.get_radials("ANWR", filename=bad)
        for bad in ("..", "//evil.com", "a/b"):
            with self.subTest(source=bad), self.assertRaises(ToolError):
                server.get_surface_currents(bad, lat=1, lon=1)
        self.assertEqual(calls, [])

    def test_file_name_returned_by_the_api_is_checked_too(self):
        install({"/api/v1/sites/ANWR/radials": {"radials": [{"filename": "../status", "timestamp": "2026-11-17T12:00:00Z"}]}})
        with self.assertRaisesRegex(ToolError, "file name from the API"):
            server.get_radials("ANWR")

    def test_out_of_range_times_are_tool_errors(self):
        calls = install({})
        cases = [
            lambda: server.get_radials("ANWR", time="0001-01-01T00:00:00+14:00"),
            lambda: server.get_radials("ANWR", time="9999-12-31T23:59:59Z"),
            lambda: server.get_diagnostics("ANWR", ["XAFW"], end="0001-01-01T00:00:00Z"),
            lambda: server.list_radial_files("ANWR", start="9999-12-31T23:59:59+00:00"),
            lambda: server.get_findings(since="1969-12-31"),
            lambda: server.get_findings(since="9" * 100000),
        ]
        for case in cases:
            with self.assertRaises(ToolError):
                case()
        self.assertEqual(calls, [])

    def test_field_codes_cannot_smuggle_parameters(self):
        calls = install({})
        for bad in ("XAFW&resolution=raw", "A#B", "A?B", "A%B", "A/B", "A\\B", "A" * 41):
            with self.subTest(code=bad), self.assertRaisesRegex(ToolError, "Bad field code"):
                server.get_diagnostics("ANWR", [bad])
        self.assertEqual(calls, [])

    def test_hours_are_capped(self):
        calls = install({})
        server.get_surface_currents("hfrnet_6km", lat=28, lon=-95.5, hours=10**12)
        self.assertIn("hours=720", calls[0])


class CurrentsThinningTests(unittest.TestCase):
    def test_arrays_thinned_with_summary(self):
        n = 200
        series = {"cell": {"lat": 28.0, "lon": -95.5}, "source": "k", "hours": n,
                  "times": [f"t{i}" for i in range(n)],
                  "u": [3.0 if i % 2 else None for i in range(n)], "v": [4.0 if i % 2 else None for i in range(n)],
                  "nrad": list(range(n))}
        install({"/api/v1/totals/k/cell": series})
        result = server.get_surface_currents("k", lat=28, lon=-95.5, hours=n, max_points=10)
        self.assertLessEqual(len(result["times"]), 11)
        self.assertEqual(result["times"][-1], "t199")
        self.assertEqual({len(result[k]) for k in ("times", "u", "v", "nrad")}, {len(result["times"])})
        self.assertEqual(result["cell"], {"lat": 28.0, "lon": -95.5})
        self.assertEqual(result["summary"], {"points_total": 200, "points_returned": len(result["times"]),
                                             "valid_points": 100, "mean_speed": 5.0, "max_speed": 5.0})
        only = server.get_surface_currents("k", lat=28, lon=-95.5, max_points=0)
        self.assertEqual(only["times"], [])
        self.assertEqual(only["summary"]["points_total"], 200)


class ClientHardeningTests(unittest.TestCase):
    def test_credentials_and_cleartext_tokens_are_refused(self):
        for base, token in (("https://user:pass@hfr.example/api/v1", None), ("https://user@hfr.example/api/v1", None),
                            ("http://hfr.example/api/v1", "secret")):
            with self.subTest(base=base), self.assertRaises(ValueError):
                HfrApi(base, token=token)
        HfrApi("http://127.0.0.1:8000/api/v1", token="secret")
        HfrApi("https://hfr.example/api/v1", token="secret")

    def test_invalid_url_errors_are_tool_errors(self):
        import http.client

        def opener(request, timeout):
            raise http.client.InvalidURL("nonnumeric port")

        with self.assertRaisesRegex(ToolError, "Could not reach"):
            HfrApi("https://hfr.example/api/v1", opener=opener).get("status")

    def test_oversized_answers_are_refused(self):
        with patch.object(client, "MAX_RESPONSE_BYTES", 1000):
            api = HfrApi("https://hfr.example/api/v1", opener=lambda r, timeout: FakeResponse(b'["' + b"A" * 5000 + b'"]'))
            with self.assertRaisesRegex(ToolError, "larger than"):
                api.get("big")
            small = HfrApi("https://hfr.example/api/v1", opener=lambda r, timeout: FakeResponse(b"[1]"))
            self.assertEqual(small.get("ok"), [1])

    def test_cross_origin_redirects_are_not_followed(self):
        seen = []

        class Other(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

        other = socketserver.TCPServer(("127.0.0.1", 0), Other)

        class Origin(Other):
            def do_GET(self):
                if self.path.startswith("/api/v1/redir"):
                    self.send_response(302)
                    self.send_header("Location", f"http://localhost:{other.server_address[1]}/steal")
                elif self.path.startswith("/api/v1/same"):
                    self.send_response(302)
                    self.send_header("Location", "/api/v1/target")
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"ok": true}')
                    return
                self.end_headers()

        origin = socketserver.TCPServer(("127.0.0.1", 0), Origin)
        for s in (origin, other):
            threading.Thread(target=s.serve_forever, daemon=True).start()
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        api = HfrApi(f"http://127.0.0.1:{origin.server_address[1]}/api/v1", token="secret", opener=make_opener(proxy=False))
        with self.assertRaisesRegex(ToolError, "another origin"):
            api.get("redir")
        self.assertEqual(seen, [])
        self.assertEqual(api.get("same"), {"ok": True})


if __name__ == "__main__":
    unittest.main()
