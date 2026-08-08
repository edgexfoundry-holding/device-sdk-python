# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for the ExtendedProtocolDriver interface and ProfileScan DTO (M11).

Covers:
- ProfileScanRequest DTO round-trip (from_dict / to_dict)
- ExtendedProtocolDriver abstract interface
- SimpleDriver example implements both ProtocolDriver and ExtendedProtocolDriver
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.interfaces import ExtendedProtocolDriver, ProtocolDriver  # noqa: E402
from device_sdk_py.models.profilescan import ProfileScanRequest  # noqa: E402


class TestProfileScanRequest(unittest.TestCase):
    """Test the ProfileScanRequest DTO mirroring go-mod-core-contracts."""

    def test_defaults(self):
        req = ProfileScanRequest()
        self.assertEqual(req.base_request.api_version, "v3")
        self.assertEqual(req.device_name, "")
        self.assertEqual(req.profile_name, "")
        self.assertEqual(req.options, {})

    def test_roundtrip(self):
        req = ProfileScanRequest.from_dict(
            {
                "apiVersion": "v3",
                "requestId": "abc-123",
                "deviceName": "sensor-01",
                "profileName": "new-profile",
                "options": {"timeout": "5s"},
            }
        )
        self.assertEqual(req.request_id, "abc-123")
        self.assertEqual(req.device_name, "sensor-01")
        self.assertEqual(req.profile_name, "new-profile")
        self.assertEqual(req.options, {"timeout": "5s"})

        data = req.to_dict()
        self.assertEqual(data["requestId"], "abc-123")
        self.assertEqual(data["deviceName"], "sensor-01")
        self.assertEqual(data["profileName"], "new-profile")
        self.assertEqual(data["options"], {"timeout": "5s"})

    def test_from_dict_missing_keys(self):
        req = ProfileScanRequest.from_dict({"deviceName": "x"})
        self.assertEqual(req.request_id, "")
        self.assertEqual(req.profile_name, "")
        self.assertEqual(req.options, {})

    def test_request_id_setter(self):
        req = ProfileScanRequest()
        req.request_id = "new-id"
        self.assertEqual(req.request_id, "new-id")
        self.assertEqual(req.base_request.request_id, "new-id")


class TestExtendedProtocolDriverInterface(unittest.TestCase):
    """Test the ExtendedProtocolDriver abstract interface."""

    def test_cannot_instantiate_abstract(self):
        with self.assertRaises(TypeError):
            ExtendedProtocolDriver()

    def test_abstract_methods_present(self):
        for method in ("profile_scan", "stop_device_discovery", "stop_profile_scan"):
            self.assertIn(method, ExtendedProtocolDriver.__abstractmethods__)

    def test_class_extends_protocol_driver_abc(self):
        self.assertTrue(issubclass(ExtendedProtocolDriver, ProtocolDriver))
        self.assertTrue(issubclass(ExtendedProtocolDriver.__bases__[0], ProtocolDriver))


class _FullDriver(ExtendedProtocolDriver):
    """A driver implementing every abstract method."""

    def initialize(self, sdk):
        pass

    def handle_read_commands(self, device_name, protocols, reqs):
        return []

    def handle_write_commands(self, device_name, protocols, reqs, params):
        pass

    def start(self):
        pass

    def stop(self, force):
        pass

    def add_device(self, device_name, protocols, admin_state):
        pass

    def update_device(self, device_name, protocols, admin_state):
        pass

    def remove_device(self, device_name, protocols):
        pass

    def discover(self):
        pass

    def validate_device(self, device):
        pass

    def get_device_profile(self, name):
        return None

    def profile_scan(self, device_name, profile_name, request_id, options):
        return None

    def stop_device_discovery(self, request_id, options):
        pass

    def stop_profile_scan(self, device_name, options):
        pass


class TestFullDriver(unittest.TestCase):
    """Test that a driver implementing all methods can be instantiated."""

    def test_instantiable(self):
        driver = _FullDriver()
        self.assertIsInstance(driver, ExtendedProtocolDriver)


if __name__ == "__main__":
    unittest.main()
