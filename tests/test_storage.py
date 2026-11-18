import io
import json
import os
import unittest
from unittest.mock import patch

import boto3
from botocore.response import StreamingBody
from botocore.stub import Stubber
from mcp.server.mcpserver.exceptions import ToolError

from test_mcp import archive_server as server
from test_mcp.storage import DEFAULT_SOURCE, HTTPArchive, S3Archive, archive_from_env


FILENAME = "RDLm_TEST_2026_11_17_1300.ruv"
RUV = b"""%Site: TEST
%TableColumnTypes: LOND LATD VELO VFLG
%TableStart:
-90 29 12.5 0
-90 29 30 1
%TableEnd:
"""


class StorageTests(unittest.TestCase):
    def tearDown(self):
        server._cache.clear()

    def test_configuration_and_validation(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(archive_from_env().url(""), DEFAULT_SOURCE)
            self.assertIsInstance(archive_from_env(), HTTPArchive)
            os.environ["HFR_BASE_URL"] = "https://mirror.example/data"
            self.assertEqual(archive_from_env().url(""), "https://mirror.example/data/")
            os.environ["HFR_DATA_PATH"] = "s3://bucket/radials/"
            self.assertEqual(archive_from_env().prefix, "radials/")
        for source in ("s3:///prefix", "s3://bucket/prefix?query", "s3://user@bucket",
                       "https://example/data", "/srv/data"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                S3Archive(source)

    def test_gcoos_paths_tools_and_cache(self):
        archive = HTTPArchive("https://mirror.example/data")
        listings = {
            "": [{"name": "gerg/", "is_dir": True}],
            "gerg/": [{"name": "TEST/", "is_dir": True}],
            "gerg/TEST/raw/MeasPattern/": [{"name": FILENAME, "is_dir": False, "size": len(RUV)}],
            "gerg/TEST/raw/IdealPattern/": [
                {"name": FILENAME.replace("RDLm", "RDLi"), "is_dir": False, "size": len(RUV)},
            ],
        }

        def response(request, timeout):
            path = request.full_url.removeprefix(archive.base)
            if path.endswith(".ruv"):
                self.assertIn(path, {"gerg/TEST/raw/MeasPattern/" + FILENAME,
                                    "gerg/TEST/raw/IdealPattern/" + FILENAME.replace("RDLm", "RDLi")})
                return io.BytesIO(RUV)
            self.assertEqual(request.get_header("Accept"), "application/json")
            return io.BytesIO(json.dumps(listings[path]).encode())

        with patch.object(server, "archive", archive), patch("urllib.request.urlopen", side_effect=response) as fetch:
            self.assertEqual(server.list_sites(), [{"site": "TEST", "operator": "gerg"}])
            for pattern in ("measured", "ideal"):
                listing = server.list_files("test", pattern=pattern, start="2026-11-17", end="2026-11-18")
                self.assertEqual(listing["matched"], 1)
                result = server.get_radials("test", pattern=pattern, time="2026-11-17T13:00Z")
                self.assertEqual(result["summary"]["rows_in_file"], 2)
                self.assertEqual(result["summary"]["rows_returned"], 1)
                self.assertEqual(result["rows"][0]["velocity_cm_s"], 12.5)
            self.assertEqual(server.list_files("TEST", start="2026-11-18")["matched"], 0)
            self.assertEqual(fetch.call_count, 6)

    def s3_archive(self):
        archive = S3Archive("s3://bucket/radials/")
        archive.client = boto3.client("s3", region_name="us-east-1",
                                      aws_access_key_id="test", aws_secret_access_key="test")
        return archive

    def test_tools_pagination_filtering_and_body_closure(self):
        archive = self.s3_archive()
        prefix = "radials/TEST/2026-11/"
        body = StreamingBody(io.BytesIO(RUV), len(RUV))
        with Stubber(archive.client) as stub, patch.object(server, "archive", archive):
            stub.add_response("list_objects_v2", {"CommonPrefixes": [{"Prefix": "radials/TEST/"}]},
                              {"Bucket": "bucket", "Prefix": "radials/", "Delimiter": "/"})
            stub.add_response("list_objects_v2", {"CommonPrefixes": [{"Prefix": prefix}]},
                              {"Bucket": "bucket", "Prefix": "radials/TEST/", "Delimiter": "/"})
            params = {"Bucket": "bucket", "Prefix": prefix, "Delimiter": "/"}
            stub.add_response("list_objects_v2", {"IsTruncated": True, "NextContinuationToken": "next",
                              "Contents": [{"Key": prefix, "Size": 0}]}, params)
            stub.add_response("list_objects_v2", {"IsTruncated": False, "Contents": [
                {"Key": prefix + FILENAME, "Size": len(RUV)},
                {"Key": prefix + FILENAME.replace("RDLm", "RDLi"), "Size": len(RUV)},
            ]}, {**params, "ContinuationToken": "next"})
            stub.add_response("get_object", {"Body": body}, {"Bucket": "bucket", "Key": prefix + FILENAME})
            self.assertEqual(server.list_sites(), [{"site": "TEST", "operator": None}])
            listing = server.list_files("test")
            self.assertEqual(listing["matched"], 1)
            self.assertEqual(listing["archive_last"], "2026-11-17T13:00Z")
            ideal = server.list_files("TEST", pattern="ideal")
            self.assertEqual(ideal["matched"], 1)
            self.assertIn("RDLi_", ideal["files"][0]["url"])
            self.assertEqual(server.list_files("TEST", start="2026-11-18")["matched"], 0)
            result = server.get_radials("test", time="2026-11-17T13:00Z")
            self.assertEqual(result["summary"]["rows_in_file"], 2)
            self.assertEqual(result["summary"]["rows_returned"], 1)
            self.assertEqual(result["rows"][0]["velocity_cm_s"], 12.5)
            self.assertTrue(body._raw_stream.closed)
            stub.assert_no_pending_responses()

    def test_access_denied_preserves_cause(self):
        archive = self.s3_archive()
        with Stubber(archive.client) as stub, patch.object(server, "archive", archive), \
                patch.object(server, "sites", return_value={"TEST": None}):
            stub.add_client_error("list_objects_v2", service_error_code="AccessDenied", http_status_code=403)
            with self.assertRaisesRegex(ToolError, "AccessDenied"):
                server.files("TEST", "measured")

    def test_access_point_arn_with_optional_prefix(self):
        arn = "arn:aws:s3:us-east-1:123456789012:accesspoint/hfrnet-prod"
        for prefix in ("", "radials/", "archive%20data/"):
            with self.subTest(prefix=prefix):
                archive = S3Archive(f"s3://{arn}/{prefix}")
                self.assertEqual(archive.bucket, arn)
                self.assertEqual(archive.region, "us-east-1")
                archive.client = self.s3_archive().client
                key = archive.prefix + FILENAME
                body = StreamingBody(io.BytesIO(RUV), len(RUV))
                with Stubber(archive.client) as stub:
                    stub.add_response("list_objects_v2", {"Contents": [{"Key": key, "Size": len(RUV)}]},
                                      {"Bucket": arn, "Prefix": archive.prefix, "Delimiter": "/"})
                    stub.add_response("get_object", {"Body": body}, {"Bucket": arn, "Key": key})
                    self.assertEqual(archive.list("")[0]["name"], FILENAME)
                    self.assertEqual(archive.read(archive.url(FILENAME)), RUV)
                    stub.assert_no_pending_responses()
        self.assertEqual(S3Archive(f"s3://{arn}").prefix, "")

    def test_monthly_layout_coverage_and_latest(self):
        archive = self.s3_archive()
        months = ("2026-10", "2026-11")
        with Stubber(archive.client) as stub, patch.object(server, "archive", archive), \
                patch.object(server, "sites", return_value={"TEST": None}):
            stub.add_response("list_objects_v2", {"CommonPrefixes": [
                {"Prefix": f"radials/TEST/{month}/"} for month in (*months, "other")
            ]}, {"Bucket": "bucket", "Prefix": "radials/TEST/", "Delimiter": "/"})
            for month in months:
                prefix = f"radials/TEST/{month}/"
                name = f"RDLm_TEST_{month.replace('-', '_')}_17_1300.ruv"
                stub.add_response("list_objects_v2", {"Contents": [{"Key": prefix + name, "Size": len(RUV)}]},
                                  {"Bucket": "bucket", "Prefix": prefix, "Delimiter": "/"})
            stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(RUV), len(RUV))},
                              {"Bucket": "bucket", "Key": "radials/TEST/2026-11/" + FILENAME})
            listing = server.list_files("TEST", start="2026-11-01")
            self.assertEqual(listing["matched"], 1)
            self.assertEqual(listing["archive_first"], "2026-10-17T13:00Z")
            self.assertEqual(server.get_radials("TEST")["time"], "2026-11-17T13:00Z")
            stub.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
