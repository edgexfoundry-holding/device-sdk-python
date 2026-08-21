# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Extended unit tests for the internal caches and the file-based provisioning loaders.

Complements `test_metadata_writeback.py`, `test_device_service_metadata.py` and
`test_command_application_extended.py` by targeting the branches those leave
uncovered: duplicate / missing / empty cache operations, the admin and operating
state transition guards, last-connected tracking, `check_profile_not_used`, regex
resource lookup, the `check_and_add` no-op, singleton access before initialization,
and every coercion / scan / normalize / error branch of `internal/provision.py`.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import device_sdk_py.internal.cache.devices as devices_module  # noqa: E402
import device_sdk_py.internal.cache.profiles as profiles_module  # noqa: E402
import device_sdk_py.internal.cache.provisionwatchers as watchers_module  # noqa: E402
import device_sdk_py.internal.provision as provision  # noqa: E402
from device_sdk_py.internal.cache import (  # noqa: E402
    ADMIN_STATE_LOCKED,
    ADMIN_STATE_UNLOCKED,
    AutoEvent,
    CacheError,
    CacheErrorKind,
    Device,
    DeviceCommand,
    DeviceProfile,
    DeviceResource,
    ProvisionWatcher,
    ResourceOperation,
    ResourceProperties,
)
from device_sdk_py.internal.cache.devices import create_device_cache  # noqa: E402
from device_sdk_py.internal.cache.profiles import create_profile_cache  # noqa: E402
from device_sdk_py.internal.cache.provisionwatchers import (  # noqa: E402
    create_provision_watcher_cache,
)


def _device(name="d1", profile_name="p1"):
    return Device(name=name, profile_name=profile_name)


def _profile(name="prof"):
    return DeviceProfile(
        name=name,
        device_resources=[
            DeviceResource(name="temp",
                           properties=ResourceProperties(value_type="Float64")),
            DeviceResource(name="sensor"),
        ],
        device_commands=[
            DeviceCommand(name="cmd1", read_write="RW",
                          resource_operations=[
                              ResourceOperation(device_resource="temp")]),
        ],
    )


def _watcher(name="w1"):
    return ProvisionWatcher(name=name, profile_name="p1")


class CacheExtendedBase(unittest.TestCase):
    """Fresh caches for every test, re-initialized afterwards to isolate tests."""

    def setUp(self):
        create_device_cache([])
        create_profile_cache([])
        create_provision_watcher_cache([])

    def tearDown(self):
        create_device_cache([])
        create_profile_cache([])
        create_provision_watcher_cache([])


