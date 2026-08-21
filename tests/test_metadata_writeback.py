# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for the Core Metadata runtime write-back (M1 / Gap G1).

Two layers are covered:

* `TestMetadataClientWriteEndpoints` - the exact Core Metadata v4 wire format of the
  write endpoints (method / route / query params / request envelope) with a mocked
  ``requests`` module, and the failure modes (non-2xx, network error).
* `TestMetadataWriteBack` - the DeviceService cache-first + rollback semantics: a fake
  metadata client records the calls and can be told to fail, proving that metadata
  failures propagate as `EdgexError` and that the local caches are rolled back.

Runs with either pytest (if installed) or the stdlib runner::

    python -m unittest tests.test_metadata_writeback
    # or
    python -m pytest tests
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

from device_sdk_py.internal.cache import (  # noqa: E402
    Device,
    DeviceProfile,
    ProvisionWatcher,
)
from device_sdk_py.internal.common.utils import EdgexErrorKind  # noqa: E402
from device_sdk_py.internal.metadata.client import (  # noqa: E402
    MetadataClient,
    MetadataError,
)
from device_sdk_py.service.bootstrap import bootstrap  # noqa: E402


class _Driver:
    def start(self):
        pass


class _ValidatingDriver:
    """A ProtocolDriver that records every Device it is asked to validate."""

    def __init__(self):
        self.validated = []

    def start(self):
        pass

    def validate_device(self, device):
        self.validated.append(device.name)


def _make_service(driver=None):
    return bootstrap("device-simple", "0.0.0", driver or _Driver())


def _make_device(name="sensor-01"):
    return Device(name=name, profile_name="p1")


def _make_profile(name="p1"):
    return DeviceProfile(name=name)


def _make_watcher(name="w1"):
    return ProvisionWatcher(name=name)


