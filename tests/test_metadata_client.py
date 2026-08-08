# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for `internal/metadata/client.py` low-level plumbing.

Complements `test_metadata_writeback.py` (which covers the wire format of the write
endpoints) with: request error wrapping, non-2xx handling, 404 lookups, the by-name
getters, bulk add methods and the factory.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.internal.metadata.client import (  # noqa: E402
    MetadataClient,
    MetadataError,
    client_from_base_url,
)

import requests  # noqa: E402

_REQ = "device_sdk_py.internal.metadata.client.requests"


def _response(status_code=200, body=None, text=""):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = text
    return resp


class TestLowLevelHelpers(unittest.TestCase):
    def setUp(self):
        self.client = MetadataClient(base_url="http://md:59881/")

    def test_base_url_trailing_slash_stripped(self):
        self.assertEqual(self.client.base_url, "http://md:59881")

    def test_url_join(self):
        self.assertEqual(self.client._url("/api/v3/device"), "http://md:59881/api/v3/device")

    def test_post_success_returns_json(self):
        with mock.patch(f"{_REQ}.post", return_value=_response(200, {"ok": True})) as m:
            result = self.client._post("/api/v3/device", [{}])
            self.assertEqual(result, {"ok": True})
            m.assert_called_once()
            self.assertEqual(m.call_args.kwargs["timeout"], 10.0)

    def test_post_network_error_wrapped(self):
        with mock.patch(f"{_REQ}.post", side_effect=requests.ConnectionError("boom")):
            with self.assertRaises(MetadataError):
                self.client._post("/api/v3/device", [{}])

    def test_post_non_2xx_wrapped(self):
        with mock.patch(f"{_REQ}.post",
                        return_value=_response(500, None, text="internal error")):
            with self.assertRaises(MetadataError):
                self.client._post("/api/v3/device", [{}])

    def test_put_network_error_wrapped(self):
        with mock.patch(f"{_REQ}.put", side_effect=requests.ConnectionError("boom")):
            with self.assertRaises(MetadataError):
                self.client._put("/api/v3/device", [{}])

    def test_patch_network_error_wrapped(self):
        with mock.patch(f"{_REQ}.patch", side_effect=requests.ConnectionError("boom")):
            with self.assertRaises(MetadataError):
                self.client._patch("/api/v3/device", [{}])

    def test_delete_network_error_wrapped(self):
        with mock.patch(f"{_REQ}.delete", side_effect=requests.ConnectionError("boom")):
            with self.assertRaises(MetadataError):
                self.client._delete("/api/v3/device")

    def test_get_network_error_wrapped(self):
        with mock.patch(f"{_REQ}.get", side_effect=requests.ConnectionError("boom")):
            with self.assertRaises(MetadataError):
                self.client._get("/api/v3/device")

    def test_get_404_returns_none(self):
        with mock.patch(f"{_REQ}.get", return_value=_response(404, None)):
            self.assertIsNone(self.client._get("/api/v3/device/name/x"))

    def test_get_non_json_body_returns_none(self):
        resp = mock.Mock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        with mock.patch(f"{_REQ}.get", return_value=resp):
            self.assertIsNone(self.client._get("/api/v3/device"))


class TestByNames(unittest.TestCase):
    def setUp(self):
        self.client = MetadataClient(base_url="http://md:59881")

    def test_device_service_by_name_found(self):
        with mock.patch(f"{_REQ}.get",
                        return_value=_response(200, {"service": {"name": "s1"}})):
            result = self.client.device_service_by_name("s1")
            self.assertEqual(result["name"], "s1")

    def test_device_service_by_name_missing(self):
        with mock.patch(f"{_REQ}.get", return_value=_response(404, None)):
            self.assertIsNone(self.client.device_service_by_name("s1"))

    def test_device_profile_by_name_found(self):
        with mock.patch(f"{_REQ}.get",
                        return_value=_response(200, {"profile": {"name": "p1"}})):
            result = self.client.device_profile_by_name("p1")
            self.assertEqual(result["name"], "p1")

    def test_device_by_name_found(self):
        with mock.patch(f"{_REQ}.get",
                        return_value=_response(200, {"device": {"name": "d1"}})):
            result = self.client.device_by_name("d1")
            self.assertEqual(result["name"], "d1")

    def test_provision_watcher_by_name_found(self):
        with mock.patch(f"{_REQ}.get",
                        return_value=_response(200, {"provisionWatcher": {"name": "w1"}})):
            result = self.client.provision_watcher_by_name("w1")
            self.assertEqual(result["name"], "w1")

    def test_by_names_hit_name_route(self):
        with mock.patch(f"{_REQ}.get", return_value=_response(404, None)) as m:
            self.client.device_by_name("d1")
            url = m.call_args[0][0]
            self.assertTrue(url.endswith("/api/v3/device/name/d1"))