class TestCacheDevice(CacheExtendedBase):
    """`DeviceCache` add/update/remove/for_name/all and state tracking edge cases."""

    def test_create_with_devices_records_last_connected(self):
        create_device_cache([_device("d1"), _device("d2")])
        self.assertEqual(devices_module.Devices().get_last_connected_by_name("d1"), 0)
        self.assertEqual(devices_module.Devices().get_last_connected_by_name("d2"), 0)
        self.assertEqual(len(devices_module.Devices().all()), 2)

    def test_for_name_missing_returns_empty_device(self):
        device, ok = devices_module.Devices().for_name("ghost")
        self.assertFalse(ok)
        self.assertEqual(device.name, "")

    def test_for_name_returns_clone(self):
        devices_module.Devices().add(_device("d1"))
        clone, ok = devices_module.Devices().for_name("d1")
        self.assertTrue(ok)
        clone.description = "mutated"
        stored, _ = devices_module.Devices().for_name("d1")
        self.assertEqual(stored.description, "")

    def test_all_returns_clones(self):
        devices_module.Devices().add(_device("d1"))
        clone = devices_module.Devices().all()[0]
        clone.labels.append("hacked")
        stored, _ = devices_module.Devices().for_name("d1")
        self.assertEqual(stored.labels, [])

    def test_add_duplicate_raises(self):
        devices_module.Devices().add(_device("d1"))
        with self.assertRaises(CacheError) as ctx:
            devices_module.Devices().add(_device("d1"))
        self.assertEqual(ctx.exception.kind, CacheErrorKind.DUPLICATE_NAME)

    def test_update_replaces_device(self):
        devices_module.Devices().add(_device("d1"))
        updated = _device("d1")
        updated.description = "new"
        devices_module.Devices().update(updated)
        stored, _ = devices_module.Devices().for_name("d1")
        self.assertEqual(stored.description, "new")

    def test_update_missing_raises(self):
        with self.assertRaises(CacheError) as ctx:
            devices_module.Devices().update(_device("ghost"))
        self.assertEqual(ctx.exception.kind, CacheErrorKind.ENTITY_DOES_NOT_EXIST)

    def test_remove_by_name_ok(self):
        devices_module.Devices().add(_device("d1"))
        devices_module.Devices().remove_by_name("d1")
        _, ok = devices_module.Devices().for_name("d1")
        self.assertFalse(ok)

    def test_remove_missing_raises(self):
        with self.assertRaises(CacheError) as ctx:
            devices_module.Devices().remove_by_name("ghost")
        self.assertEqual(ctx.exception.kind, CacheErrorKind.ENTITY_DOES_NOT_EXIST)

    def test_update_admin_state_ok(self):
        devices_module.Devices().add(_device("d1"))
        devices_module.Devices().update_admin_state("d1", ADMIN_STATE_LOCKED)
        stored, _ = devices_module.Devices().for_name("d1")
        self.assertEqual(stored.admin_state, ADMIN_STATE_LOCKED)

    def test_update_admin_state_invalid_raises(self):
        devices_module.Devices().add(_device("d1"))
        with self.assertRaises(CacheError) as ctx:
            devices_module.Devices().update_admin_state("d1", "BOGUS")
        self.assertEqual(ctx.exception.kind, CacheErrorKind.CONTRACT_INVALID)

    def test_update_admin_state_missing_raises(self):
        with self.assertRaises(CacheError) as ctx:
            devices_module.Devices().update_admin_state("ghost", ADMIN_STATE_LOCKED)
        self.assertEqual(ctx.exception.kind, CacheErrorKind.ENTITY_DOES_NOT_EXIST)

    def test_update_operating_state_ok(self):
        devices_module.Devices().add(_device("d1"))
        for state in ("UP", "DOWN", "DISABLED"):
            devices_module.Devices().update_operating_state("d1", state)
            stored, _ = devices_module.Devices().for_name("d1")
            self.assertEqual(stored.operating_state, state)

    def test_update_operating_state_invalid_raises(self):
        devices_module.Devices().add(_device("d1"))
        with self.assertRaises(CacheError) as ctx:
            devices_module.Devices().update_operating_state("d1", "SOMEWHAT_UP")
        self.assertEqual(ctx.exception.kind, CacheErrorKind.CONTRACT_INVALID)

    def test_update_operating_state_missing_raises(self):
        with self.assertRaises(CacheError) as ctx:
            devices_module.Devices().update_operating_state("ghost", "UP")
        self.assertEqual(ctx.exception.kind, CacheErrorKind.ENTITY_DOES_NOT_EXIST)

    def test_device_exists(self):
        devices_module.Devices().add(_device("d1"))
        self.assertTrue(devices_module.Devices().device_exists("d1"))
        self.assertFalse(devices_module.Devices().device_exists("ghost"))

    def test_set_and_get_last_connected(self):
        devices_module.Devices().add(_device("d1"))
        self.assertEqual(devices_module.Devices().get_last_connected_by_name("d1"), 0)
        devices_module.Devices().set_last_connected_by_name("d1")
        self.assertGreater(devices_module.Devices().get_last_connected_by_name("d1"), 0)

    def test_set_last_connected_unknown_is_noop(self):
        devices_module.Devices().set_last_connected_by_name("ghost")
        self.assertEqual(devices_module.Devices().get_last_connected_by_name("ghost"), 0)

    def test_devices_accessor_uninitialized_raises(self):
        with mock.patch.object(devices_module, "_device_cache", None):
            with self.assertRaises(RuntimeError):
                devices_module.Devices()

    def test_check_profile_not_used(self):
        create_device_cache([_device("d1", "p1"), _device("d2", "p2")])
        self.assertFalse(devices_module.check_profile_not_used("p1"))
        self.assertTrue(devices_module.check_profile_not_used("p3"))

    def test_check_profile_not_used_without_cache(self):
        with mock.patch.object(devices_module, "_device_cache", None):
            self.assertTrue(devices_module.check_profile_not_used("p1"))