class TestMetadataClientWriteEndpoints(unittest.TestCase):
    """Wire format of the Core Metadata v4 write endpoints (TDD RED)."""

    def setUp(self):
        self.client = MetadataClient(base_url="http://md:59881")

    def _ok(self, status=200):
        resp = mock.MagicMock()
        resp.status_code = status
        resp.json.return_value = [{"apiVersion": "v3", "requestId": "r",
                                   "statusCode": status}]
        resp.text = "ok"
        return resp

    # -- Device ---------------------------------------------------------------

    def test_add_device_posts_to_device_route_with_bypass_param(self):
        with mock.patch("device_sdk_py.internal.metadata.client.requests.post",
                        return_value=self._ok()) as mpost:
            self.client.add_device(_make_device(), bypass_validation=True)
            mpost.assert_called_once()
            args, kwargs = mpost.call_args
            url = args[0]
            self.assertEqual(url, "http://md:59881/api/v3/device")
            self.assertEqual(kwargs["params"], {"bypassValidation": "true"})
            body = kwargs["json"]
            self.assertEqual(len(body), 1)
            self.assertEqual(body[0]["apiVersion"], "v3")
            self.assertIn("requestId", body[0])
            self.assertEqual(body[0]["device"]["name"], "sensor-01")

    def test_add_device_passes_false_bypass_param(self):
        with mock.patch("device_sdk_py.internal.metadata.client.requests.post",
                        return_value=self._ok()) as mpost:
            self.client.add_device(_make_device(), bypass_validation=False)
            _, kwargs = mpost.call_args
            self.assertEqual(kwargs["params"], {"bypassValidation": "false"})

    def test_patch_device_patches_collection_route_with_camel_case_updates(self):
        with mock.patch("device_sdk_py.internal.metadata.client.requests.patch",
                        return_value=self._ok()) as mpatch:
            self.client.patch_device(
                "sensor-01", {"operating_state": "DOWN", "labels": ["a"]},
                bypass_validation=True)
            mpatch.assert_called_once()
            args, kwargs = mpatch.call_args
            url = args[0]
            self.assertEqual(url, "http://md:59881/api/v3/device")
            self.assertEqual(kwargs["params"], {"bypassValidation": "true"})
            body = kwargs["json"]
            self.assertEqual(len(body), 1)
            self.assertEqual(body[0]["device"]["name"], "sensor-01")
            self.assertEqual(body[0]["device"]["operatingState"], "DOWN")
            self.assertEqual(body[0]["device"]["labels"], ["a"])

    def test_delete_device_deletes_by_name_route(self):
        with mock.patch("device_sdk_py.internal.metadata.client.requests.delete",
                        return_value=self._ok()) as mdelete:
            self.client.delete_device("sensor-01")
            mdelete.assert_called_once()
            args, _ = mdelete.call_args
            url = args[0]
            self.assertEqual(url, "http://md:59881/api/v3/device/name/sensor-01")

    # -- DeviceProfile --------------------------------------------------------

    def test_add_device_profile_posts(self):
        with mock.patch("device_sdk_py.internal.metadata.client.requests.post",
                        return_value=self._ok()) as mpost:
            self.client.add_device_profile(_make_profile())
            mpost.assert_called_once()
            args, kwargs = mpost.call_args
            url = args[0]
            self.assertEqual(url, "http://md:59881/api/v3/deviceprofile")
            self.assertEqual(kwargs["json"][0]["profile"]["name"], "p1")

    def test_update_device_profile_puts(self):
        with mock.patch("device_sdk_py.internal.metadata.client.requests.put",
                        return_value=self._ok()) as mput:
            self.client.update_device_profile(_make_profile())
            mput.assert_called_once()
            args, kwargs = mput.call_args
            url = args[0]
            self.assertEqual(url, "http://md:59881/api/v3/deviceprofile")
            self.assertEqual(kwargs["json"][0]["profile"]["name"], "p1")

    def test_delete_device_profile_deletes_by_name_route(self):
        with mock.patch("device_sdk_py.internal.metadata.client.requests.delete",
                        return_value=self._ok()) as mdelete:
            self.client.delete_device_profile("p1")
            mdelete.assert_called_once()
            args, _ = mdelete.call_args
            url = args[0]
            self.assertEqual(url, "http://md:59881/api/v3/deviceprofile/name/p1")

    # -- ProvisionWatcher -----------------------------------------------------

    def test_add_provision_watcher_posts(self):
        with mock.patch("device_sdk_py.internal.metadata.client.requests.post",
                        return_value=self._ok()) as mpost:
            self.client.add_provision_watcher(_make_watcher())
            mpost.assert_called_once()
            args, kwargs = mpost.call_args
            url = args[0]
            self.assertEqual(url, "http://md:59881/api/v3/provisionwatcher")
            self.assertEqual(kwargs["json"][0]["provisionwatcher"]["name"], "w1")

    def test_update_provision_watcher_patches_collection_route(self):
        watcher = _make_watcher()
        watcher.profile_name = "p1"
        watcher.labels = ["x"]
        with mock.patch("device_sdk_py.internal.metadata.client.requests.patch",
                        return_value=self._ok()) as mpatch:
            self.client.update_provision_watcher(watcher)
            mpatch.assert_called_once()
            args, kwargs = mpatch.call_args
            url = args[0]
            self.assertEqual(url, "http://md:59881/api/v3/provisionwatcher")
            body = kwargs["json"]
            self.assertEqual(len(body), 1)
            pw = body[0]["provisionwatcher"]
            self.assertEqual(pw["name"], "w1")
            self.assertEqual(pw["profileName"], "p1")
            self.assertEqual(pw["labels"], ["x"])

    def test_delete_provision_watcher_deletes_by_name_route(self):
        with mock.patch("device_sdk_py.internal.metadata.client.requests.delete",
                        return_value=self._ok()) as mdelete:
            self.client.delete_provision_watcher("w1")
            mdelete.assert_called_once()
            args, _ = mdelete.call_args
            url = args[0]
            self.assertEqual(url, "http://md:59881/api/v3/provisionwatcher/name/w1")

    # -- failures -------------------------------------------------------------

    def test_non_2xx_raises_metadata_error(self):
        with mock.patch("device_sdk_py.internal.metadata.client.requests.post",
                        return_value=self._ok(status=500)):
            with self.assertRaises(MetadataError):
                self.client.add_device(_make_device(), bypass_validation=False)

    def test_network_error_raises_metadata_error(self):
        import requests
        with mock.patch("device_sdk_py.internal.metadata.client.requests.post",
                        side_effect=requests.ConnectionError("down")):
            with self.assertRaises(MetadataError):
                self.client.add_device(_make_device(), bypass_validation=False)


