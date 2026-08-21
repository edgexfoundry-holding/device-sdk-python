# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for the service bootstrap and DeviceService CRUD flow.

Runs with either pytest (if installed) or the stdlib runner::

    python -m unittest tests.test_bootstrap
    # or
    python -m pytest tests
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.internal.cache import (  # noqa: E402
    Device,
    DeviceProfile,
    ProvisionWatcher,
)
from device_sdk_py.interfaces.service import DeviceServiceSDK  # noqa: E402
from device_sdk_py.internal.common.utils import EdgexErrorKind  # noqa: E402
from device_sdk_py.service.bootstrap import bootstrap  # noqa: E402


class _Driver:
    def start(self):
        pass


def _make_service():
    return bootstrap("device-simple", "0.0.0", _Driver())


class TestBootstrap(unittest.TestCase):
    def test_bootstrap_returns_device_service(self):
        ds = _make_service()
        self.assertEqual(ds.name(), "device-simple")
        self.assertEqual(ds.version(), "0.0.0")
        self.assertIsInstance(ds, DeviceServiceSDK)


class TestDeviceCRUD(unittest.TestCase):
    def setUp(self):
        self.ds = _make_service()

    def test_add_and_get_device(self):
        d = Device(name="sensor-01", profile_name="p1")
        self.ds.add_device(d)
        self.assertEqual(len(self.ds.devices()), 1)
        fetched = self.ds.get_device_by_name("sensor-01")
        self.assertEqual(fetched.name, "sensor-01")
        self.assertTrue(self.ds.device_exists_for_name("sensor-01"))

    def test_add_device_duplicate_raises(self):
        d = Device(name="sensor-01", profile_name="p1")
        self.ds.add_device(d)
        with self.assertRaises(Exception) as ctx:
            self.ds.add_device(d)
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.DUPLICATE_NAME)

    def test_update_operating_state_persists_in_cache(self):
        self.ds.add_device(Device(name="sensor-01", profile_name="p1"))
        self.ds.update_device_operating_state("sensor-01", "DISABLED")
        self.assertEqual(self.ds.get_device_by_name("sensor-01").operating_state, "DISABLED")

    def test_remove_device(self):
        self.ds.add_device(Device(name="sensor-01", profile_name="p1"))
        self.ds.remove_device_by_name("sensor-01")
        self.assertEqual(len(self.ds.devices()), 0)
        self.assertFalse(self.ds.device_exists_for_name("sensor-01"))


class TestProfileAndWatcherCRUD(unittest.TestCase):
    def setUp(self):
        self.ds = _make_service()

    def test_profile_add_update_remove(self):
        self.ds.add_device_profile(DeviceProfile(name="p1"))
        self.assertEqual(len(self.ds.device_profiles()), 1)
        self.ds.update_device_profile(DeviceProfile(name="p1"))
        self.assertEqual(len(self.ds.device_profiles()), 1)
        self.ds.remove_device_profile_by_name("p1")
        self.assertEqual(len(self.ds.device_profiles()), 0)

    def test_watcher_add_update_remove(self):
        self.ds.add_provision_watcher(ProvisionWatcher(name="w1"))
        self.assertEqual(len(self.ds.provision_watchers()), 1)
        self.ds.update_provision_watcher(ProvisionWatcher(name="w1"))
        self.assertEqual(len(self.ds.provision_watchers()), 1)
        self.ds.remove_provision_watcher("w1")
        self.assertEqual(len(self.ds.provision_watchers()), 0)


class TestController(unittest.TestCase):
    def test_ping_and_version(self):
        ds = _make_service()
        ds._init_http_controller()
        app = ds.controller.app()
        from starlette.testclient import TestClient
        client = TestClient(app)
        ping = client.get("/api/v3/ping")
        self.assertEqual(ping.status_code, 200)
        self.assertEqual(ping.json()["serviceName"], "device-simple")
        version = client.get("/api/v3/version")
        self.assertEqual(version.status_code, 200)
        self.assertEqual(version.json()["apiVersion"], "v3")


if __name__ == "__main__":
    unittest.main()