class TestCacheProfile(CacheExtendedBase):
    """`DeviceProfileCache` lookup maps, regex matching and duplicate handling."""

    def test_create_with_profiles_builds_lookups(self):
        create_profile_cache([_profile("prof")])
        cache = profiles_module.Profiles()
        resource, ok = cache.device_resource("prof", "temp")
        self.assertTrue(ok)
        self.assertEqual(resource.name, "temp")
        command, ok = cache.device_command("prof", "cmd1")
        self.assertTrue(ok)
        self.assertEqual(command.name, "cmd1")

    def test_check_and_add_existing_is_noop(self):
        profiles_module.Profiles().add(_profile("prof"))
        profiles_module.Profiles().check_and_add(_profile("prof"))
        self.assertEqual(len(profiles_module.Profiles().all()), 1)

    def test_check_and_add_new(self):
        profiles_module.Profiles().check_and_add(_profile("prof"))
        self.assertEqual(len(profiles_module.Profiles().all()), 1)

    def test_add_duplicate_raises(self):
        profiles_module.Profiles().add(_profile("prof"))
        with self.assertRaises(CacheError) as ctx:
            profiles_module.Profiles().add(_profile("prof"))
        self.assertEqual(ctx.exception.kind, CacheErrorKind.DUPLICATE_NAME)

    def test_update_replaces_profile(self):
        profiles_module.Profiles().add(_profile("prof"))
        updated = _profile("prof")
        updated.description = "new"
        profiles_module.Profiles().update(updated)
        stored, _ = profiles_module.Profiles().for_name("prof")
        self.assertEqual(stored.description, "new")

    def test_update_missing_raises(self):
        with self.assertRaises(CacheError) as ctx:
            profiles_module.Profiles().update(_profile("ghost"))
        self.assertEqual(ctx.exception.kind, CacheErrorKind.ENTITY_DOES_NOT_EXIST)

    def test_remove_by_name_ok(self):
        profiles_module.Profiles().add(_profile("prof"))
        profiles_module.Profiles().remove_by_name("prof")
        _, ok = profiles_module.Profiles().for_name("prof")
        self.assertFalse(ok)

    def test_remove_missing_raises(self):
        with self.assertRaises(CacheError) as ctx:
            profiles_module.Profiles().remove_by_name("ghost")
        self.assertEqual(ctx.exception.kind, CacheErrorKind.ENTITY_DOES_NOT_EXIST)

    def test_for_name_missing_returns_empty_profile(self):
        profile, ok = profiles_module.Profiles().for_name("ghost")
        self.assertFalse(ok)
        self.assertEqual(profile.name, "")

    def test_device_resource_missing_profile(self):
        resource, ok = profiles_module.Profiles().device_resource("ghost", "temp")
        self.assertFalse(ok)
        self.assertEqual(resource.name, "")

    def test_device_resource_missing_resource(self):
        profiles_module.Profiles().add(_profile("prof"))
        resource, ok = profiles_module.Profiles().device_resource("prof", "ghost")
        self.assertFalse(ok)
        self.assertEqual(resource.name, "")

    def test_device_resources_by_regex_missing_profile(self):
        matched, ok = profiles_module.Profiles().device_resources_by_regex(
            "ghost", re.compile(".*"))
        self.assertFalse(ok)
        self.assertEqual(matched, [])

    def test_device_resources_by_regex_matches_name_and_search(self):
        profiles_module.Profiles().add(_profile("prof"))
        matched, ok = profiles_module.Profiles().device_resources_by_regex(
            "prof", re.compile("temp"))
        self.assertTrue(ok)
        self.assertEqual([r.name for r in matched], ["temp"])
        matched, ok = profiles_module.Profiles().device_resources_by_regex(
            "prof", re.compile(".*nsor"))
        self.assertTrue(ok)
        self.assertEqual([r.name for r in matched], ["sensor"])
        matched, ok = profiles_module.Profiles().device_resources_by_regex(
            "prof", re.compile("zzz"))
        self.assertTrue(ok)
        self.assertEqual(matched, [])

    def test_device_command_missing_profile(self):
        command, ok = profiles_module.Profiles().device_command("ghost", "cmd1")
        self.assertFalse(ok)
        self.assertEqual(command.name, "")

    def test_device_command_missing_command(self):
        profiles_module.Profiles().add(_profile("prof"))
        command, ok = profiles_module.Profiles().device_command("prof", "ghost")
        self.assertFalse(ok)
        self.assertEqual(command.name, "")

    def test_resource_operation_ok(self):
        profiles_module.Profiles().add(_profile("prof"))
        op = profiles_module.Profiles().resource_operation("prof", "temp")
        self.assertEqual(op.device_resource, "temp")

    def test_resource_operation_no_match_raises(self):
        profiles_module.Profiles().add(_profile("prof"))
        with self.assertRaises(CacheError) as ctx:
            profiles_module.Profiles().resource_operation("prof", "ghost")
        self.assertEqual(ctx.exception.kind, CacheErrorKind.ENTITY_DOES_NOT_EXIST)

    def test_resource_operation_missing_profile_raises(self):
        with self.assertRaises(CacheError) as ctx:
            profiles_module.Profiles().resource_operation("ghost", "temp")
        self.assertEqual(ctx.exception.kind, CacheErrorKind.ENTITY_DOES_NOT_EXIST)

    def test_profiles_accessor_uninitialized_raises(self):
        with mock.patch.object(profiles_module, "_profile_cache", None):
            with self.assertRaises(RuntimeError):
                profiles_module.Profiles()


