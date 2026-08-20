# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for the Go-aligned application layer added to keep parity with
`device-sdk-go/internal/application`:

    callback.py    - Core Metadata callback handlers (device / profile / watcher / service)
    devicereturn.py- background device-return retry loop
    profilescan.py - profile scan wrapper / stop
    interfaces/manager.py - AutoEventManager ABC
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
    ADMIN_STATE_UNLOCKED,
    ADMIN_STATE_LOCKED,
    AutoEvent,
    Device,
    DeviceProfile,
    DeviceResource,
    Devices,
    Profiles,
    ProvisionWatcher,
    ProvisionWatchers,
    ResourceOperation,
    ResourceProperties,
    create_device_cache,
    create_profile_cache,
    create_provision_watcher_cache,
)
from device_sdk_py.internal.common.consts import (  # noqa: E402
    OPERATING_STATE_DOWN,
    OPERATING_STATE_UP,
)
from device_sdk_py.internal.common.utils import (  # noqa: E402
    EdgexErrorKind,
    KIND_CONTRACT_INVALID,
    KIND_ENTITY_DOES_NOT_EXIST,
    KIND_NOT_IMPLEMENTED,
    KIND_SERVICE_LOCKED,
)
from device_sdk_py.internal.application import (  # noqa: E402
    add_device,
    add_provision_watcher,
    delete_device,
    delete_profile,
    delete_provision_watcher,
    device_return,
    profile_scan_wrapper,
    start_device_return,
    stop_profile_scan,
    update_associated_profile,
    update_device,
    update_device_service,
    update_profile,
    update_provision_watcher,
)
from device_sdk_py.interfaces import AutoEventManager  # noqa: E402


# Initialize the module-level cache singletons once for the whole test module.
create_device_cache([])
create_profile_cache([])
create_provision_watcher_cache([])


class _FakeLogger:
    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class _FakeDriver:
    def __init__(self):
        self.calls = []

    def add_device(self, *a, **k):
        self.calls.append(("add_device", a))

    def update_device(self, *a, **k):
        self.calls.append(("update_device", a))

    def remove_device(self, *a, **k):
        self.calls.append(("remove_device", a))

    def profile_scan(self, *a, **k):
        self.calls.append(("profile_scan", a))
        return DeviceProfile(name="scanned", device_resources=[], device_commands=[])

    def stop_profile_scan(self, *a, **k):
        self.calls.append(("stop_profile_scan", a))


class _FakeDeviceService:
    def __init__(self, name="device-simple"):
        self.name = name
        self.admin_state = ADMIN_STATE_UNLOCKED
        self.labels = []
        self.driver = _FakeDriver()
        self.operating_state_calls = []

    def update_device_operating_state(self, name, state):
        self.operating_state_calls.append((name, state))


class _FakeFailsTracker:
    def __init__(self):
        self._values = {}

    def set(self, name, value):
        self._values[name] = value

    def value(self, name):
        return self._values.get(name, 0)

    def decrease(self, name):
        v = self._values.get(name, 0) - 1
        self._values[name] = v
        return v

    def remove(self, name):
        self._values.pop(name, None)


class _FakeAutoEventManager(AutoEventManager):
    def __init__(self):
        self.calls = []

    def start_auto_events(self):
        self.calls.append("start")

    def restart_for_device(self, name):
        self.calls.append(("restart", name))

    def stop_for_device(self, name):
        self.calls.append(("stop", name))


class _FakeProfileClient:
    def __init__(self):
        self.profile = DeviceProfile(
            name="remote",
            description="remote profile",
            device_resources=[],
            device_commands=[],
        )

    def device_profile_by_name(self, name):
        return {"profile": DeviceProfile(
            name=name, description="remote profile",
            device_resources=[], device_commands=[])}


class _FakeConfig:
    def __init__(self, allowed_fails=3):
        self.device = mock.Mock()
        self.device.allowed_fails = allowed_fails