class TestBulkAndIds(unittest.TestCase):
    def setUp(self):
        self.client = MetadataClient(base_url="http://md:59881")
        patchers = [
            mock.patch(f"device_sdk_py.internal.metadata.client.dto_serializers."
                       f"{name}", return_value={})
            for name in ("add_device_request", "add_device_profile_request",
                         "add_provision_watcher_request",
                         "update_provision_watcher_request",
                         "update_device_request")
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_add_device_profiles_posts_each(self):
        profiles = [mock.Mock(), mock.Mock()]
        with mock.patch(f"{_REQ}.post", return_value=_response(200, [])) as m:
            self.client.add_device_profiles(profiles)
            body = m.call_args.kwargs["json"]
            self.assertEqual(len(body), 2)

    def test_add_devices_sends_bypass_param(self):
        with mock.patch(f"{_REQ}.post", return_value=_response(200, [])) as m:
            self.client.add_devices([mock.Mock()])
            self.assertEqual(m.call_args.kwargs["params"], {"bypassValidation": "true"})

    def test_add_provision_watchers_posts_each(self):
        watchers = [mock.Mock(), mock.Mock()]
        with mock.patch(f"{_REQ}.post", return_value=_response(200, [])) as m:
            self.client.add_provision_watchers(watchers)
            self.assertEqual(len(m.call_args.kwargs["json"]), 2)

    def test_add_device_returns_id(self):
        with mock.patch(f"{_REQ}.post",
                        return_value=_response(200, [{"id": "abc123"}])):
            self.assertEqual(self.client.add_device(mock.Mock()), "abc123")

    def test_add_device_no_id_in_response(self):
        with mock.patch(f"{_REQ}.post", return_value=_response(200, [{}])):
            self.assertIsNone(self.client.add_device(mock.Mock()))

    def test_add_device_profile_returns_id(self):
        with mock.patch(f"{_REQ}.post",
                        return_value=_response(200, [{"id": "pid"}])):
            self.assertEqual(self.client.add_device_profile(mock.Mock()), "pid")

    def test_add_device_profile_no_list(self):
        with mock.patch(f"{_REQ}.post", return_value=_response(200, {"id": "x"})):
            self.assertIsNone(self.client.add_device_profile(mock.Mock()))

    def test_add_provision_watcher_returns_id(self):
        with mock.patch(f"{_REQ}.post",
                        return_value=_response(200, [{"id": "wid"}])):
            self.assertEqual(self.client.add_provision_watcher(mock.Mock()), "wid")

    def test_delete_device_profile(self):
        with mock.patch(f"{_REQ}.delete", return_value=_response(200, {})) as m:
            self.client.delete_device_profile("p1")
            self.assertTrue(m.call_args[0][0].endswith("/api/v3/deviceprofile/name/p1"))

    def test_delete_provision_watcher(self):
        with mock.patch(f"{_REQ}.delete", return_value=_response(200, {})) as m:
            self.client.delete_provision_watcher("w1")
            self.assertTrue(m.call_args[0][0].endswith("/api/v3/provisionwatcher/name/w1"))

    def test_patch_device_sends_bypass_param(self):
        with mock.patch(f"{_REQ}.patch", return_value=_response(200, {})) as m:
            self.client.patch_device("d1", {"admin_state": "LOCKED"})
            self.assertEqual(m.call_args.kwargs["params"],
                             {"bypassValidation": "false"})

    def test_client_from_base_url(self):
        client = client_from_base_url("http://x", timeout=5.0)
        self.assertIsInstance(client, MetadataClient)
        self.assertEqual(client.timeout, 5.0)


if __name__ == "__main__":
    unittest.main()