class TestCacheProvisionWatcher(CacheExtendedBase):
    """`ProvisionWatcherCache` lifecycle and admin state transitions."""

    def test_create_with_watchers(self):
        create_provision_watcher_cache([_watcher("w1"), _watcher("w2")])
        self.assertEqual(len(watchers_module.ProvisionWatchers().all()), 2)

    def test_for_name_missing_returns_empty_watcher(self):
        watcher, ok = watchers_module.ProvisionWatchers().for_name("ghost")
        self.assertFalse(ok)
        self.assertEqual(watcher.name, "")

    def test_for_name_returns_clone(self):
        watchers_module.ProvisionWatchers().add(_watcher("w1"))
        clone, ok = watchers_module.ProvisionWatchers().for_name("w1")
        self.assertTrue(ok)
        clone.labels.append("hacked")
        stored, _ = watchers_module.ProvisionWatchers().for_name("w1")
        self.assertEqual(stored.labels, [])

    def test_add_duplicate_raises(self):
        watchers_module.ProvisionWatchers().add(_watcher("w1"))
        with self.assertRaises(CacheError) as ctx:
            watchers_module.ProvisionWatchers().add(_watcher("w1"))
        self.assertEqual(ctx.exception.kind, CacheErrorKind.DUPLICATE_NAME)

    def test_update_replaces_watcher(self):
        watchers_module.ProvisionWatchers().add(_watcher("w1"))
        updated = _watcher("w1")
        updated.profile_name = "p2"
        watchers_module.ProvisionWatchers().update(updated)
        stored, _ = watchers_module.ProvisionWatchers().for_name("w1")
        self.assertEqual(stored.profile_name, "p2")

    def test_update_missing_raises(self):
        with self.assertRaises(CacheError) as ctx:
            watchers_module.ProvisionWatchers().update(_watcher("ghost"))
        self.assertEqual(ctx.exception.kind, CacheErrorKind.ENTITY_DOES_NOT_EXIST)

    def test_remove_by_name_ok(self):
        watchers_module.ProvisionWatchers().add(_watcher("w1"))
        watchers_module.ProvisionWatchers().remove_by_name("w1")
        _, ok = watchers_module.ProvisionWatchers().for_name("w1")
        self.assertFalse(ok)

    def test_remove_missing_raises(self):
        with self.assertRaises(CacheError) as ctx:
            watchers_module.ProvisionWatchers().remove_by_name("ghost")
        self.assertEqual(ctx.exception.kind, CacheErrorKind.ENTITY_DOES_NOT_EXIST)

    def test_update_admin_state_ok(self):
        watchers_module.ProvisionWatchers().add(_watcher("w1"))
        watchers_module.ProvisionWatchers().update_admin_state("w1", ADMIN_STATE_LOCKED)
        stored, _ = watchers_module.ProvisionWatchers().for_name("w1")
        self.assertEqual(stored.admin_state, ADMIN_STATE_LOCKED)

    def test_update_admin_state_invalid_raises(self):
        watchers_module.ProvisionWatchers().add(_watcher("w1"))
        with self.assertRaises(CacheError) as ctx:
            watchers_module.ProvisionWatchers().update_admin_state("w1", "BOGUS")
        self.assertEqual(ctx.exception.kind, CacheErrorKind.CONTRACT_INVALID)

    def test_update_admin_state_missing_raises(self):
        with self.assertRaises(CacheError) as ctx:
            watchers_module.ProvisionWatchers().update_admin_state("ghost",
                                                                   ADMIN_STATE_LOCKED)
        self.assertEqual(ctx.exception.kind, CacheErrorKind.ENTITY_DOES_NOT_EXIST)

    def test_provision_watchers_accessor_uninitialized_raises(self):
        with mock.patch.object(watchers_module, "_provision_watcher_cache", None):
            with self.assertRaises(RuntimeError):
                watchers_module.ProvisionWatchers()