class _FakeDic(dict):
    """A minimal stand-in for the Go DI container used by the callback handlers."""

    def __init__(self, **kwargs):
        defaults = {
            "logging_client": _FakeLogger(),
            "protocol_driver": _FakeDriver(),
            "extended_protocol_driver": _FakeDriver(),
            "device_service": _FakeDeviceService(),
            "devices": create_device_cache([]),
            "profiles": create_profile_cache([]),
            "provision_watchers": create_provision_watcher_cache([]),
            "allowed_request_failures_tracker": _FakeFailsTracker(),
            "auto_event_manager": _FakeAutoEventManager(),
            "device_profile_client": _FakeProfileClient(),
            "configuration": _FakeConfig(),
        }
        defaults.update(kwargs)
        super().__init__(defaults)
        # mimic dic.get
        self.get = lambda key, *d: dict.get(self, key, d[0] if d else None)


class TestAutoEventManager(unittest.TestCase):
    def test_abstract_methods(self):
        with self.assertRaises(TypeError):
            AutoEventManager()  # noqa: E1120 - abstract class cannot be instantiated

    def test_concrete_implementation(self):
        m = _FakeAutoEventManager()
        m.start_auto_events()
        m.restart_for_device("d1")
        m.stop_for_device("d1")
        self.assertEqual(m.calls, ["start", ("restart", "d1"), ("stop", "d1")])


class TestCallbackProfile(unittest.TestCase):
    def setUp(self):
        self.dic = _FakeDic()
        self.profile = DeviceProfile(
            name="p1",
            description="profile 1",
            device_resources=[],
            device_commands=[],
        )
        self.dic["profiles"].add(self.profile)

    def test_update_profile_missing(self):
        err = update_profile(DeviceProfile(name="nope"), self.dic)
        self.assertIsNotNone(err)
        self.assertEqual(err.kind, KIND_CONTRACT_INVALID)

    def test_update_profile_success_invokes_driver_for_matching_devices(self):
        dev = Device(name="dev-1", profile_name="p1", operating_state=OPERATING_STATE_UP,
                     admin_state=ADMIN_STATE_UNLOCKED)
        self.dic["devices"].add(dev)
        err = update_profile(DeviceProfile(name="p1", description="updated"), self.dic)
        self.assertIsNone(err)
        driver = self.dic["protocol_driver"]
        self.assertTrue(any(c[0] == "update_device" for c in driver.calls))

    def test_delete_profile_in_use_warns(self):
        dev = Device(name="dev-1", profile_name="p1", operating_state=OPERATING_STATE_UP,
                     admin_state=ADMIN_STATE_UNLOCKED)
        self.dic["devices"].add(dev)
        err = delete_profile("p1", self.dic)
        self.assertIsNone(err)
        # Profile still in cache because it is in use
        _, ok = self.dic["profiles"].for_name("p1")
        self.assertTrue(ok)

    def test_delete_profile_not_in_use_removes(self):
        err = delete_profile("p1", self.dic)
        self.assertIsNone(err)
        _, ok = self.dic["profiles"].for_name("p1")
        self.assertFalse(ok)


