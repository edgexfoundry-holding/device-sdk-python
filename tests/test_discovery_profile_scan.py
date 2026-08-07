# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for device discovery and profile scan (M3 / Gap G3).

Covers the REST endpoints for triggering/stopping discovery and profile scan,
and the driver hooks that the DeviceService wires.
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
)
from device_sdk_py.internal.common.utils import EdgexErrorKind  # noqa: E402
from device_sdk_py.service.bootstrap import bootstrap  # noqa: E402


class _Driver:
    def start(self):
        pass


class _DiscoveringDriver(_Driver):
    """A driver that tracks discovery/profile-scan calls."""

    def __init__(self):
        self.discovered = []
        self.profile_scanned = []
        self._stop_discovery = False
        self._stop_profile_scan = False

    def discover(self):
        # Simulate discovery by putting a device into the SDK channel
        # (The actual channel is set via initialize())
        pass

    def validate_device(self, device):
        pass


def _make_service(driver=None):
    return bootstrap("device-simple", "0.0.0", driver or _Driver())


class _MockDiscoveringDriver(_Driver):
    """Driver that puts discovered devices into the channel when discover() is called."""

    def __init__(self, ds):
        self.ds = ds
        self.discover_called = 0

    def start(self):
        pass

    def discover(self):
        self.discover_called += 1
        if hasattr(self, 'ds') and self.ds is not None:
            from device_sdk_py.models import DiscoveredDevice
            d = DiscoveredDevice(
                name="discovered-sensor",
                protocols={"protocol": {"address": "192.168.1.50"}},
                description="auto-discovered",
                labels=["auto"],
            )
            self.ds.discovered_device_channel().put([d])

    def validate_device(self, device):
        pass


class TestDiscoveryEndpoints(unittest.TestCase):
    """Test POST /api/v3/discovery and DELETE /api/v3/discovery endpoints."""

    def setUp(self):
        self.ds = _make_service()
        self.ds._init_http_controller()
        app = self.ds.controller.app()
        from starlette.testclient import TestClient
        self.client = TestClient(app)

    def tearDown(self):
        self.ds._shutdown()

    def test_discovery_disabled_returns_503(self):
        # discovery is disabled by default in config
        resp = self.client.post("/api/v3/discovery")
        self.assertEqual(resp.status_code, 503)

    def test_discovery_enabled_triggers_driver_discover(self):
        # Need a driver with discover() that puts devices in channel
        driver = _MockDiscoveringDriver(self.ds)
        self.ds = _make_service(driver)
        self.ds._init_http_controller()
        app = self.ds.controller.app()
        from starlette.testclient import TestClient
        self.client = TestClient(app)

        # Enable discovery in config by setting device.discovery.enabled
        # We can't easily do this without full config, so mock the check
        with mock.patch.object(self.ds.controller, "_discovery_enabled", return_value=True):
            resp = self.client.post("/api/v3/discovery")
            # Accepted because discover runs in background
            self.assertEqual(resp.status_code, 202)
            # Driver discover should have been called
            self.assertEqual(driver.discover_called, 1)

    def test_stop_discovery_returns_ok_when_handler_exists(self):
        # The stop handler is now implemented
        resp = self.client.delete("/api/v3/discovery")
        # Should return 200 (the handler exists and runs)
        self.assertEqual(resp.status_code, 200)


class TestProfileScanEndpoints(unittest.TestCase):
    """Test POST /api/v3/profilescan and DELETE /api/v3/profilescan/device/{name}."""

    def setUp(self):
        self.ds = _make_service()
        # Need a device in cache for profile scan
        self.ds.add_device(Device(name="sensor-01", profile_name="p1"))
        self.ds.add_device_profile(DeviceProfile(name="p1"))
        self.ds._init_http_controller()
        app = self.ds.controller.app()
        from starlette.testclient import TestClient
        self.client = TestClient(app)

    def tearDown(self):
        self.ds._shutdown()

    def test_profile_scan_device_not_found(self):
        resp = self.client.post("/api/v3/profilescan", json={"deviceName": "nonexistent"})
        self.assertEqual(resp.status_code, 404)

    def test_profile_scan_profile_duplicated(self):
        resp = self.client.post("/api/v3/profilescan", json={"deviceName": "sensor-01", "profileName": "p1"})
        self.assertEqual(resp.status_code, 409)

    def test_profile_scan_returns_accepted_when_handler_exists(self):
        # The profile scan handler is now implemented
        resp = self.client.post("/api/v3/profilescan", json={"deviceName": "sensor-01", "profileName": "new-profile"})
        # Should return 202 Accepted (handler runs in background)
        self.assertEqual(resp.status_code, 202)

    def test_stop_profile_scan_returns_ok_when_handler_exists(self):
        # The stop profile scan handler is now implemented
        resp = self.client.delete("/api/v3/profilescan/device/sensor-01")
        self.assertEqual(resp.status_code, 200)


class TestDiscoveryProgressEvents(unittest.TestCase):
    """Test that discovery/profile-scan progress events are published."""

    def setUp(self):
        self.driver = _MockDiscoveringDriver(None)  # will be replaced
        self.ds = _make_service(self.driver)
        # Enable discovery by mocking the config check
        self.driver.ds = self.ds
        self.ds._init_http_controller()
        app = self.ds.controller.app()
        from starlette.testclient import TestClient
        self.client = TestClient(app)

    def tearDown(self):
        self.ds._shutdown()

    def test_discovery_publishes_start_and_complete_events(self):
        # Spy on _publish_discovery_progress
        with mock.patch.object(self.ds, "_publish_discovery_progress") as m:
            with mock.patch.object(self.ds.controller, "_discovery_enabled", return_value=True):
                resp = self.client.post("/api/v3/discovery")
                self.assertEqual(resp.status_code, 202)
                # Give the background thread time to run
                import time
                time.sleep(0.1)
                # Should have been called with progress 0 (start) and 100 (complete)
                calls = [call.args for call in m.call_args_list]
                self.assertTrue(any(c[0] == 0 for c in calls), "start progress 0 not published")
                self.assertTrue(any(c[0] == 100 for c in calls), "complete progress 100 not published")


if __name__ == "__main__":
    unittest.main()