class TestCacheProvisionCoercion(CacheExtendedBase):
    """The private coercion helpers in `internal/provision.py`."""

    def test_as_bool_matrix(self):
        self.assertTrue(provision._as_bool(True))
        self.assertFalse(provision._as_bool(False))
        for text in ("true", "TRUE", "1", "yes"):
            self.assertTrue(provision._as_bool(text))
        for text in ("false", "no", "0"):
            self.assertFalse(provision._as_bool(text))
        self.assertTrue(provision._as_bool("  yes  "))
        self.assertFalse(provision._as_bool(None))
        self.assertFalse(provision._as_bool(None, default=False))
        self.assertTrue(provision._as_bool(None, default=True))
        self.assertTrue(provision._as_bool(5))
        self.assertFalse(provision._as_bool(0))
        self.assertFalse(provision._as_bool([]))

    def test_as_float_matrix(self):
        self.assertEqual(provision._as_float(None), 0.0)
        self.assertEqual(provision._as_float(None, default=7.5), 7.5)
        self.assertEqual(provision._as_float("1.5"), 1.5)
        self.assertEqual(provision._as_float(3), 3.0)
        self.assertEqual(provision._as_float("abc"), 0.0)
        self.assertEqual(provision._as_float(None), 0.0)
        self.assertEqual(provision._as_float([1, 2]), 0.0)

    def test_as_str_and_str_map(self):
        self.assertEqual(provision._as_str(None), "")
        self.assertEqual(provision._as_str("x"), "x")
        self.assertEqual(provision._as_str(5), "5")
        self.assertEqual(provision._as_str_map({"a": None, "b": 2}), {"a": "", "b": "2"})
        self.assertEqual(provision._as_str_map("nope"), {})

    def test_as_str_list_matrix(self):
        self.assertEqual(provision._as_str_list(None), [])
        self.assertEqual(provision._as_str_list("single"), ["single"])
        self.assertEqual(provision._as_str_list(["a", "b"]), ["a", "b"])
        self.assertEqual(provision._as_str_list(42), ["42"])

    def test_as_str_map_of_lists_matrix(self):
        self.assertEqual(provision._as_str_map_of_lists("nope"), {})
        result = provision._as_str_map_of_lists({"a": ["x"], "b": "y", "c": None})
        self.assertEqual(result, {"a": ["x"], "b": ["y"], "c": []})

    def test_as_raw_map_matrix(self):
        self.assertEqual(provision._as_raw_map("nope"), {})
        original = {"a": 1}
        copy = provision._as_raw_map(original)
        self.assertEqual(copy, {"a": 1})
        self.assertIsNot(copy, original)

    def test_auto_event_empty(self):
        event = provision._auto_event({})
        self.assertEqual(event.source_name, "")
        self.assertFalse(event.on_change)
        self.assertEqual(event.interval, "")

    def test_auto_event_none(self):
        event = provision._auto_event(None)
        self.assertEqual(event.source_name, "")

    def test_auto_event_full(self):
        event = provision._auto_event({
            "sourceName": "temp",
            "onChange": "true",
            "onChangeThreshold": "1.5",
            "interval": "30s",
        })
        self.assertEqual(event.source_name, "temp")
        self.assertTrue(event.on_change)
        self.assertEqual(event.on_change_threshold, 1.5)
        self.assertEqual(event.interval, "30s")

    def test_auto_event_snake_aliases(self):
        event = provision._auto_event({
            "source_name": "temp",
            "frequency": "300ms",
        })
        self.assertEqual(event.source_name, "temp")
        self.assertEqual(event.interval, "300ms")


def _write_files(path, files):
    for name, content in files.items():
        full = os.path.join(path, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content)