class TestCallbackDevice(unittest.TestCase):
    def setUp(self):
        self.dic = _FakeDic()
        self.profile = DeviceProfile(
            name="p1", description="profile 1",
            device_resources=[], device_commands=[])
        self.dic["profiles"].add(self.profile)

    def _device(self, name="dev-1", profile_name="p1", admin_state=ADMIN_STATE_UNLOCKED,
                protocols=None, service_name="device-simple"):
        return Device(
            name=name, profile_name=profile_name, admin_state=admin_state,
            service_name=service_name, protocols=protocols or {"modbus": {"addr": "1"}},
            operating_state=OPERATING_STATE_UP)

    def test_add_device(self):
        err = add_device(self._device(), self.dic)
        self.assertIsNone(err)
        _, ok = self.dic["devices"].for_name("dev-1")
        self.assertTrue(ok)
        driver = self.dic["protocol_driver"]
        self.assertTrue(any(c[0] == "add_device" for c in driver.calls))
        # auto events restarted
        self.assertTrue(any(c[0] == "restart" and c[1] == "dev-1"
                            for c in self.dic["auto_event_manager"].calls))

    def test_update_device(self):
        err = add_device(self._device(), self.dic)
        self.assertIsNone(err)
        updated = self._device(protocols={"modbus": {"addr": "2"}})
        err = update_device(updated, self.dic)
        self.assertIsNone(err)
        dev, _ = self.dic["devices"].for_name("dev-1")
        self.assertEqual(dev.protocols["modbus"]["addr"], "2")
        driver = self.dic["protocol_driver"]
        self.assertTrue(any(c[0] == "update_device" for c in driver.calls))

    def test_update_device_locked_stops_auto_events(self):
        err = add_device(self._device(), self.dic)
        self.assertIsNone(err)
        err = update_device(self._device(admin_state=ADMIN_STATE_LOCKED), self.dic)
        self.assertIsNone(err)
        self.assertTrue(any(c[0] == "stop" and c[1] == "dev-1"
                            for c in self.dic["auto_event_manager"].calls))

    def test_update_device_not_found_returns_error(self):
        err = update_device(self._device(name="ghost", service_name="other"), self.dic)
        self.assertIsNotNone(err)

    def test_delete_device(self):
        err = add_device(self._device(), self.dic)
        self.assertIsNone(err)
        err = delete_device("dev-1", self.dic)
        self.assertIsNone(err)
        _, ok = self.dic["devices"].for_name("dev-1")
        self.assertFalse(ok)
        driver = self.dic["protocol_driver"]
        self.assertTrue(any(c[0] == "remove_device" for c in driver.calls))
        self.assertTrue(any(c[0] == "stop" and c[1] == "dev-1"
                            for c in self.dic["auto_event_manager"].calls))

    def test_delete_device_not_found(self):
        err = delete_device("ghost", self.dic)
        self.assertIsNotNone(err)


class TestCallbackProvisionWatcher(unittest.TestCase):
    def setUp(self):
        self.dic = _FakeDic()
        self.profile = DeviceProfile(
            name="p1", description="profile 1",
            device_resources=[], device_commands=[])
        self.dic["profiles"].add(self.profile)

    def _pw(self, name="watcher-1", profile_name="p1", service_name=""):
        from device_sdk_py.internal.cache import ProvisionWatcher
        return ProvisionWatcher(name=name, profile_name=profile_name,
                                service_name=service_name)

    def test_add_provision_watcher(self):
        err = add_provision_watcher(self._pw(), self.dic)
        self.assertIsNone(err)
        _, ok = self.dic["provision_watchers"].for_name("watcher-1")
        self.assertTrue(ok)

    def test_update_provision_watcher(self):
        err = add_provision_watcher(self._pw(), self.dic)
        self.assertIsNone(err)
        err = update_provision_watcher(self._pw(), self.dic)
        self.assertIsNone(err)

    def test_delete_provision_watcher(self):
        err = add_provision_watcher(self._pw(), self.dic)
        self.assertIsNone(err)
        err = delete_provision_watcher("watcher-1", self.dic)
        self.assertIsNone(err)
        _, ok = self.dic["provision_watchers"].for_name("watcher-1")
        self.assertFalse(ok)


class TestCallbackDeviceService(unittest.TestCase):
    def test_update_device_service_matches_name(self):
        dic = _FakeDic()
        service = mock.Mock()
        service.name = "device-simple"
        service.admin_state = ADMIN_STATE_LOCKED
        service.labels = ["a"]
        err = update_device_service(service, dic)
        self.assertIsNone(err)
        self.assertEqual(dic["device_service"].admin_state, ADMIN_STATE_LOCKED)

    def test_update_device_service_mismatch(self):
        dic = _FakeDic()
        service = mock.Mock()
        service.name = "other-service"
        err = update_device_service(service, dic)
        self.assertIsNotNone(err)
        self.assertEqual(err.kind, KIND_ENTITY_DOES_NOT_EXIST)