class _FakeMetadataClient:
    """In-process fake of the MetadataClient write API.

    Records every call and can be told to fail specific operations so the DeviceService
    rollback / error-propagation behaviour can be asserted without any network.
    """

    def __init__(self):
        self.calls = []
        self._fail_ops = set()

    def fail(self, op):
        self._fail_ops.add(op)

    def _record(self, op, name, **kwargs):
        self.calls.append((op, name, kwargs))
        if op in self._fail_ops:
            raise MetadataError(f"{op} failed (fake)")

    def add_device(self, device, bypass_validation=False):
        self._record("add_device", device.name, bypass_validation=bypass_validation)
        return f"md-device-{device.name}"

    def patch_device(self, name, updates, bypass_validation=False):
        self._record("patch_device", name, updates=updates,
                     bypass_validation=bypass_validation)

    def delete_device(self, name):
        self._record("delete_device", name)

    def add_device_profile(self, profile):
        self._record("add_device_profile", profile.name)
        return f"md-profile-{profile.name}"

    def update_device_profile(self, profile):
        self._record("update_device_profile", profile.name)

    def delete_device_profile(self, name):
        self._record("delete_device_profile", name)

    def add_provision_watcher(self, watcher):
        self._record("add_provision_watcher", watcher.name)
        return f"md-watcher-{watcher.name}"

    def update_provision_watcher(self, watcher):
        self._record("update_provision_watcher", watcher.name)

    def delete_provision_watcher(self, name):
        self._record("delete_provision_watcher", name)