_PROFILE_JSON = {
    "name": "temp-profile",
    "description": "profile",
    "manufacturer": "YIQI",
    "model": "M1",
    "labels": ["a", "b"],
    "deviceResources": [{
        "name": "temp",
        "description": "temperature",
        "isHidden": False,
        "tag": "t",
        "properties": {
            "valueType": "Float64",
            "readWrite": "R",
            "units": "C",
            "defaultValue": "0",
            "assertion": "",
            "mediaType": "text/plain",
        },
        "attributes": {"sensor": "x"},
    }],
    "deviceCommands": [{
        "name": "getTemp",
        "isHidden": False,
        "readWrite": "R",
        "resourceOperations": [{
            "deviceResource": "temp",
            "defaultValue": "1",
            "mappings": {"0": "off"},
            "attributes": {"a": 1},
        }],
    }],
    "addTags": {"k": "v"},
    "properties": {"p": "v"},
}

_DEVICE_JSON = {
    "id": "dev-1",
    "name": "dev1",
    "description": "a device",
    "adminState": "UNLOCKED",
    "operatingState": "UP",
    "serviceName": "svc",
    "profileName": "temp-profile",
    "labels": ["x"],
    "location": {"lat": 1},
    "autoEvents": [{
        "sourceName": "temp",
        "onChange": True,
        "onChangeThreshold": 0.5,
        "interval": "30s",
    }],
    "protocols": {"modbus": {"address": "1"}},
    "lastConnected": 100,
    "lastReported": 200,
    "tags": {"k": "v"},
    "properties": {"p": "v"},
}

_WATCHER_JSON = {
    "id": "w-1",
    "name": "watcher1",
    "description": "watcher",
    "serviceName": "svc",
    "labels": ["a"],
    "identifiers": {"mac": "00:11:22"},
    "blockingIdentifiers": {"serial": ["x", "y"]},
    "adminState": "UNLOCKED",
    "profileName": "temp-profile",
    "discoveredDevice": {
        "profileName": "temp-profile",
        "adminState": "UNLOCKED",
        "labels": ["d"],
        "autoEvents": [{
            "sourceName": "temp",
            "onChange": False,
            "onChangeThreshold": "0.25",
            "interval": "10s",
        }],
        "properties": {"k": "v"},
    },
}


