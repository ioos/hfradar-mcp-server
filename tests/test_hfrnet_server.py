import io
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from mcp.server.mcpserver.exceptions import ToolError

from test_mcp import hfrnet_server as server
from test_mcp.hfrnet_client import DEFAULT_API_URL, DatasetAPI


class Response(io.BytesIO):
    def __init__(self, data=None, status=200):
        body = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else b""
        super().__init__(body)
        self.status = status


class APITests(unittest.TestCase):
    def setUp(self):
        self.api = DatasetAPI("http://localhost:5000/api/")
        self.api_patch = patch.object(server, "api", self.api)
        self.api_patch.start()
        self.addCleanup(self.api_patch.stop)

    def test_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(DatasetAPI.from_env().base_url, DEFAULT_API_URL)
            os.environ["HFR_API_BASE_URL"] = "https://dev.example/api/"
            self.assertEqual(DatasetAPI.from_env().base_url, "https://dev.example/api")
        for source in ("file:///tmp/api", "localhost:5000", "http://host/?x=y",
                       "http://host/#fragment", "http://user:password@host"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                DatasetAPI(source)

    def test_latest_discovery_retains_exact_records_and_repeated_regions(self):
        record = {"dataset_id": "opaque_ID-123", "nominal_time": "2026-11-18T01:00:00Z",
                  "access": [{"url": "s3://bucket/file.nc", "authentication": "deployment_dependent"}]}
        payload = {"product": "h_6km", "regions": ["USWC", "USHI"],
                   "datasets": [record], "returned": 1, "next_cursor": "opaque_cursor"}
        response = Response(payload)
        with patch("urllib.request.urlopen", return_value=response) as fetch:
            result = server.discover_datasets(prod="h_6km", regions=["USWC", "USHI"],
                                              lookback_periods=48, limit=1)
        self.assertEqual(result, payload)
        request = fetch.call_args.args[0]
        self.assertEqual(urlsplit(request.full_url).path, "/api/datasets")
        self.assertEqual(parse_qs(urlsplit(request.full_url).query), {
            "prod": ["h_6km"], "regions": ["USWC", "USHI"], "latest": ["true"],
            "lookback_periods": ["48"], "limit": ["1"],
        })
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertTrue(response.closed)

    def test_time_selection_bbox_and_pagination(self):
        selectors = ({"time": "2026-11-18T00:00:00Z"},
                     {"start_time": "2026-11-17T00:00:00Z", "end_time": "2026-11-18T00:00:00Z"})
        for times in selectors:
            with self.subTest(times=times), patch("urllib.request.urlopen", return_value=Response({})) as fetch:
                server.discover_datasets(bbox=[170, 60, -160, 74], cursor="exact_cursor-1", **times)
                query = parse_qs(urlsplit(fetch.call_args.args[0].full_url).query)
                self.assertNotIn("latest", query)
                self.assertEqual(query["bbox"], ["170", "60", "-160", "74"])
                self.assertEqual(query["cursor"], ["exact_cursor-1"])
                for name, value in times.items():
                    self.assertEqual(query[name], [value])

    def test_latest_maps_bbox_to_api_corner_convention(self):
        payload = {"utc_time": "20261118010000", "timestamp": 1794963600, "prod": "h_6km", "method": "all"}
        with patch("urllib.request.urlopen", return_value=Response(payload)) as fetch:
            result = server.get_latest_time("h_6km", [-125, 35, -120, 40], "all", 24)
        self.assertEqual(result, {"available": True, **payload})
        self.assertEqual(parse_qs(urlsplit(fetch.call_args.args[0].full_url).query), {
            "prod": ["h_6km"], "method": ["all"], "lookback_periods": ["24"],
            "lon1": ["-125"], "lat1": ["40"], "lon2": ["-120"], "lat2": ["35"],
        })

    def test_latest_no_content_and_empty_discovery_are_not_errors(self):
        with patch("urllib.request.urlopen", return_value=Response(status=204)):
            self.assertEqual(server.get_latest_time(), {"available": False})
        empty = {"datasets": [], "returned": 0, "next_cursor": None}
        with patch("urllib.request.urlopen", return_value=Response(empty)):
            self.assertEqual(server.discover_datasets(), empty)

    def test_latest_is_fetched_on_every_call(self):
        with patch("urllib.request.urlopen", side_effect=[
            Response({"timestamp": 1}), Response({"timestamp": 2}),
        ]) as fetch:
            self.assertEqual(server.get_latest_time()["timestamp"], 1)
            self.assertEqual(server.get_latest_time()["timestamp"], 2)
        self.assertEqual(fetch.call_count, 2)

    def test_metadata_keeps_native_schema_units_and_sample_semantics(self):
        payload = {"dataset": {"dataset_id": "exact_ID-123"}, "dimensions": {"TIME": 1},
                   "variables": {"u_mean": {"units": "cm s-1", "attributes": {"scale_factor": 0.1}}},
                   "coordinate_values": "stored", "coordinates": {},
                   "velocity_components": {"eastward": ["u_mean"]}}
        with patch("urllib.request.urlopen", return_value=Response(payload)) as fetch:
            self.assertEqual(server.get_dataset_metadata("exact_ID-123", coordinate_limit=0), payload)
        url = urlsplit(fetch.call_args.args[0].full_url)
        self.assertEqual(url.path, "/api/datasets/exact_ID-123/metadata")
        self.assertEqual(parse_qs(url.query), {"coordinate_limit": ["0"]})
        for dataset_id in ("", "../options", "http://elsewhere", "bad?query"):
            with self.subTest(dataset_id=dataset_id), self.assertRaises(ToolError):
                server.get_dataset_metadata(dataset_id)

    def test_http_errors_preserve_fastapi_details(self):
        for status, detail in ((422, [{"loc": ["query", "prod"], "msg": "Unknown product"}]),
                               (404, "Dataset not found"), (502, "Dataset storage is unavailable"),
                               (504, "Dataset discovery timed out")):
            error = HTTPError("http://localhost:5000/datasets", status, "Error", {},
                              io.BytesIO(json.dumps({"detail": detail}).encode()))
            with self.subTest(status=status), patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaisesRegex(ToolError, f"HTTP {status}") as caught:
                    server.discover_datasets()
                self.assertIn(str(detail), str(caught.exception))
                self.assertTrue(error.closed)

    def test_connection_and_non_json_errors_are_actionable(self):
        with patch("urllib.request.urlopen", side_effect=URLError("Connection refused")):
            with self.assertRaisesRegex(ToolError, "HFR_API_BASE_URL"):
                server.get_dataset_options()
        for response in (Response(b"<html>web frontend</html>"), Response([])):
            with patch("urllib.request.urlopen", return_value=response):
                with self.assertRaisesRegex(ToolError, "Expected.*JSON"):
                    server.get_dataset_options()
        error = HTTPError("http://localhost:5000/datasets", 502, "Bad Gateway", {},
                          io.BytesIO(b"<html>Bad Gateway</html>"))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(ToolError, "HTTP 502.*Bad Gateway"):
                server.get_dataset_options()


class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_are_registered_and_callable_through_mcp(self):
        tool_names = {tool.name for tool in await server.mcp.list_tools()}
        self.assertEqual({"get_dataset_options", "discover_datasets",
                          "get_dataset_metadata", "get_latest_time"}, tool_names)
        with patch.object(server, "api", DatasetAPI("http://localhost:5000")):
            for name, arguments, payload in (
                ("get_dataset_options", {}, {"products": []}),
                ("discover_datasets", {"bbox": [-125, 35, -120, 40]}, {"datasets": [], "returned": 0}),
                ("get_dataset_metadata", {"dataset_id": "exact_ID", "coordinate_limit": 0}, {"dimensions": {}}),
                ("get_latest_time", {"bbox": [-125, 35, -120, 40]}, {"timestamp": 123}),
            ):
                with self.subTest(tool=name), patch("urllib.request.urlopen", return_value=Response(payload)):
                    result = await server.mcp.call_tool(name, arguments)
                    self.assertFalse(result.is_error)
                    expected = {"available": True, **payload} if name == "get_latest_time" else payload
                    self.assertEqual(json.loads(result.content[0].text), expected)

    async def test_invalid_limits_and_geography_fail_before_network(self):
        with patch("urllib.request.urlopen") as fetch:
            for name, arguments in (
                ("discover_datasets", {"limit": 501}),
                ("discover_datasets", {"lookback_periods": 0}),
                ("get_dataset_metadata", {"dataset_id": "valid_ID", "coordinate_limit": 1001}),
                ("get_latest_time", {"method": "bogus"}),
                ("get_latest_time", {"bbox": [-125, 35, -120]}),
                ("get_latest_time", {"bbox": [-181, 35, -120, 40]}),
                ("get_latest_time", {"bbox": [-125, 40, -120, 35]}),
                ("get_latest_time", {"bbox": [float("nan"), 35, -120, 40]}),
            ):
                with self.subTest(tool=name, arguments=arguments), self.assertRaises(ToolError):
                    await server.mcp.call_tool(name, arguments)
            fetch.assert_not_called()


class ServerIsolationTests(unittest.TestCase):
    def check_import(self, code, **environment):
        result = subprocess.run(
            [sys.executable, "-c", code], env={**os.environ, **environment},
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hfrnet_does_not_load_gcoos_clients_or_settings(self):
        self.check_import(
            "import sys; from test_mcp import hfrnet_server; "
            "assert not {'test_mcp.server', 'test_mcp.archive_server', "
            "'test_mcp.client', 'test_mcp.storage'} & sys.modules.keys()",
            HFR_API_BASE_URL="http://localhost:5000", HFR_API_URL="invalid",
            HFR_BASE_URL="invalid", HFR_DATA_PATH="invalid",
        )

    def test_gcoos_servers_do_not_load_hfrnet_client_or_settings(self):
        for module, environment in (
            ("server", {"HFR_API_URL": "https://hfr.example/api/v1"}),
            ("archive_server", {"HFR_API_URL": "invalid", "HFR_DATA_PATH": "https://archive.example/data/"}),
        ):
            with self.subTest(module=module):
                self.check_import(
                    f"import sys; from test_mcp import {module}; "
                    "assert 'test_mcp.hfrnet_server' not in sys.modules; "
                    "assert 'test_mcp.hfrnet_client' not in sys.modules",
                    HFR_API_BASE_URL="invalid", **environment,
                )

    def test_default_package_exports_still_refer_to_hfradmin(self):
        from test_mcp import main, mcp, server as hfradmin

        self.assertIs(main, hfradmin.main)
        self.assertIs(mcp, hfradmin.mcp)

    def test_hfrnet_entry_point_runs_its_own_server(self):
        with patch.object(server.mcp, "run") as run:
            server.main()
        run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