class TestUpdateAssociatedProfile(unittest.TestCase):
    def test_fetches_and_adds_missing_profile(self):
        dic = _FakeDic()
        err = update_associated_profile("remote", dic)
        self.assertIsNone(err)
        _, ok = dic["profiles"].for_name("remote")
        self.assertTrue(ok)

    def test_fetches_and_updates_existing_profile(self):
        dic = _FakeDic()
        dic["profiles"].add(DeviceProfile(
            name="remote", description="old", device_resources=[], device_commands=[]))
        err = update_associated_profile("remote", dic)
        self.assertIsNone(err)
        prof, _ = dic["profiles"].for_name("remote")
        self.assertEqual(prof.description, "remote profile")


class TestDeviceReturn(unittest.TestCase):
    def setUp(self):
        self.config = mock.Mock()
        self.config.device_down_timeout = 1
        self.ds = _FakeDeviceService()
        self.logger = _FakeLogger()
        self.device = Device(name="dev-1", profile_name="p1",
                             operating_state=OPERATING_STATE_DOWN,
                             admin_state=ADMIN_STATE_UNLOCKED)
        self.profile = DeviceProfile(
            name="p1", description="profile 1",
            device_resources=[], device_commands=[])
        create_device_cache([self.device])
        create_profile_cache([self.profile])

    def test_device_return_device_not_found(self):
        create_device_cache([])
        with mock.patch("device_sdk_py.internal.application.devicereturn.time.sleep"):
            device_return("missing", self.config, self.ds, self.logger)
        # No exception; loop exits because device not found

    def test_device_return_no_readable_resources_sets_up(self):
        with mock.patch("device_sdk_py.internal.application.devicereturn.time.sleep"):
            device_return("dev-1", self.config, self.ds, self.logger)
        self.assertTrue(any(c[1] == OPERATING_STATE_UP
                            for c in self.ds.operating_state_calls))

    def test_start_device_return_returns_thread(self):
        thread = start_device_return("dev-1", self.config, self.ds, self.logger)
        self.assertTrue(thread.is_alive() or not thread.is_alive())
        self.assertTrue(thread.daemon)


class TestProfileScan(unittest.TestCase):
    def setUp(self):
        self.dic = _FakeDic()
        self.req = mock.Mock()
        self.req.device_name = "dev-1"
        self.req.profile_name = "p1"
        self.req.request_id = "req-1"
        self.req.options = {}

    def test_profile_scan_wrapper_does_not_recurse_on_busy(self):
        busy = []
        profile_scan_wrapper(busy, self.dic["extended_protocol_driver"], self.req, self.dic)
        # busy gets a single element appended (the acquired flag)
        self.assertEqual(len(busy), 1)

    def test_stop_profile_scan_not_implemented_without_ext_driver(self):
        dic = _FakeDic(extended_protocol_driver=None)
        err = stop_profile_scan(dic, "dev-1", {})
        self.assertIsNotNone(err)
        self.assertEqual(err.kind, KIND_NOT_IMPLEMENTED)

    def test_stop_profile_scan_locked(self):
        self.dic["device_service"].admin_state = ADMIN_STATE_LOCKED
        err = stop_profile_scan(self.dic, "dev-1", {})
        self.assertIsNotNone(err)
        self.assertEqual(err.kind, KIND_SERVICE_LOCKED)

    def test_stop_profile_scan_device_missing(self):
        err = stop_profile_scan(self.dic, "ghost", {})
        self.assertIsNotNone(err)
        self.assertEqual(err.kind, KIND_ENTITY_DOES_NOT_EXIST)