class TestMetadataWriteBack(unittest.TestCase):
    """DeviceService cache-first + rollback + strict error propagation (TDD RED)."""

    def setUp(self):
        self.driver = _ValidatingDriver()
        self.ds = _make_service(self.driver)
        self.ds._metadata_base_url = lambda: "http://md:59881"  # type: ignore[method-assign]
        self.fake = _FakeMetadataClient()
        self.ds._metadata_client_instance = self.fake

    def tearDown(self):
        self.ds._shutdown()

    # -- Device add -----------------------------------------------------------

    def test_add_device_writes_metadata_and_cache(self):
        dev_id = self.ds.add_device(_make_device())
        self.assertTrue(dev_id.startswith("md-device-"))
        self.assertTrue(self.ds.device_exists_for_name("sensor-01"))
        self.assertEqual(self.fake.calls, [("add_device", "sensor-01",
                                            {"bypass_validation": False})])

    def test_add_device_validated_calls_driver_validate_device(self):
        self.ds.add_device(_make_device())
        self.assertEqual(self.driver.validated, ["sensor-01"])

    def test_add_device_without_validation_skips_validation(self):
        self.ds.add_device_without_validation(_make_device())
        self.assertEqual(self.driver.validated, [])
        self.assertEqual(self.fake.calls, [("add_device", "sensor-01",
                                            {"bypass_validation": True})])

    def test_add_device_metadata_failure_rolls_back_cache(self):
        self.fake.fail("add_device")
        with self.assertRaises(Exception) as ctx:
            self.ds.add_device(_make_device())
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.SERVER_ERROR)
        self.assertFalse(self.ds.device_exists_for_name("sensor-01"))
        self.assertEqual(len(self.ds.devices()), 0)

    # -- Device patch ---------------------------------------------------------

    def test_patch_device_applies_to_cache_and_metadata(self):
        self.ds.add_device(_make_device())
        self.ds.patch_device({"name": "sensor-01", "operating_state": "DOWN"})
        self.assertEqual(self.ds.get_device_by_name("sensor-01").operating_state, "DOWN")
        op, name, kwargs = self.fake.calls[-1]
        self.assertEqual((op, name), ("patch_device", "sensor-01"))
        self.assertEqual(kwargs["updates"], {"operating_state": "DOWN"})
        self.assertFalse(kwargs["bypass_validation"])

    def test_patch_device_metadata_failure_rolls_back_cache(self):
        self.ds.add_device(_make_device())
        self.fake.fail("patch_device")
        with self.assertRaises(Exception) as ctx:
            self.ds.patch_device({"name": "sensor-01", "operating_state": "DOWN"})
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.SERVER_ERROR)
        self.assertEqual(self.ds.get_device_by_name("sensor-01").operating_state, "")

    def test_patch_device_without_validation_forwards_flag(self):
        self.ds.add_device(_make_device())
        self.ds.patch_device_without_validation(
            {"name": "sensor-01", "operating_state": "DOWN"})
        _, _, kwargs = self.fake.calls[-1]
        self.assertTrue(kwargs["bypass_validation"])

    def test_update_device_operating_state_bypasses_validation(self):
        self.ds.add_device(_make_device())
        self.ds.update_device_operating_state("sensor-01", "DOWN")
        self.assertEqual(self.ds.get_device_by_name("sensor-01").operating_state, "DOWN")
        _, _, kwargs = self.fake.calls[-1]
        self.assertTrue(kwargs["bypass_validation"])

    # -- Device remove --------------------------------------------------------

    def test_remove_device_ok(self):
        self.ds.add_device(_make_device())
        self.ds.remove_device_by_name("sensor-01")
        self.assertFalse(self.ds.device_exists_for_name("sensor-01"))
        self.assertEqual(self.fake.calls[-1], ("delete_device", "sensor-01", {}))

    def test_remove_device_metadata_failure_rolls_back_cache(self):
        self.ds.add_device(_make_device())
        self.fake.fail("delete_device")
        with self.assertRaises(Exception) as ctx:
            self.ds.remove_device_by_name("sensor-01")
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.SERVER_ERROR)
        self.assertTrue(self.ds.device_exists_for_name("sensor-01"))

    # -- DeviceProfile --------------------------------------------------------

    def test_add_profile_writes_metadata_and_cache(self):
        profile_id = self.ds.add_device_profile(_make_profile())
        self.assertTrue(profile_id.startswith("md-profile-"))
        self.assertTrue(self.ds.get_profile_by_name("p1").name == "p1")

    def test_add_profile_metadata_failure_rolls_back_cache(self):
        self.fake.fail("add_device_profile")
        with self.assertRaises(Exception) as ctx:
            self.ds.add_device_profile(_make_profile())
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.SERVER_ERROR)
        self.assertEqual(len(self.ds.device_profiles()), 0)

    def test_update_profile_metadata_failure_rolls_back_cache(self):
        self.ds.add_device_profile(_make_profile())
        profile = _make_profile()
        profile.description = "new description"
        self.fake.fail("update_device_profile")
        with self.assertRaises(Exception) as ctx:
            self.ds.update_device_profile(profile)
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.SERVER_ERROR)
        self.assertEqual(self.ds.get_profile_by_name("p1").description, "")

    def test_remove_profile_metadata_failure_rolls_back_cache(self):
        self.ds.add_device_profile(_make_profile())
        self.fake.fail("delete_device_profile")
        with self.assertRaises(Exception) as ctx:
            self.ds.remove_device_profile_by_name("p1")
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.SERVER_ERROR)
        self.assertTrue(self.ds.get_profile_by_name("p1").name == "p1")

    # -- ProvisionWatcher -----------------------------------------------------

    def test_add_watcher_writes_metadata_and_cache(self):
        watcher_id = self.ds.add_provision_watcher(_make_watcher())
        self.assertTrue(watcher_id.startswith("md-watcher-"))
        self.assertEqual(self.ds.get_provision_watcher_by_name("w1").name, "w1")

    def test_add_watcher_metadata_failure_rolls_back_cache(self):
        self.fake.fail("add_provision_watcher")
        with self.assertRaises(Exception) as ctx:
            self.ds.add_provision_watcher(_make_watcher())
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.SERVER_ERROR)
        self.assertEqual(len(self.ds.provision_watchers()), 0)

    def test_update_watcher_metadata_failure_rolls_back_cache(self):
        self.ds.add_provision_watcher(_make_watcher())
        watcher = _make_watcher()
        watcher.profile_name = "p1"
        self.fake.fail("update_provision_watcher")
        with self.assertRaises(Exception) as ctx:
            self.ds.update_provision_watcher(watcher)
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.SERVER_ERROR)
        self.assertEqual(self.ds.get_provision_watcher_by_name("w1").profile_name, "")

    def test_remove_watcher_metadata_failure_rolls_back_cache(self):
        self.ds.add_provision_watcher(_make_watcher())
        self.fake.fail("delete_provision_watcher")
        with self.assertRaises(Exception) as ctx:
            self.ds.remove_provision_watcher("w1")
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.SERVER_ERROR)
        self.assertTrue(self.ds.get_provision_watcher_by_name("w1").name == "w1")


if __name__ == "__main__":
    unittest.main()