class TestCacheProvisionLoad(CacheExtendedBase):
    """`load_profiles` / `load_devices` / `load_provision_watchers` and scanning."""

    def test_scan_files_empty_path(self):
        self.assertEqual(provision._scan_files(""), [])
        self.assertEqual(provision._scan_files(None), [])

    def test_scan_files_filters_extensions_and_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, {
                "a.json": "{}",
                "b.yaml": "{}",
                "c.yml": "{}",
                "d.txt": "skip",
                "e.noext": "skip",
                "sub/f.json": "{}",
            })
            scanned = [os.path.basename(p) for p in provision._scan_files(tmp)]
            self.assertEqual(sorted(scanned), ["a.json", "b.yaml", "c.yml"])

    def test_scan_files_missing_directory(self):
        self.assertEqual(provision._scan_files("/nonexistent/path"), [])

    def test_normalize_device_list_shapes(self):
        array = provision._normalize_device_list([{"name": "a"}, 42, "x"])
        self.assertEqual(array, [{"name": "a"}])
        wrapped = provision._normalize_device_list({"deviceList": [{"name": "a"}, 1]})
        self.assertEqual(wrapped, [{"name": "a"}])
        bare = provision._normalize_device_list({"name": "a"})
        self.assertEqual(bare, [{"name": "a"}])
        self.assertEqual(provision._normalize_device_list({"other": 1}), [])
        self.assertEqual(provision._normalize_device_list("nope"), [])
        self.assertEqual(provision._normalize_device_list(5), [])

    def test_normalize_watcher_list_shapes(self):
        array = provision._normalize_watcher_list([{"name": "a"}, "x"])
        self.assertEqual(array, [{"name": "a"}])
        wrapped = provision._normalize_watcher_list(
            {"provisionWatcherList": [{"name": "a"}]})
        self.assertEqual(wrapped, [{"name": "a"}])
        bare = provision._normalize_watcher_list({"name": "a"})
        self.assertEqual(bare, [{"name": "a"}])
        self.assertEqual(provision._normalize_watcher_list({"other": 1}), [])
        self.assertEqual(provision._normalize_watcher_list("nope"), [])

    def test_load_profiles_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, {"profile.json": json.dumps(_PROFILE_JSON)})
            profiles = provision.load_profiles(tmp, logger=mock.Mock())
            self.assertEqual(len(profiles), 1)
            profile = profiles[0]
            self.assertEqual(profile.name, "temp-profile")
            self.assertEqual(profile.device_resources[0].name, "temp")
            self.assertEqual(profile.device_resources[0].properties.value_type,
                             "Float64")
            self.assertEqual(profile.device_commands[0].name, "getTemp")
            self.assertEqual(
                profile.device_commands[0].resource_operations[0].mappings,
                {"0": "off"})
            self.assertEqual(profile.add_tags, {"k": "v"})

    def test_load_profiles_parse_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, {"bad.json": "{not json"})
            logger = mock.Mock()
            profiles = provision.load_profiles(tmp, logger=logger)
            self.assertEqual(profiles, [])
            logger.warning.assert_called_once()

    def test_load_profiles_non_object_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, {"list.yaml": "- 1\n- 2\n"})
            logger = mock.Mock()
            profiles = provision.load_profiles(tmp, logger=logger)
            self.assertEqual(profiles, [])
            logger.warning.assert_called_once()

    def test_load_profiles_unknown_ext_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, {"profile.txt": json.dumps(_PROFILE_JSON)})
            profiles = provision.load_profiles(tmp, logger=mock.Mock())
            self.assertEqual(profiles, [])

    def test_load_devices_valid_array(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, {"devices.json": json.dumps([_DEVICE_JSON])})
            devices = provision.load_devices(tmp, logger=mock.Mock())
            self.assertEqual(len(devices), 1)
            device = devices[0]
            self.assertEqual(device.name, "dev1")
            self.assertEqual(device.profile_name, "temp-profile")
            self.assertEqual(device.operating_state, "UP")
            self.assertEqual(device.last_connected, 100)
            self.assertEqual(device.protocols, {"modbus": {"address": "1"}})
            self.assertEqual(len(device.auto_events), 1)
            self.assertEqual(device.auto_events[0].source_name, "temp")
            self.assertTrue(device.auto_events[0].on_change)
            self.assertEqual(device.auto_events[0].interval, "30s")

    def test_load_devices_valid_wrapper_and_bare(self):
        wrapped = {"deviceList": [_DEVICE_JSON]}
        bare = dict(_DEVICE_JSON)
        bare["name"] = "dev2"
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, {
                "wrapped.json": json.dumps(wrapped),
                "bare.yaml": "name: dev2\nprofileName: temp-profile\n",
            })
            devices = provision.load_devices(tmp, logger=mock.Mock())
            names = sorted(d.name for d in devices)
            self.assertEqual(names, ["dev1", "dev2"])

    def test_load_devices_parse_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, {"bad.json": "{not json"})
            logger = mock.Mock()
            devices = provision.load_devices(tmp, logger=logger)
            self.assertEqual(devices, [])
            logger.warning.assert_called_once()

    def test_load_devices_non_object_protocol_props(self):
        device_json = dict(_DEVICE_JSON)
        device_json["protocols"] = {"modbus": "not-a-dict"}
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, {"devices.json": json.dumps([device_json])})
            devices = provision.load_devices(tmp, logger=mock.Mock())
            self.assertEqual(devices[0].protocols, {"modbus": {"": "not-a-dict"}})

    def test_load_provision_watchers_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, {"watchers.json": json.dumps([_WATCHER_JSON])})
            watchers = provision.load_provision_watchers(tmp, logger=mock.Mock())
            self.assertEqual(len(watchers), 1)
            watcher = watchers[0]
            self.assertEqual(watcher.name, "watcher1")
            self.assertEqual(watcher.identifiers, {"mac": "00:11:22"})
            self.assertEqual(watcher.blocking_identifiers, {"serial": ["x", "y"]})
            self.assertEqual(watcher.discovered_device.profile_name, "temp-profile")
            self.assertEqual(len(watcher.discovered_device.auto_events), 1)

    def test_load_provision_watchers_wrapper(self):
        wrapped = {"provisionWatcherList": [_WATCHER_JSON]}
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, {"watchers.yaml": json.dumps(wrapped)})
            watchers = provision.load_provision_watchers(tmp, logger=mock.Mock())
            self.assertEqual(len(watchers), 1)

    def test_load_provision_watchers_parse_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, {"bad.json": "{not json"})
            logger = mock.Mock()
            watchers = provision.load_provision_watchers(tmp, logger=logger)
            self.assertEqual(watchers, [])
            logger.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
