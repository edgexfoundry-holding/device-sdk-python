# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for public system-event API (M4 / Gap G4).

Covers the three public methods that publish system events to the EdgeX message bus:
- publish_device_discovery_progress_system_event
- publish_profile_scan_progress_system_event
- publish_generic_system_event
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


def _make_service(driver=None):
    return bootstrap("device-simple", "0.0.0", driver or _Driver())


class TestSystemEventAPI(unittest.TestCase):
    """Test the public system-event publishing API."""

    def setUp(self):
        self.ds = _make_service()
        # Need a mock messaging client
        self.mock_client = mock.MagicMock()
        self.ds._messaging_client = self.mock_client
        # Need a mock message bus config
        self.mock_config = mock.MagicMock()
        self.mock_config.base_topic_prefix = "edgex"
        self.ds._message_bus_config_obj = self.mock_config

    def tearDown(self):
        self.ds._shutdown()

    def test_publish_device_discovery_progress_system_event_calls_client(self):
        """publish_device_discovery_progress_system_event calls client.publish."""
        self.ds.publish_device_discovery_progress_system_event(50, 2, "Discovering...")
        self.mock_client.publish.assert_called_once()
        # Check the envelope and topic
        call_args = self.mock_client.publish.call_args
        envelope = call_args[0][0]
        topic = call_args[0][1]
        self.assertEqual(envelope.content_type, "application/json")
        self.assertIn("edgex/system-events/device-simple/device/discovery", topic)
        import json
        payload = json.loads(envelope.payload)
        self.assertEqual(payload["type"], "device")
        self.assertEqual(payload["action"], "discovery")
        self.assertEqual(payload["details"]["progress"], 50)
        self.assertEqual(payload["details"]["discoveredDeviceCount"], 2)
        self.assertEqual(payload["details"]["message"], "Discovering...")

    def test_publish_profile_scan_progress_system_event_calls_client(self):
        """publish_profile_scan_progress_system_event calls client.publish."""
        self.ds.publish_profile_scan_progress_system_event("req-123", 75, "Scanning...")
        self.mock_client.publish.assert_called_once()
        call_args = self.mock_client.publish.call_args
        envelope = call_args[0][0]
        topic = call_args[0][1]
        self.assertEqual(envelope.content_type, "application/json")
        self.assertIn("edgex/system-events/device-simple/device/profilescan", topic)
        import json
        payload = json.loads(envelope.payload)
        self.assertEqual(payload["type"], "device")
        self.assertEqual(payload["action"], "profilescan")
        self.assertEqual(payload["details"]["requestId"], "req-123")
        self.assertEqual(payload["details"]["progress"], 75)
        self.assertEqual(payload["details"]["message"], "Scanning...")

    def test_publish_generic_system_event_calls_client(self):
        """publish_generic_system_event calls client.publish with arbitrary type/action."""
        self.ds.publish_generic_system_event("device-profile", "add", {"name": "p1"})
        self.mock_client.publish.assert_called_once()
        call_args = self.mock_client.publish.call_args
        envelope = call_args[0][0]
        topic = call_args[0][1]
        self.assertEqual(envelope.content_type, "application/json")
        self.assertIn("edgex/system-events/device-simple/device-profile/add", topic)
        import json
        payload = json.loads(envelope.payload)
        self.assertEqual(payload["type"], "device-profile")
        self.assertEqual(payload["action"], "add")
        self.assertEqual(payload["details"]["name"], "p1")

    def test_publish_system_event_no_client_just_logs(self):
        """When no messaging client, methods should just log (no crash)."""
        self.ds._messaging_client = None
        self.ds._message_bus_config_obj = None
        # Should not raise
        self.ds.publish_device_discovery_progress_system_event(10, 1, "test")
        self.ds.publish_profile_scan_progress_system_event("req", 10, "test")
        self.ds.publish_generic_system_event("type", "action", {})

    def test_publish_system_event_no_config_just_logs(self):
        """When no message bus config, methods should just log."""
        self.ds._message_bus_config_obj = None
        # Should not raise
        self.ds.publish_device_discovery_progress_system_event(10, 1, "test")


class TestInternalProgressHelpers(unittest.TestCase):
    """Test the internal _publish_discovery_progress and _publish_profile_scan_progress."""

    def setUp(self):
        self.ds = _make_service()
        self.mock_client = mock.MagicMock()
        self.ds._messaging_client = self.mock_client
        self.mock_config = mock.MagicMock()
        self.mock_config.base_topic_prefix = "edgex"
        self.ds._message_bus_config_obj = self.mock_config

    def tearDown(self):
        self.ds._shutdown()

    def test_publish_discovery_progress(self):
        """_publish_discovery_progress publishes to system-events with device type and discovery action."""
        self.ds._publish_discovery_progress(25, 1, "Starting...")
        self.mock_client.publish.assert_called_once()
        call_args = self.mock_client.publish.call_args
        envelope = call_args[0][0]
        topic = call_args[0][1]
        self.assertIn("edgex/system-events/device-simple/device/discovery", topic)
        import json
        payload = json.loads(envelope.payload)
        self.assertEqual(payload["type"], "device")
        self.assertEqual(payload["action"], "discovery")
        self.assertEqual(payload["details"]["progress"], 25)
        self.assertEqual(payload["details"]["discoveredDeviceCount"], 1)
        self.assertEqual(payload["details"]["message"], "Starting...")

    def test_publish_profile_scan_progress(self):
        """_publish_profile_scan_progress publishes with requestId in details."""
        self.ds._publish_profile_scan_progress("req-456", 50, "Halfway")
        self.mock_client.publish.assert_called_once()
        call_args = self.mock_client.publish.call_args
        envelope = call_args[0][0]
        topic = call_args[0][1]
        self.assertIn("edgex/system-events/device-simple/device/profilescan", topic)
        import json
        payload = json.loads(envelope.payload)
        self.assertEqual(payload["type"], "device")
        self.assertEqual(payload["action"], "profilescan")
        self.assertEqual(payload["details"]["requestId"], "req-456")
        self.assertEqual(payload["details"]["progress"], 50)
        self.assertEqual(payload["details"]["message"], "Halfway")


if __name__ == "__main__":
    unittest.main()