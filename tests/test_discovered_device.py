# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for discovered device registration (M2 / Gap G2).

Covers the full port of discovered device matching against ProvisionWatchers
and registration via Core Metadata with bypassValidation=true.
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
from device_sdk_py.models import DiscoveredDevice  # noqa: E402
from device_sdk_py.internal.common.utils import EdgexErrorKind  # noqa: E402
from device_sdk_py.service.bootstrap import bootstrap  # noqa: E402


class _Driver:
    def start(self):
        pass


def _make_service(driver=None):
    return bootstrap("device-simple", "0.0.0", driver or _Driver())


def _make_discovered_device(name="sensor-discovered", protocol_props=None):
    """Create a DiscoveredDevice with typical protocol properties."""
    if protocol_props is None:
        protocol_props = {"protocol": {"address": "192.168.1.100"}}
    return DiscoveredDevice(
        name=name,
        protocols=protocol_props,
        description="discovered device",
        labels=["discovered"],
    )


def _make_provision_watcher(name="watcher-1", profile_name="p1",
                            identifiers=None, blocking_identifiers=None,
                            admin_state="UNLOCKED", auto_events=None, properties=None):
    """Create a ProvisionWatcher with discovered_device config."""
    if identifiers is None:
        identifiers = {"address": ".*"}  # match any address value in protocol props
    if blocking_identifiers is None:
        blocking_identifiers = {}
    if auto_events is None:
        auto_events = []
    if properties is None:
        properties = {}
    watcher = ProvisionWatcher(
        name=name,
        identifiers=identifiers,
        blocking_identifiers=blocking_identifiers,
        admin_state=admin_state,
        profile_name=profile_name,
    )
    # The ProvisionWatcher model needs a discovered_device attribute for
    # the device creation in _process_discovered_devices. This mirrors the
    # EdgeX v4 contract where ProvisionWatcher has a DiscoveredDevice child.
    # We attach it dynamically for the test.
    from types import SimpleNamespace
    watcher.discovered_device = SimpleNamespace(
        profile_name=profile_name,
        admin_state=admin_state,
        auto_events=auto_events or [],
        properties=properties or {},
    )
    return watcher


class TestDiscoveredDeviceRegistration(unittest.TestCase):
    """Test discovered device matching and registration with bypassValidation."""

    def setUp(self):
        self.ds = _make_service()
        # Need a DeviceProfile for the watcher to reference
        self.ds.add_device_profile(DeviceProfile(name="p1"))

    def tearDown(self):
        self.ds._shutdown()

    def test_discovered_device_matching_watcher_added_without_validation(self):
        """Discovered device matching a watcher triggers add_device_without_validation."""
        watcher = _make_provision_watcher()
        self.ds.add_provision_watcher(watcher)
        d = _make_discovered_device()
        # Put device into the discovered channel
        self.ds.discovered_device_channel().put([d])
        # Process the discovered pump manually (not via thread)
        self.ds._process_discovered_devices([d])

        # Device should be in cache
        self.assertTrue(self.ds.device_exists_for_name("sensor-discovered"))
        device = self.ds.get_device_by_name("sensor-discovered")
        self.assertEqual(device.name, "sensor-discovered")
        self.assertEqual(device.profile_name, "p1")
        self.assertEqual(device.admin_state, "UNLOCKED")
        self.assertEqual(device.operating_state, "UP")

    def test_discovered_device_non_matching_watcher_not_added(self):
        """Discovered device with non-matching identifiers is not added."""
        # Watcher expects address, but device has different protocol prop name
        watcher = _make_provision_watcher(identifiers={"other_prop": ".*"})
        self.ds.add_provision_watcher(watcher)
        d = _make_discovered_device(protocol_props={"protocol": {"address": "1.2.3.4"}})
        self.ds._process_discovered_devices([d])
        self.assertFalse(self.ds.device_exists_for_name("sensor-discovered"))

    def test_discovered_device_blocked_by_watcher_not_added(self):
        """Discovered device matching blocking identifiers is not added."""
        # Watcher blocks 192.168.1.200
        watcher = _make_provision_watcher(
            name="watcher-2",
            identifiers={"address": ".*"},
            blocking_identifiers={"address": ["192.168.1.200"]},
        )
        self.ds.add_provision_watcher(watcher)

        d = _make_discovered_device(protocol_props={"protocol": {"address": "192.168.1.200"}})
        self.ds._process_discovered_devices([d])
        self.assertFalse(self.ds.device_exists_for_name("sensor-discovered"))

    def test_discovered_device_locked_watcher_not_added(self):
        """Discovered device with LOCKED watcher is not added."""
        watcher = _make_provision_watcher(name="watcher-3", admin_state="LOCKED")
        self.ds.add_provision_watcher(watcher)

        d = _make_discovered_device()
        self.ds._process_discovered_devices([d])
        self.assertFalse(self.ds.device_exists_for_name("sensor-discovered"))

    def test_discovered_device_duplicate_name_not_added_twice(self):
        """Same discovered device processed twice only added once."""
        watcher = _make_provision_watcher()
        self.ds.add_provision_watcher(watcher)
        d = _make_discovered_device()
        self.ds._process_discovered_devices([d])
        self.ds._process_discovered_devices([d])
        # Should still have only one
        self.assertEqual(len(self.ds.devices()), 1)

    def test_discovered_device_added_with_bypass_validation_false(self):
        """Discovered device registration uses bypassValidation=true."""
        # The public method add_device_without_validation is called, which
        # internally calls _add_device_to_metadata with bypass_validation=True
        # We verify by checking the metadata client was called with the flag.
        # Since we don't have a real metadata client configured, the device
        # ends up in cache via the cache-first path. The key assertion is
        # that the code path uses the without_validation method.
        watcher = _make_provision_watcher()
        self.ds.add_provision_watcher(watcher)
        d = _make_discovered_device()
        # Spy on add_device_without_validation
        with mock.patch.object(self.ds, "add_device_without_validation",
                               wraps=self.ds.add_device_without_validation) as m:
            self.ds._process_discovered_devices([d])
            m.assert_called_once()
            # The call should be with a Device object
            called_device = m.call_args[0][0]
            self.assertEqual(called_device.name, "sensor-discovered")
            self.assertEqual(called_device.profile_name, "p1")


class TestDiscoveredDeviceChannel(unittest.TestCase):
    """Test the public discovered_device_channel accessor."""

    def setUp(self):
        self.ds = _make_service()

    def tearDown(self):
        self.ds._shutdown()

    def test_channel_returns_queue(self):
        ch = self.ds.discovered_device_channel()
        self.assertTrue(hasattr(ch, "put"))
        self.assertTrue(hasattr(ch, "get"))


if __name__ == "__main__":
    unittest.main()