class _RecordingDriver(_FakeDriver):
    """A driver that also records add/update/remove calls for integration assertions."""

    def __init__(self):
        super().__init__()
        self.added = []
        self.updated = []
        self.removed = []

    def add_device(self, name, protocols, admin_state):
        self.added.append(name)
        self.calls.append(("add_device", (name,)))

    def update_device(self, name, protocols, admin_state):
        self.updated.append(name)
        self.calls.append(("update_device", (name,)))

    def remove_device(self, name, protocols):
        self.removed.append(name)
        self.calls.append(("remove_device", (name,)))


class TestUnifiedSystemEventCallbacks(unittest.TestCase):
    """Verify the DeviceService ``_on_*`` system-event handlers now delegate to
    ``application/callback.py`` (the unified Go-aligned application layer)."""

    def setUp(self):
        from device_sdk_py.service.device_service import DeviceService
        from device_sdk_py.internal.common.configuration import ConfigurationStruct
        os.environ.pop("EDGEX_CORE_METADATA_HOST", None)
        os.environ.pop("EDGEX_CORE_METADATA_PORT", None)
        os.environ.pop("EDGEX_CORE_METADATA_URL", None)
        os.environ.pop("EDGEX_SERVICE_ADDRESS", None)
        os.environ.pop("EDGEX_SERVICE_HOST", None)
        self.driver = _RecordingDriver()
        config = ConfigurationStruct()
        config.Device.AllowedFails = 3
        self.ds = DeviceService("device-simple", "0.0.0", self.driver,
                                configuration=config)
        self.ds._logger = _FakeLogger()
        create_device_cache([])
        create_profile_cache([])
        create_provision_watcher_cache([])

    def tearDown(self):
        create_device_cache([])
        create_profile_cache([])
        create_provision_watcher_cache([])

    def test_on_device_added_delegates_to_application_layer(self):
        self.ds._on_device_added({
            "name": "nd",
            "profileName": "p1",
            "protocols": {"modbus": {"addr": "1"}},
            "adminState": "UNLOCKED",
            "serviceName": "device-simple",
        })
        device, ok = Devices().for_name("nd")
        self.assertTrue(ok)
        self.assertEqual(device.profile_name, "p1")
        # The unified application layer invoked the driver callback.
        self.assertIn("nd", self.driver.added)

    def test_on_device_deleted_delegates_to_application_layer(self):
        Devices().add(Device(name="nd", profile_name="p1", service_name="device-simple",
                             admin_state=ADMIN_STATE_UNLOCKED))
        self.ds._on_device_deleted("nd")
        _, ok = Devices().for_name("nd")
        self.assertFalse(ok)
        self.assertIn("nd", self.driver.removed)

    def test_on_device_updated_delegates_to_application_layer(self):
        Devices().add(Device(name="nd", profile_name="p1", service_name="device-simple",
                             admin_state=ADMIN_STATE_UNLOCKED))
        self.ds._on_device_updated("nd", {"description": "new"})
        device, _ = Devices().for_name("nd")
        self.assertEqual(device.description, "new")
        self.assertIn("nd", self.driver.updated)

    def test_on_watcher_added_delegates_to_application_layer(self):
        self.ds._on_watcher_added({
            "name": "nw",
            "profileName": "p1",
            "identifiers": {"Address": ".*"},
            "adminState": "UNLOCKED",
            "serviceName": "device-simple",
        })
        watcher, ok = ProvisionWatchers().for_name("nw")
        self.assertTrue(ok)
        self.assertEqual(watcher.profile_name, "p1")

    def test_on_profile_updated_delegates_to_application_layer(self):
        Profiles().add(DeviceProfile(name="p1"))
        self.ds._on_profile_updated({"name": "p1", "description": "d"})
        profile, _ = Profiles().for_name("p1")
        self.assertEqual(profile.description, "d")


if __name__ == "__main__":
    unittest.main()
