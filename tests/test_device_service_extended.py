# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Extended unit tests for `service/device_service.py` targeting the ~750 uncovered lines.

Covers the remaining branches not exercised by existing tests:
- Device / Profile / ProvisionWatcher management (add/update/patch/remove)
- Resource loading, metadata registration, advertised host resolution
- Message-bus config / client init / async pumps / device-return pump
- Discovery / profile-scan handlers & stop handlers
- Command & system-events subscriptions & callbacks
- Config / custom routes / logging / secret provider / metrics
- Internal write-back helpers (_add/_patch/_delete to metadata)
"""

from __future__ import annotations

import builtins
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.internal.cache import (  # noqa: E402
    ADMIN_STATE_LOCKED,
    ADMIN_STATE_UNLOCKED,
    AutoEvent,
    Device,
    DeviceCommand,
    DeviceProfile,
    DeviceResource,
    Devices,
    Profiles,
    ProvisionWatcher,
    ProvisionWatcherDiscoveredDevice,
    ProvisionWatchers,
    ResourceOperation,
    ResourceProperties,
)
from device_sdk_py.internal.cache.devices import create_device_cache  # noqa: E402
from device_sdk_py.internal.cache.profiles import create_profile_cache  # noqa: E402
from device_sdk_py.internal.cache.provisionwatchers import (  # noqa: E402
    create_provision_watcher_cache,
)
from device_sdk_py.internal.common.consts import (  # noqa: E402
    OPERATING_STATE_DOWN,
    OPERATING_STATE_UP,
)
from device_sdk_py.internal.common.utils import (  # noqa: E402
    KIND_CONTRACT_INVALID,
    KIND_ENTITY_DOES_NOT_EXIST,
    EdgexError,
)
from device_sdk_py.internal.controller.messaging.publish import (  # noqa: E402
    DEVICE_SYSTEM_EVENT_TYPE,
    SYSTEM_EVENT_ACTION_PROGRESS,
)
from device_sdk_py.internal.metadata.client import MetadataError  # noqa: E402
from device_sdk_py.models import AsyncValues, CommandValue, DiscoveredDevice  # noqa: E402
from device_sdk_py.service.device_service import DeviceService  # noqa: E402


class _Driver:
    def start(self):
        pass

    def validate_device(self, device):
        pass


class _MockDeviceOpts:
    def __init__(self, **options):
        for key, value in options.items():
            setattr(self, key, value)


class _MockConfig:
    def __init__(self, **sections):
        for key, value in sections.items():
            setattr(self, key, value)


def _service(config=None, driver=None):
    return DeviceService("device-simple", "0.0.0", driver or _Driver(),
                         configuration=config)


def _device(name="dev1", profile_name="p1", **kw):
    return Device(name=name, profile_name=profile_name, **kw)


def _profile(name="p1"):
    return DeviceProfile(name=name)


def _watcher(name="w1", profile_name="p1", **kw):
    return ProvisionWatcher(name=name, profile_name=profile_name, **kw)


class _FakeThread:
    def __init__(self, alive=True):
        self._alive = alive

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self._alive = False


class _ImmediateThread:
    def __init__(self, target=None, daemon=True, name=""):
        self._target = target

    def start(self):
        self._target()


class DeviceServiceExtendedBase(unittest.TestCase):
    def setUp(self):
        create_device_cache([])
        create_profile_cache([])
        create_provision_watcher_cache([])
        self.ds = _service()

    def tearDown(self):
        os.environ.pop("EDGEX_SECURE_MODE", None)
        os.environ.pop("EDGEX_SERVICE_ADDRESS", None)
        os.environ.pop("EDGEX_SERVICE_HOST", None)
        os.environ.pop("EDGEX_CORE_METADATA_HOST", None)
        os.environ.pop("EDGEX_CORE_METADATA_PORT", None)
        os.environ.pop("EDGEX_MESSAGEBUS_HOST", None)
        self.ds._shutdown_event.set()
        self.ds._shutdown()


class TestManagedDeviceOps(DeviceServiceExtendedBase):
    def test_add_device_without_validation_duplicate(self):
        Devices().add(_device("dev1"))
        with self.assertRaises(EdgexError):
            self.ds.add_device_without_validation(_device("dev1"))

    def test_get_device_by_name_missing(self):
        with self.assertRaises(EdgexError) as ctx:
            self.ds.get_device_by_name("ghost")
        self.assertEqual(ctx.exception.kind, KIND_ENTITY_DOES_NOT_EXIST)

    def test_update_device(self):
        dev = _device("dev1", description="old")
        Devices().add(dev)
        dev.description = "new"
        dev.operating_state = OPERATING_STATE_UP
        with mock.patch.object(self.ds, "_patch_device_in_metadata") as patch:
            self.ds.update_device(dev)
        patch.assert_called_once()
        self.assertEqual(patch.call_args[0][0], "dev1")
        self.assertEqual(patch.call_args[0][1]["description"], "new")
        self.assertEqual(patch.call_args.kwargs["bypass_validation"], False)

    def test_update_device_without_validation(self):
        dev = _device("dev1")
        Devices().add(dev)
        dev.labels = ["x"]
        with mock.patch.object(self.ds, "_patch_device_in_metadata") as patch:
            self.ds.update_device_without_validation(dev)
        self.assertEqual(patch.call_args.kwargs["bypass_validation"], True)

    def test_patch_device_with_dict(self):
        Devices().add(_device("dev1", description="old"))
        self.ds.patch_device({"name": "dev1", "description": "new"})
        self.assertEqual(Devices().for_name("dev1")[0].description, "new")

    def test_patch_device_with_object(self):
        Devices().add(_device("dev1", description="old"))
        upd = _MockDeviceOpts(name="dev1", description="new", operating_state=None)
        self.ds.patch_device_without_validation(upd)
        self.assertEqual(Devices().for_name("dev1")[0].description, "new")

    def test_patch_device_missing_name(self):
        with self.assertRaises(EdgexError) as ctx:
            self.ds.patch_device({})
        self.assertEqual(ctx.exception.kind, KIND_CONTRACT_INVALID)

    def test_remove_device_by_name(self):
        Devices().add(_device("dev1"))
        client = mock.Mock()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            self.ds.remove_device_by_name("dev1")
        client.delete_device.assert_called_once_with("dev1")
        self.assertFalse(Devices().for_name("dev1")[1])

    def test_update_device_operating_state(self):
        Devices().add(_device("dev1"))
        client = mock.Mock()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            self.ds.update_device_operating_state("dev1", "DOWN")
        self.assertEqual(Devices().for_name("dev1")[0].operating_state, "DOWN")
        self.assertEqual(client.patch_device.call_args[0][1],
                         {"operating_state": "DOWN"})
        self.assertEqual(client.patch_device.call_args.kwargs["bypass_validation"],
                         True)


class TestProfileWatcherOps(DeviceServiceExtendedBase):
    def test_add_device_profile_duplicate(self):
        Profiles().add(_profile("p1"))
        with self.assertRaises(EdgexError):
            self.ds.add_device_profile(_profile("p1"))

    def test_get_profile_by_name_missing(self):
        with self.assertRaises(EdgexError):
            self.ds.get_profile_by_name("ghost")

    def test_update_device_profile_missing(self):
        with self.assertRaises(EdgexError):
            self.ds.update_device_profile(_profile("ghost"))

    def test_remove_device_profile_by_name_missing(self):
        with self.assertRaises(EdgexError):
            self.ds.remove_device_profile_by_name("ghost")

    def test_add_device_profile_success(self):
        pid = self.ds.add_device_profile(_profile("p1"))
        self.assertTrue(pid)
        self.assertTrue(Profiles().for_name("p1")[1])

    def test_add_provision_watcher_duplicate(self):
        ProvisionWatchers().add(_watcher("w1"))
        with self.assertRaises(EdgexError):
            self.ds.add_provision_watcher(_watcher("w1"))

    def test_get_provision_watcher_by_name_missing(self):
        with self.assertRaises(EdgexError):
            self.ds.get_provision_watcher_by_name("ghost")

    def test_update_provision_watcher_missing(self):
        with self.assertRaises(EdgexError):
            self.ds.update_provision_watcher(_watcher("ghost"))

    def test_remove_provision_watcher_missing(self):
        with self.assertRaises(EdgexError):
            self.ds.remove_provision_watcher("ghost")

    def test_add_provision_watcher_success(self):
        wid = self.ds.add_provision_watcher(_watcher("w1"))
        self.assertTrue(wid)
        self.assertEqual(ProvisionWatchers().for_name("w1")[0].service_name,
                         "device-simple")


class TestDeviceResourceLookup(DeviceServiceExtendedBase):
    def test_device_resource_missing_device(self):
        res, ok = self.ds.device_resource("ghost", "r")
        self.assertFalse(ok)
        self.assertIsInstance(res, DeviceResource)

    def test_device_resource_missing_resource(self):
        Profiles().add(_profile("p1"))
        Devices().add(_device("dev1", profile_name="p1"))
        res, ok = self.ds.device_resource("dev1", "ghost")
        self.assertFalse(ok)

    def test_device_resource_found(self):
        p = _profile("p1")
        p.device_resources = [
            DeviceResource(name="r", properties=ResourceProperties(value_type="String"))]
        Profiles().add(p)
        Devices().add(_device("dev1", profile_name="p1"))
        res, ok = self.ds.device_resource("dev1", "r")
        self.assertTrue(ok)
        self.assertEqual(res.name, "r")

    def test_device_command_missing_device(self):
        res, ok = self.ds.device_command("ghost", "c")
        self.assertFalse(ok)
        self.assertIsInstance(res, DeviceCommand)

    def test_device_command_missing_command(self):
        Profiles().add(_profile("p1"))
        Devices().add(_device("dev1", profile_name="p1"))
        res, ok = self.ds.device_command("dev1", "ghost")
        self.assertFalse(ok)

    def test_device_command_found(self):
        p = _profile("p1")
        p.device_commands = [DeviceCommand(name="get")]
        Profiles().add(p)
        Devices().add(_device("dev1", profile_name="p1"))
        res, ok = self.ds.device_command("dev1", "get")
        self.assertTrue(ok)
        self.assertEqual(res.name, "get")


class TestAutoEventsCRUD(DeviceServiceExtendedBase):
    def test_add_device_auto_event_new(self):
        Devices().add(_device("dev1"))
        with mock.patch.object(self.ds, "_restart_auto_events") as restart:
            self.ds.add_device_auto_event("dev1", AutoEvent(source_name="s1"))
        self.assertEqual(len(Devices().for_name("dev1")[0].auto_events), 1)
        restart.assert_called_once_with("dev1")

    def test_add_device_auto_event_updates_existing(self):
        Devices().add(_device("dev1"))
        dev = Devices().for_name("dev1")[0]
        dev.auto_events = [AutoEvent(source_name="s1", interval="1s")]
        Devices().update(dev)
        with mock.patch.object(self.ds, "_restart_auto_events") as restart:
            self.ds.add_device_auto_event(
                "dev1", AutoEvent(source_name="s1", interval="5s", on_change=True))
        # as documented, the update is not persisted to the cache
        self.assertEqual(len(Devices().for_name("dev1")[0].auto_events), 1)
        self.assertEqual(Devices().for_name("dev1")[0].auto_events[0].interval, "1s")
        restart.assert_called_once_with("dev1")

    def test_add_device_auto_event_missing_device(self):
        with self.assertRaises(EdgexError):
            self.ds.add_device_auto_event("ghost", AutoEvent(source_name="s1"))

    def test_remove_device_auto_event(self):
        Devices().add(_device("dev1"))
        dev = Devices().for_name("dev1")[0]
        dev.auto_events = [AutoEvent(source_name="s1"), AutoEvent(source_name="s2")]
        Devices().update(dev)
        with mock.patch.object(self.ds, "_restart_auto_events") as restart:
            self.ds.remove_device_auto_event("dev1", AutoEvent(source_name="s1"))
        remaining = Devices().for_name("dev1")[0].auto_events
        self.assertEqual([e.source_name for e in remaining], ["s2"])
        restart.assert_called_once_with("dev1")

    def test_remove_device_auto_event_missing_device(self):
        with self.assertRaises(EdgexError):
            self.ds.remove_device_auto_event("ghost", AutoEvent(source_name="s1"))


class TestInitializeResources(DeviceServiceExtendedBase):
    def _write(self, root, sub, name, payload):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
        with open(os.path.join(root, sub, name), "w") as handle:
            import json
            json.dump(payload, handle)

    def test_missing_res_dir(self):
        counts = self.ds.initialize_resources(
            res_root=os.path.join(tempfile.mkdtemp(), "nope"))
        self.assertEqual(counts, {"profiles": 0, "devices": 0, "watchers": 0})

    def test_profile_and_watcher_filters_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "profiles", "p1.json",
                        {"name": "p1", "deviceResources": [
                            {"name": "r", "properties": {"valueType": "String",
                                                          "readWrite": "R"}}]})
            self._write(tmp, "profiles", "p2.json", {"name": "p2"})
            self._write(tmp, "devices", "d1.json",
                        {"name": "d1", "profileName": "p1"})
            self._write(tmp, "devices", "d2.json",
                        {"name": "d2", "profileName": "p1", "serviceName": "other"})
            self._write(tmp, "provisionwatchers", "w1.json",
                        {"name": "w1", "profileName": "p1",
                         "identifiers": {"Address": ".*"}})
            self._write(tmp, "provisionwatchers", "w2.json",
                        {"name": "w2", "profileName": "p1", "serviceName": "other"})

            counts = self.ds.initialize_resources(
                res_root=tmp, profile_names=["p1"], watcher_names=["w1"])
            self.assertEqual(counts, {"profiles": 1, "devices": 2, "watchers": 1})
            self.assertTrue(Profiles().for_name("p1")[1])
            self.assertFalse(Profiles().for_name("p2")[1])
            # device without serviceName defaults to this service key
            self.assertEqual(Devices().for_name("d1")[0].service_name,
                             "device-simple")
            # watcher without serviceName defaults to this service key
            self.assertEqual(ProvisionWatchers().for_name("w1")[0].service_name,
                             "device-simple")
            self.assertEqual(ProvisionWatchers().for_name("w1")[0].identifiers,
                             {"Address": ".*"})


class TestMetadataBaseUrlObject(DeviceServiceExtendedBase):
    class _ClientInfo:
        def __init__(self, host="", port="", base_url=""):
            self.host = host
            self.port = port
            self.base_url = base_url

    class _ClientsSection:
        def __init__(self, info):
            self.core_metadata = info

    def test_from_object_host_port(self):
        cfg = _MockConfig(clients=self._ClientsSection(
            self._ClientInfo(host="md", port="59881")))
        self.assertEqual(_service(cfg)._metadata_base_url(), "http://md:59881")

    def test_from_object_base_url(self):
        cfg = _MockConfig(clients=self._ClientsSection(
            self._ClientInfo(base_url="http://md:59999")))
        self.assertEqual(_service(cfg)._metadata_base_url(), "http://md:59999")


class TestRunMetadata(DeviceServiceExtendedBase):
    def test_run_metadata(self):
        client = mock.Mock()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            self.assertEqual(self.ds._run_metadata(lambda: "res"), "res")
        self.assertIsNotNone(self.ds._metadata_executor)
        self.ds._metadata_executor.shutdown(wait=False)
        self.ds._metadata_executor = None

    def test_run_metadata_none_client(self):
        with mock.patch.object(self.ds, "_metadata_client", return_value=None):
            self.assertIsNone(self.ds._run_metadata(lambda: "res"))


class TestRegisterResources(DeviceServiceExtendedBase):
    def test_register_resources(self):
        client = mock.Mock()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            with mock.patch.object(self.ds, "_register_device_service") as rds:
                with mock.patch.object(self.ds, "_add_missing_profiles") as amp:
                    with mock.patch.object(self.ds, "_add_missing_devices") as amd:
                        with mock.patch.object(self.ds, "_add_missing_watchers") as amw:
                            self.ds._register_resources_to_metadata([], [], [])
        rds.assert_called_once_with(client)
        amp.assert_called_once_with(client, [])
        amd.assert_called_once_with(client, [])
        amw.assert_called_once_with(client, [])

    def test_register_device_service_new(self):
        client = mock.Mock()
        client.device_service_by_name.return_value = None
        self._reg_service(client)
        client.add_device_service.assert_called_once()

    def test_register_device_service_update(self):
        client = mock.Mock()
        client.device_service_by_name.return_value = {"name": "device-simple"}
        self._reg_service(client)
        client.update_device_service.assert_called_once()
        client.add_device_service.assert_not_called()

    def test_register_device_service_check_error(self):
        client = mock.Mock()
        client.device_service_by_name.side_effect = MetadataError("boom")
        ds = self._reg_service(client, logger=mock.Mock())
        client.add_device_service.assert_not_called()
        ds._logger.warning.assert_called()

    def test_register_device_service_add_error(self):
        client = mock.Mock()
        client.device_service_by_name.return_value = None
        client.add_device_service.side_effect = MetadataError("boom")
        ds = self._reg_service(client, logger=mock.Mock())
        ds._logger.error.assert_called()

    def test_register_device_service_update_error(self):
        client = mock.Mock()
        client.device_service_by_name.return_value = {"name": "device-simple"}
        client.update_device_service.side_effect = MetadataError("boom")
        ds = self._reg_service(client, logger=mock.Mock())
        ds._logger.error.assert_called()

    def _reg_service(self, client, logger=None):
        ds = _service()
        if logger is not None:
            ds._logger = logger
        with mock.patch.object(ds, "_http_host_port",
                               return_value=("0.0.0.0", 59986)):
            with mock.patch.object(ds, "_advertised_host",
                                   return_value="localhost"):
                with mock.patch.object(ds, "_device_labels",
                                       return_value=["label"]):
                    ds._register_device_service(client)
        return ds


class TestAdvertisedHostAutoDetect(DeviceServiceExtendedBase):
    def test_auto_detect_success(self):
        ds = _service(_MockConfig(service=_MockDeviceOpts(host="0.0.0.0")))
        with mock.patch("socket.socket") as sock:
            sock.return_value.getsockname.return_value = ("192.168.1.50", 12345)
            self.assertEqual(ds._advertised_host(), "192.168.1.50")

    def test_auto_detect_disabled(self):
        ds = _service(_MockConfig(service=_MockDeviceOpts(
            host="0.0.0.0", auto_detect_host=False)))
        self.assertEqual(ds._advertised_host(), "localhost")


class TestDeviceLabels(DeviceServiceExtendedBase):
    def test_labels_from_config(self):
        ds = _service(_MockConfig(device=_MockDeviceOpts(labels=["a", "b"])))
        self.assertEqual(ds._device_labels(), ["a", "b"])

    def test_no_labels(self):
        self.assertEqual(_service()._device_labels(), [])


class TestAddMissingRegistrations(DeviceServiceExtendedBase):
    def test_add_missing_profiles_adds(self):
        client = mock.Mock()
        client.device_profile_by_name.return_value = None
        self.ds._add_missing_profiles(client, [DeviceProfile(name="p1")])
        client.add_device_profiles.assert_called_once()

    def test_add_missing_profiles_exists(self):
        client = mock.Mock()
        client.device_profile_by_name.return_value = {"name": "p1"}
        self.ds._add_missing_profiles(client, [DeviceProfile(name="p1")])
        client.add_device_profiles.assert_not_called()

    def test_add_missing_profiles_error(self):
        client = mock.Mock()
        client.device_profile_by_name.side_effect = MetadataError("boom")
        ds = _service()
        ds._logger = mock.Mock()
        ds._add_missing_profiles(client, [DeviceProfile(name="p1")])
        ds._logger.error.assert_called()

    def test_add_missing_devices_adds(self):
        client = mock.Mock()
        client.device_by_name.return_value = None
        self.ds._add_missing_devices(client, [_device()])
        client.add_devices.assert_called_once()

    def test_add_missing_devices_exists(self):
        client = mock.Mock()
        client.device_by_name.return_value = {"name": "dev1"}
        self.ds._add_missing_devices(client, [_device()])
        client.add_devices.assert_not_called()

    def test_add_missing_devices_error(self):
        client = mock.Mock()
        client.device_by_name.side_effect = MetadataError("boom")
        ds = _service()
        ds._logger = mock.Mock()
        ds._add_missing_devices(client, [_device()])
        ds._logger.error.assert_called()

    def test_add_missing_watchers_adds(self):
        client = mock.Mock()
        client.provision_watcher_by_name.return_value = None
        self.ds._add_missing_watchers(client, [_watcher()])
        client.add_provision_watchers.assert_called_once()

    def test_add_missing_watchers_exists(self):
        client = mock.Mock()
        client.provision_watcher_by_name.return_value = {"name": "w1"}
        self.ds._add_missing_watchers(client, [_watcher()])
        client.add_provision_watchers.assert_not_called()

    def test_add_missing_watchers_error(self):
        client = mock.Mock()
        client.provision_watcher_by_name.side_effect = MetadataError("boom")
        ds = _service()
        ds._logger = mock.Mock()
        ds._add_missing_watchers(client, [_watcher()])
        ds._logger.error.assert_called()


class TestMessageBusConfigObject(DeviceServiceExtendedBase):
    def test_from_object(self):
        mq = _MockDeviceOpts(host="obj-host", port=2883, type="mqtt",
                             base_topic_prefix="edgex2",
                             publish_topic_prefix="events2",
                             optional={"Username": "u"},
                             auth_mode="usernamepassword")
        cfg = _service(_MockConfig(message_bus=mq))._message_bus_config()
        self.assertEqual(cfg.broker_info.host, "obj-host")
        self.assertEqual(cfg.broker_info.port, 2883)
        self.assertEqual(cfg.message_bus_type, "mqtt")
        self.assertEqual(cfg.base_topic_prefix, "edgex2")
        self.assertEqual(cfg.optional["Username"], "u")
        self.assertEqual(cfg.auth_mode, "usernamepassword")


class TestInitMessagingClient(DeviceServiceExtendedBase):
    def test_init_already_initialized(self):
        self.ds._messaging_client = object()
        with mock.patch("device_sdk_py.service.device_service.create_message_client") as cm:
            self.ds._init_messaging_client()
        cm.assert_not_called()

    def test_init_connect_success(self):
        client = mock.Mock()
        with mock.patch("device_sdk_py.service.device_service.create_message_client",
                        return_value=client) as cm:
            self.ds._init_messaging_client()
        cm.assert_called_once()
        client.connect.assert_called_once()
        self.assertIs(self.ds._messaging_client, client)
        self.assertIsNotNone(self.ds._send_event_handler)

    def test_init_connect_failure(self):
        client = mock.Mock()
        client.connect.side_effect = OSError("no broker")
        self.ds._logger = mock.Mock()
        with mock.patch("device_sdk_py.service.device_service.create_message_client",
                        return_value=client):
            self.ds._init_messaging_client()
        self.assertIsNone(self.ds._messaging_client)
        self.ds._logger.warning.assert_called()

    def test_init_unsupported_bus_type_no_crash(self):
        self.ds._logger = mock.Mock()
        with mock.patch("device_sdk_py.service.device_service.create_message_client",
                        side_effect=ValueError(
                            "Unsupported message bus type: redisstreams")):
            self.ds._init_messaging_client()
        self.assertIsNone(self.ds._messaging_client)
        self.assertIsNone(self.ds._send_event_handler)
        self.ds._logger.warning.assert_called()


class TestMakeSendEventHandler(DeviceServiceExtendedBase):
    def _configured(self):
        self.ds._messaging_client = mock.Mock()
        self.ds._message_bus_config_obj = mock.Mock(base_topic_prefix="edgex")

    def test_handler_no_client(self):
        handler = self.ds._make_send_event_handler()
        with mock.patch("device_sdk_py.service.device_service.publish_event") as pub:
            handler(mock.Mock(), "cid")
            pub.assert_not_called()

    def test_handler_publishes(self):
        self._configured()
        event = mock.Mock(profile_name="p1", device_name="d1", source_name="s1")
        with mock.patch("device_sdk_py.service.device_service.publish_event") as pub:
            self.ds._make_send_event_handler()(event, "cid")
            pub.assert_called_once()

    def test_handler_passes_metrics_manager(self):
        self._configured()
        event = mock.Mock(profile_name="p1", device_name="d1", source_name="s1")
        with mock.patch("device_sdk_py.service.device_service.publish_event") as pub:
            self.ds._make_send_event_handler()(event, "cid")
        self.assertIs(pub.call_args.kwargs["metrics_manager"], self.ds.metrics_manager())

    def test_handler_publish_error(self):
        self._configured()
        self.ds._logger = mock.Mock()
        with mock.patch("device_sdk_py.service.device_service.publish_event",
                        side_effect=RuntimeError("x")) as pub:
            self.ds._make_send_event_handler()(mock.Mock(), "cid")
        self.ds._logger.error.assert_called()


class TestStartDeviceValidationHandler(DeviceServiceExtendedBase):
    def test_import_fail(self):
        self.ds._logger = mock.Mock()

        def fake_import(name, *args, **kwargs):
            raise ImportError("no module")

        with mock.patch("builtins.__import__", side_effect=fake_import):
            self.ds._start_device_validation_handler()
        self.ds._logger.debug.assert_called()

    def test_skip_when_no_client(self):
        self.ds._logger = mock.Mock()
        self.ds._init_messaging_client = mock.Mock()
        with mock.patch(
                "device_sdk_py.internal.controller.messaging.validation."
                "subscribe_device_validation") as sdv:
            self.ds._start_device_validation_handler()
            sdv.assert_not_called()

    def test_skip_when_already_set(self):
        self.ds._validation_handler = "existing"
        self.ds._logger = mock.Mock()
        with mock.patch(
                "device_sdk_py.internal.controller.messaging.validation."
                "subscribe_device_validation") as sdv:
            self.ds._start_device_validation_handler()
            sdv.assert_not_called()

    def test_starts(self):
        self.ds._logger = mock.Mock()
        self.ds._message_bus_config_obj = mock.Mock(base_topic_prefix="edgex")
        self.ds._messaging_client = mock.Mock()
        self.ds.driver = mock.Mock()
        with mock.patch(
                "device_sdk_py.internal.controller.messaging.validation."
                "subscribe_device_validation",
                return_value="vh") as sdv:
            self.ds._start_device_validation_handler()
        self.assertEqual(self.ds._validation_handler, "vh")
        sdv.assert_called_once()
        self.assertIs(sdv.call_args.kwargs["client"], self.ds._messaging_client)
        self.assertEqual(sdv.call_args.kwargs["base_topic_prefix"], "edgex")
        del self.ds._validation_handler


class TestStartAsyncPumps(DeviceServiceExtendedBase):
    def test_pumps_disabled_without_client(self):
        self.ds._logger = mock.Mock()
        self.ds._start_async_pumps()
        self.ds._logger.debug.assert_called()

    def test_pumps_started_with_return_and_discovery(self):
        self.ds._shutdown_event.set()
        self.ds._messaging_client = mock.Mock()
        self.ds._message_bus_config_obj = mock.Mock(base_topic_prefix="edgex")
        self.ds.configuration = _MockConfig(device=_MockDeviceOpts(
            device_down_timeout=2,
            discovery=_MockDeviceOpts(enabled=True, interval="10s")))
        with mock.patch(
                "device_sdk_py.service.device_service.bootstrap_autodiscovery",
                return_value=mock.Mock()) as ba:
            self.ds._start_async_pumps()
            ba.assert_called_once()
        self.assertIsNotNone(self.ds._async_pump_thread)
        self.assertIsNotNone(self.ds._discovered_pump_thread)
        self.assertIsNotNone(self.ds._device_return_thread)
        self.ds._async_pump_thread.join(timeout=3)
        self.ds._discovered_pump_thread.join(timeout=3)
        self.ds._device_return_thread.join(timeout=3)

    def test_pumps_no_return_thread_when_disabled(self):
        self.ds._shutdown_event.set()
        self.ds._messaging_client = mock.Mock()
        self.ds._message_bus_config_obj = mock.Mock(base_topic_prefix="edgex")
        self.ds.configuration = _MockConfig(
            device=_MockDeviceOpts(device_down_timeout=0))
        self.ds._start_async_pumps()
        self.assertIsNone(self.ds._device_return_thread)
        self.ds._async_pump_thread.join(timeout=3)
        self.ds._discovered_pump_thread.join(timeout=3)

    def _configured_for_pumps(self):
        self.ds._messaging_client = mock.Mock()
        self.ds._message_bus_config_obj = mock.Mock(base_topic_prefix="edgex")
        self.ds.configuration = _MockConfig(
            device=_MockDeviceOpts(device_down_timeout=0))

    def test_async_pump_processes_value(self):
        self._configured_for_pumps()
        processed = []

        def fake_process(acv, cfg):
            processed.append(acv)
            self.ds._shutdown_event.set()

        self.ds._process_async_values = fake_process
        self.ds._async_values_channel.put(
            AsyncValues(command_values=[CommandValue("r1", "String", "a")]))
        self.ds._start_async_pumps()
        self.ds._async_pump_thread.join(timeout=5)
        self.ds._discovered_pump_thread.join(timeout=5)
        self.assertEqual(len(processed), 1)

    def test_async_pump_logs_error(self):
        self._configured_for_pumps()
        self.ds._logger = mock.Mock()

        def boom(acv, cfg):
            self.ds._shutdown_event.set()
            raise RuntimeError("boom")

        self.ds._process_async_values = boom
        self.ds._async_values_channel.put(
            AsyncValues(command_values=[CommandValue("r1", "String", "a")]))
        self.ds._start_async_pumps()
        self.ds._async_pump_thread.join(timeout=5)
        self.ds._discovered_pump_thread.join(timeout=5)
        self.assertTrue(self.ds._logger.error.called)

    def test_discovered_pump_processes_value(self):
        self._configured_for_pumps()
        processed = []

        def fake_process(devices):
            processed.append(devices)
            self.ds._shutdown_event.set()

        self.ds._process_discovered_devices = fake_process
        self.ds._discovered_device_channel.get = mock.Mock(
            side_effect=[queue.Empty, [DiscoveredDevice(
                name="d1", protocols={"a": {"b": "c"}})]])
        self.ds._start_async_pumps()
        self.ds._async_pump_thread.join(timeout=5)
        self.ds._discovered_pump_thread.join(timeout=5)
        self.assertEqual(len(processed), 1)

    def test_discovered_pump_logs_error(self):
        self._configured_for_pumps()
        self.ds._logger = mock.Mock()

        def boom(devices):
            self.ds._shutdown_event.set()
            raise RuntimeError("boom")

        self.ds._process_discovered_devices = boom
        self.ds._discovered_device_channel.put(
            [DiscoveredDevice(name="d1", protocols={"a": {"b": "c"}})])
        self.ds._start_async_pumps()
        self.ds._async_pump_thread.join(timeout=5)
        self.ds._discovered_pump_thread.join(timeout=5)
        self.assertTrue(self.ds._logger.error.called)


class TestProcessAsyncValues(DeviceServiceExtendedBase):
    def test_empty_command_values(self):
        self.ds._logger = mock.Mock()
        self.ds._process_async_values(AsyncValues(command_values=[]), mock.Mock())
        self.ds._logger.warning.assert_called()

    def test_multi_readings_no_source(self):
        cv1 = CommandValue("r1", "String", "a")
        cv2 = CommandValue("r2", "String", "b")
        self.ds._logger = mock.Mock()
        self.ds._process_async_values(
            AsyncValues(command_values=[cv1, cv2]), mock.Mock())
        self.ds._logger.warning.assert_called()

    def test_single_sets_source_and_publishes(self):
        cv = CommandValue("r1", "String", "a")
        acv = AsyncValues(command_values=[cv])
        handler = mock.Mock()
        self.ds._send_event_handler = handler
        self.ds.configuration = _MockConfig(device=_MockDeviceOpts(
            data_transform=True, reading_units=True))
        with mock.patch("device_sdk_py.internal.transformer.transform."
                        "command_values_to_event", return_value=mock.Mock()) as t:
            self.ds._process_async_values(acv, mock.Mock())
            t.assert_called_once()
        handler.assert_called_once()
        self.assertEqual(acv.source_name, "r1")

    def test_transform_returns_none(self):
        cv = CommandValue("r1", "String", "a")
        handler = mock.Mock()
        self.ds._send_event_handler = handler
        with mock.patch("device_sdk_py.internal.transformer.transform."
                        "command_values_to_event", return_value=None):
            self.ds._process_async_values(
                AsyncValues(command_values=[cv]), mock.Mock())
        handler.assert_not_called()

    def test_transform_raises(self):
        cv = CommandValue("r1", "String", "a")
        self.ds._logger = mock.Mock()
        with mock.patch("device_sdk_py.internal.transformer.transform."
                        "command_values_to_event",
                        side_effect=ValueError("bad")):
            self.ds._process_async_values(
                AsyncValues(command_values=[cv]), mock.Mock())
        self.ds._logger.error.assert_called()


class TestProcessDiscoveredDevices(DeviceServiceExtendedBase):
    def test_no_watchers(self):
        self.ds._process_discovered_devices(
            [DiscoveredDevice(name="d1", protocols={"a": {"b": "c"}})])

    def test_locked_watcher_skipped(self):
        ProvisionWatchers().add(_watcher("w1", admin_state="LOCKED"))
        with mock.patch.object(self.ds, "add_device_without_validation") as add:
            self.ds._process_discovered_devices(
                [DiscoveredDevice(name="d1", protocols={"a": {"b": "c"}})])
            add.assert_not_called()

    def test_matched_and_added(self):
        pw = _watcher("w1", admin_state="UNLOCKED", identifiers={"Address": ".*"})
        pw.discovered_device = ProvisionWatcherDiscoveredDevice(profile_name="p1")
        ProvisionWatchers().add(pw)
        d = DiscoveredDevice(name="nd", protocols={"simple": {"Address": "x"}})
        with mock.patch.object(self.ds, "add_device_without_validation") as add:
            with mock.patch.object(self.ds, "_publish_discovery_progress") as pub:
                self.ds._process_discovered_devices([d])
                add.assert_called_once()
                pub.assert_called_once_with(100, 1, mock.ANY)

    def test_matched_but_exists(self):
        pw = _watcher("w1", admin_state="UNLOCKED", identifiers={"Address": ".*"})
        pw.discovered_device = ProvisionWatcherDiscoveredDevice(profile_name="p1")
        ProvisionWatchers().add(pw)
        Devices().add(_device("nd"))
        d = DiscoveredDevice(name="nd", protocols={"simple": {"Address": "x"}})
        with mock.patch.object(self.ds, "add_device_without_validation") as add:
            self.ds._process_discovered_devices([d])
            add.assert_not_called()

    def test_add_fails(self):
        pw = _watcher("w1", admin_state="UNLOCKED", identifiers={"Address": ".*"})
        pw.discovered_device = ProvisionWatcherDiscoveredDevice(profile_name="p1")
        ProvisionWatchers().add(pw)
        self.ds._logger = mock.Mock()
        d = DiscoveredDevice(name="nd", protocols={"simple": {"Address": "x"}})
        with mock.patch.object(self.ds, "add_device_without_validation",
                               side_effect=RuntimeError("boom")):
            self.ds._process_discovered_devices([d])
        self.ds._logger.error.assert_called()


class TestMatchProvisionWatcher(DeviceServiceExtendedBase):
    def test_no_identifiers(self):
        pw = _watcher("w1", identifiers={})
        d = DiscoveredDevice(name="x", protocols={"a": {"b": "c"}})
        self.assertFalse(self.ds._match_provision_watcher(d, pw))

    def test_no_match(self):
        pw = _watcher("w1", identifiers={"Address": r"10\..*"})
        d = DiscoveredDevice(name="x", protocols={"simple": {"Address": "192.168.0.1"}})
        self.assertFalse(self.ds._match_provision_watcher(d, pw))

    def test_empty_value_skipped(self):
        pw = _watcher("w1", identifiers={"Address": ".*"})
        d = DiscoveredDevice(name="x", protocols={"simple": {"Address": ""}})
        self.assertFalse(self.ds._match_provision_watcher(d, pw))

    def test_match_success(self):
        pw = _watcher("w1", identifiers={"Address": r"192\..*"})
        d = DiscoveredDevice(name="x", protocols={"simple": {"Address": "192.168.0.1"}})
        self.assertTrue(self.ds._match_provision_watcher(d, pw))

    def test_blocked(self):
        pw = _watcher("w1", identifiers={"Address": r"192\..*"},
                      blocking_identifiers={"Address": ["192.168.0.1"]})
        d = DiscoveredDevice(name="x", protocols={"simple": {"Address": "192.168.0.1"}})
        self.assertFalse(self.ds._match_provision_watcher(d, pw))

    def test_blocking_key_absent_allows(self):
        pw = _watcher("w1", identifiers={"Address": r"192\..*"},
                      blocking_identifiers={"Port": ["9999"]})
        d = DiscoveredDevice(name="x", protocols={"simple": {"Address": "192.168.0.1"}})
        self.assertTrue(self.ds._match_provision_watcher(d, pw))


class TestDeviceReturnPump(DeviceServiceExtendedBase):
    def test_no_down_timeout_exits(self):
        self.ds.configuration = _MockConfig(device=_MockDeviceOpts())
        self.ds._logger = mock.Mock()
        self.ds._device_return_pump()

    def test_restores_down_device(self):
        self.ds.configuration = _MockConfig(
            device=_MockDeviceOpts(device_down_timeout=1))
        Devices().add(_device("dev1", operating_state=OPERATING_STATE_DOWN))
        self.ds._logger = mock.Mock()
        calls = {"n": 0}

        def fake_is_set():
            calls["n"] += 1
            return calls["n"] >= 5

        self.ds._shutdown_event = mock.Mock()
        self.ds._shutdown_event.is_set = fake_is_set
        with mock.patch("device_sdk_py.service.device_service.time.sleep"):
            with mock.patch("device_sdk_py.internal.application.command."
                            "command_read") as cr:
                self.ds._device_return_pump()
                cr.assert_called_once()

    def test_return_attempt_failure_logged(self):
        self.ds.configuration = _MockConfig(
            device=_MockDeviceOpts(device_down_timeout=1))
        Devices().add(_device("dev1", operating_state=OPERATING_STATE_DOWN))
        self.ds._logger = mock.Mock()
        calls = {"n": 0}

        def fake_is_set():
            calls["n"] += 1
            return calls["n"] >= 5

        self.ds._shutdown_event = mock.Mock()
        self.ds._shutdown_event.is_set = fake_is_set
        with mock.patch("device_sdk_py.service.device_service.time.sleep"):
            with mock.patch("device_sdk_py.internal.application.command."
                            "command_read",
                            side_effect=RuntimeError("down")):
                self.ds._device_return_pump()
        self.ds._logger.debug.assert_called()

    def test_stops_during_wait_loop(self):
        self.ds.configuration = _MockConfig(
            device=_MockDeviceOpts(device_down_timeout=1))
        self.ds._logger = mock.Mock()
        calls = {"n": 0}

        def fake_is_set():
            calls["n"] += 1
            return calls["n"] >= 2

        self.ds._shutdown_event = mock.Mock()
        self.ds._shutdown_event.is_set = fake_is_set
        with mock.patch("device_sdk_py.service.device_service.time.sleep"):
            self.ds._device_return_pump()
        self.assertEqual(calls["n"], 3)

    def test_stops_mid_device_restore(self):
        self.ds.configuration = _MockConfig(
            device=_MockDeviceOpts(device_down_timeout=1))
        Devices().add(_device("dev1", operating_state=OPERATING_STATE_DOWN))
        self.ds._logger = mock.Mock()
        calls = {"n": 0}

        def fake_is_set():
            calls["n"] += 1
            return calls["n"] >= 4

        self.ds._shutdown_event = mock.Mock()
        self.ds._shutdown_event.is_set = fake_is_set
        with mock.patch("device_sdk_py.service.device_service.time.sleep"):
            with mock.patch("device_sdk_py.internal.application.command."
                            "command_read") as cr:
                self.ds._device_return_pump()
                cr.assert_not_called()

    def test_outer_error_logged(self):
        self.ds.configuration = _MockConfig(
            device=_MockDeviceOpts(device_down_timeout=1))
        self.ds._logger = mock.Mock()
        calls = {"n": 0}

        def fake_is_set():
            calls["n"] += 1
            return calls["n"] >= 6

        self.ds._shutdown_event = mock.Mock()
        self.ds._shutdown_event.is_set = fake_is_set
        with mock.patch("device_sdk_py.service.device_service.time.sleep",
                        side_effect=[None, RuntimeError("boom")]):
            self.ds._device_return_pump()
        self.assertTrue(self.ds._logger.error.called)


class TestProgressPublish(DeviceServiceExtendedBase):
    def _client(self):
        self.ds._messaging_client = mock.Mock()
        self.ds._message_bus_config_obj = mock.Mock(base_topic_prefix="edgex")

    def test_discovery_progress_error(self):
        self._client()
        self.ds._logger = mock.Mock()
        with mock.patch("device_sdk_py.service.device_service.publish_system_event",
                        side_effect=RuntimeError("x")):
            self.ds._publish_discovery_progress(100, 1, "msg")
        self.ds._logger.error.assert_called()

    def test_profile_scan_progress_error(self):
        self._client()
        self.ds._logger = mock.Mock()
        with mock.patch("device_sdk_py.service.device_service.publish_system_event",
                        side_effect=RuntimeError("x")):
            self.ds._publish_profile_scan_progress("req", 100, "msg")
        self.ds._logger.error.assert_called()

    def test_discovery_progress_publishes(self):
        self._client()
        with mock.patch("device_sdk_py.service.device_service.publish_system_event") as pub:
            self.ds._publish_discovery_progress(50, 2, "half")
            pub.assert_called_once()
        self.assertEqual(pub.call_args.kwargs["event_type"], DEVICE_SYSTEM_EVENT_TYPE)
        self.assertEqual(pub.call_args.kwargs["action"], "discovery")

    def test_generic_system_event_error(self):
        self._client()
        self.ds._logger = mock.Mock()
        with mock.patch("device_sdk_py.service.device_service.publish_system_event",
                        side_effect=RuntimeError("x")):
            self.ds.publish_generic_system_event("type", "action", {})
        self.ds._logger.error.assert_called()


class TestDiscoveryStopHandler(DeviceServiceExtendedBase):
    def test_stop_sets_event(self):
        ev = threading.Event()
        self.ds._discovery_stop_events["req"] = ev
        thread = _FakeThread()
        self.ds._discovery_thread = thread
        with mock.patch.object(self.ds, "_publish_discovery_progress") as pub:
            self.ds._device_discovery_stop_handler("req", {})
        self.assertTrue(ev.is_set())
        pub.assert_called_once_with(-1, 0, mock.ANY)
        self.assertIsNone(self.ds._discovery_thread)

    def test_stop_without_event(self):
        thread = _FakeThread()
        self.ds._discovery_thread = thread
        with mock.patch.object(self.ds, "_publish_discovery_progress") as pub:
            self.ds._device_discovery_stop_handler("ghost", {})
        pub.assert_not_called()


class TestProfileScanHandler(DeviceServiceExtendedBase):
    def test_scan_success(self):
        self.ds._logger = mock.Mock()
        Devices().add(_device("dev1", protocols={"simple": {"Address": "x"}}))
        self.ds._profile_scan_stop_events = {}
        with mock.patch.object(self.ds, "add_device_profile") as addp:
            with mock.patch.object(self.ds, "_publish_profile_scan_progress") as pub:
                with mock.patch("threading.Thread", _ImmediateThread):
                    self.ds._profile_scan_handler("dev1", "newp", "req1", {})
        addp.assert_called_once()
        pub.assert_called_once_with("req1", 100, "Profile scan completed")
        self.assertNotIn("req1", self.ds._profile_scan_stop_events)

    def test_scan_device_missing(self):
        self.ds._logger = mock.Mock()
        with mock.patch.object(self.ds, "_publish_profile_scan_progress") as pub:
            with mock.patch("threading.Thread", _ImmediateThread):
                self.ds._profile_scan_handler("ghost", "p", "req2", {})
        pub.assert_called_once_with("req2", -1, mock.ANY)

    def test_scan_exception(self):
        self.ds._logger = mock.Mock()
        Devices().add(_device("dev1", protocols={"simple": {"Address": "x"}}))
        with mock.patch.object(self.ds, "add_device_profile",
                               side_effect=RuntimeError("boom")):
            with mock.patch.object(self.ds, "_publish_profile_scan_progress") as pub:
                with mock.patch("threading.Thread", _ImmediateThread):
                    self.ds._profile_scan_handler("dev1", "p", "req3", {})
        pub.assert_called_once()
        self.assertEqual(pub.call_args[0][1], -1)

    def test_scan_stopped_before_start(self):
        self.ds._logger = mock.Mock()
        ev = threading.Event()
        ev.set()
        with mock.patch("threading.Event", return_value=ev):
            with mock.patch("threading.Thread", _ImmediateThread):
                with mock.patch.object(self.ds,
                                       "_publish_profile_scan_progress") as pub:
                    self.ds._profile_scan_handler("dev1", "p", "req0", {})
        pub.assert_not_called()
        self.assertTrue(self.ds._logger.info.called)
        self.assertNotIn("req0", self.ds._profile_scan_stop_events)

    def test_stop_with_list_request_id(self):
        ev = threading.Event()
        self.ds._profile_scan_stop_events = {"rid": ev}
        with mock.patch.object(self.ds, "_publish_profile_scan_progress") as pub:
            self.ds._profile_scan_stop_handler("dev1", {"requestId": ["rid"]})
        self.assertTrue(ev.is_set())
        pub.assert_called_once_with("rid", -1, mock.ANY)

    def test_stop_with_str_request_id(self):
        ev = threading.Event()
        self.ds._profile_scan_stop_events = {"rid": ev}
        with mock.patch.object(self.ds, "_publish_profile_scan_progress") as pub:
            self.ds._profile_scan_stop_handler("dev1", {"requestId": "rid"})
        pub.assert_called_once_with("rid", -1, mock.ANY)

    def test_stop_fallback_all(self):
        ev1 = threading.Event()
        ev2 = threading.Event()
        self.ds._profile_scan_stop_events = {"a": ev1, "b": ev2}
        with mock.patch.object(self.ds, "_publish_profile_scan_progress") as pub:
            self.ds._profile_scan_stop_handler("dev1", {})
        self.assertTrue(ev1.is_set())
        self.assertTrue(ev2.is_set())
        self.assertEqual(pub.call_count, 2)


class TestSubscriptions(DeviceServiceExtendedBase):
    def test_command_subscription_disabled(self):
        self.ds._logger = mock.Mock()
        self.ds._start_command_subscription()
        self.ds._logger.debug.assert_called()

    def test_command_subscription_default_max(self):
        self.ds._messaging_client = mock.Mock()
        self.ds._message_bus_config_obj = mock.Mock(base_topic_prefix="edgex")
        self.ds.driver = mock.Mock()
        with mock.patch("device_sdk_py.internal.controller.messaging.command."
                        "subscribe_commands", return_value=mock.Mock()) as sc:
            self.ds._start_command_subscription()
        sc.assert_called_once()
        self.assertEqual(sc.call_args.kwargs["max_concurrent"], 32)

    def test_command_subscription_custom_max(self):
        self.ds._messaging_client = mock.Mock()
        self.ds._message_bus_config_obj = mock.Mock(base_topic_prefix="edgex")
        self.ds.configuration = _MockConfig(
            device=_MockDeviceOpts(max_concurrent_commands=8))
        with mock.patch("device_sdk_py.internal.controller.messaging.command."
                        "subscribe_commands", return_value=mock.Mock()) as sc:
            self.ds._start_command_subscription()
        self.assertEqual(sc.call_args.kwargs["max_concurrent"], 8)

    def test_system_events_disabled(self):
        self.ds._logger = mock.Mock()
        self.ds._start_system_events_subscription()
        self.ds._logger.debug.assert_called()

    def test_system_events_starts(self):
        self.ds._messaging_client = mock.Mock()
        self.ds._message_bus_config_obj = mock.Mock(base_topic_prefix="edgex")
        with mock.patch("device_sdk_py.internal.controller.messaging.callback."
                        "subscribe_system_events", return_value=mock.Mock()) as sc:
            self.ds._start_system_events_subscription()
        sc.assert_called_once()
        self.assertIsNotNone(self.ds._system_events_thread)


class TestSystemEventCallbacks(DeviceServiceExtendedBase):
    def test_on_device_added(self):
        self.ds._logger = mock.Mock()
        self.ds._on_device_added({"name": "nd", "profileName": "p1",
                                  "protocols": {"a": {"b": "c"}}})
        device, ok = Devices().for_name("nd")
        self.assertTrue(ok)
        self.assertEqual(device.profile_name, "p1")

    def test_on_device_updated(self):
        Devices().add(_device("dev1", description="old"))
        self.ds._logger = mock.Mock()
        self.ds._on_device_updated("dev1", {"description": "new"})
        self.assertEqual(Devices().for_name("dev1")[0].description, "new")

    def test_on_device_deleted(self):
        Devices().add(_device("dev1"))
        self.ds._logger = mock.Mock()
        self.ds._on_device_deleted("dev1")
        self.assertFalse(Devices().for_name("dev1")[1])

    def test_on_profile_added_noop(self):
        self.ds._logger = mock.Mock()
        self.ds._on_profile_added({"name": "p"})
        self.ds._logger.debug.assert_called()

    def test_on_profile_updated(self):
        Profiles().add(_profile("np"))
        self.ds._logger = mock.Mock()
        self.ds._on_profile_updated({"name": "np", "description": "d",
                                     "manufacturer": "m", "model": "x",
                                     "labels": ["l"], "deviceResources": [],
                                     "deviceCommands": [], "resources": []})
        profile, ok = Profiles().for_name("np")
        self.assertTrue(ok)
        self.assertEqual(profile.description, "d")

    def test_on_profile_deleted(self):
        Profiles().add(_profile("p1"))
        self.ds._logger = mock.Mock()
        self.ds._on_profile_deleted("p1")
        self.assertFalse(Profiles().for_name("p1")[1])

    def test_on_watcher_added(self):
        self.ds._logger = mock.Mock()
        self.ds._on_watcher_added({"name": "nw", "profileName": "p1",
                                   "identifiers": {"Address": ".*"},
                                   "adminState": "UNLOCKED",
                                   "created": 1, "modified": 2, "origin": 3})
        watcher, ok = ProvisionWatchers().for_name("nw")
        self.assertTrue(ok)
        self.assertEqual(watcher.origin, 3)

    def test_on_watcher_updated(self):
        ProvisionWatchers().add(_watcher("w1"))
        self.ds._logger = mock.Mock()
        self.ds._on_watcher_updated("w1", {"description": "updated"})
        self.assertEqual(ProvisionWatchers().for_name("w1")[0].description,
                         "updated")

    def test_on_watcher_updated_missing(self):
        self.ds._logger = mock.Mock()
        self.ds._on_watcher_updated("ghost", {})
        self.ds._logger.warning.assert_called()

    def test_on_watcher_deleted(self):
        ProvisionWatchers().add(_watcher("w1"))
        self.ds._logger = mock.Mock()
        self.ds._on_watcher_deleted("w1")
        self.assertFalse(ProvisionWatchers().for_name("w1")[1])

    def test_on_service_updated(self):
        self.ds._logger = mock.Mock()
        self.ds._on_service_updated({"admin_state": "LOCKED"})
        self.assertEqual(self.ds.device_service_model.admin_state, "LOCKED")
        self.ds._logger.info.assert_called()


class _ConfigStopEvent:
    def __init__(self):
        self._n = 0

    def is_set(self):
        self._n += 1
        return self._n >= 2

    def wait(self, timeout=None):
        return False


class _CaptureThread:
    def __init__(self, target=None, daemon=True, name=""):
        self.target = target

    def start(self):
        pass

    def is_alive(self):
        return False

    def join(self, timeout=None):
        pass


class TestConfigWatch(DeviceServiceExtendedBase):
    def _config_file(self, tmp, content="key: value\n"):
        path = os.path.join(tmp, "cfg.yaml")
        with open(path, "w") as handle:
            handle.write(content)
        return path

    def test_watch_requires_load(self):
        with self.assertRaises(RuntimeError):
            self.ds.listen_for_custom_config_changes(
                mock.Mock(custom_config_path="/x"), "s", lambda c: None)

    def test_watch_already_watching(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config_file(tmp)
            config = mock.Mock(custom_config_path=path)
            ds = _service()
            ds.load_custom_config(config, "s")
            with mock.patch("threading.Thread", _CaptureThread):
                ds.listen_for_custom_config_changes(config, "s", lambda c: None)
                ds._logger = mock.Mock()
                ds.listen_for_custom_config_changes(config, "s", lambda c: None)
            ds._logger.warning.assert_called()

    def test_watch_detects_initial_and_loop_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config_file(tmp)
            config = mock.Mock(custom_config_path=path)
            ds = _service()
            ds.load_custom_config(config, "s")
            calls = []
            with mock.patch("threading.Thread", _ImmediateThread):
                with mock.patch("threading.Event", _ConfigStopEvent):
                    with mock.patch("os.path.getmtime",
                                    side_effect=[100.0, 200.0, 300.0]):
                        ds.listen_for_custom_config_changes(
                            config, "s", lambda c: calls.append(c))
        # initial check (100 -> 200) and one loop iteration (200 -> 300)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].get("key"), "value")

    def test_watch_parse_error_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config_file(tmp)
            config = mock.Mock(custom_config_path=path)
            ds = _service()
            ds.load_custom_config(config, "s")
            ds._logger = mock.Mock()
            with mock.patch("threading.Thread", _ImmediateThread):
                with mock.patch("threading.Event", _ConfigStopEvent):
                    with mock.patch("os.path.getmtime",
                                    side_effect=[100.0, 200.0, 300.0]):
                        with mock.patch("yaml.safe_load",
                                        side_effect=ValueError("bad")):
                            ds.listen_for_custom_config_changes(
                                config, "s", lambda c: None)
        self.assertTrue(ds._logger.error.called)

    def test_watch_callback_error_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config_file(tmp)
            config = mock.Mock(custom_config_path=path)
            ds = _service()
            ds.load_custom_config(config, "s")
            ds._logger = mock.Mock()

            def boom(_config):
                raise RuntimeError("cb")

            with mock.patch("threading.Thread", _ImmediateThread):
                with mock.patch("threading.Event", _ConfigStopEvent):
                    with mock.patch("os.path.getmtime",
                                    side_effect=[100.0, 200.0, 300.0]):
                        ds.listen_for_custom_config_changes(config, "s", boom)
        self.assertTrue(ds._logger.error.called)

    def test_watch_mtime_error_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config_file(tmp)
            config = mock.Mock(custom_config_path=path)
            ds = _service()
            ds.load_custom_config(config, "s")
            ds._logger = mock.Mock()
            with mock.patch("threading.Thread", _ImmediateThread):
                with mock.patch("threading.Event", _ConfigStopEvent):
                    with mock.patch("os.path.getmtime",
                                    side_effect=[100.0, RuntimeError("io")]):
                        ds.listen_for_custom_config_changes(
                            config, "s", lambda c: None)
        self.assertTrue(ds._logger.error.called)


class TestSecretProviderSecureMode(DeviceServiceExtendedBase):
    def test_secure_mode_from_config(self):
        ds = _service(_MockConfig(device=_MockDeviceOpts(
            secure_mode=True,
            secretstore_token_file="/tmp/token",
            vault_addr="http://vault:8200")))
        with mock.patch("device_sdk_py.internal.clients.create_secret_provider",
                        return_value="provider") as cp:
            self.assertEqual(ds.secret_provider(), "provider")
        self.assertEqual(cp.call_args.kwargs["mode"], "secure")
        self.assertEqual(cp.call_args.kwargs["base_url"], "http://vault:8200")


class TestMetadataNoopPaths(DeviceServiceExtendedBase):
    def test_patch_device_skips_none_values(self):
        dev = _device("dev1", description="orig")
        Devices().add(dev)
        client = mock.Mock()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            self.ds._patch_device_in_metadata(
                "dev1", {"description": None, "operating_state": OPERATING_STATE_DOWN},
                bypass_validation=True)
        self.assertEqual(Devices().for_name("dev1")[0].description, "orig")
        self.assertEqual(Devices().for_name("dev1")[0].operating_state,
                         OPERATING_STATE_DOWN)
        client.patch_device.assert_called_once()

    def test_delete_device_missing_noop(self):
        client = mock.Mock()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            self.ds._delete_device_from_metadata("ghost")
        client.delete_device.assert_not_called()

    def test_update_profile_missing_noop(self):
        client = mock.Mock()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            self.ds._update_profile_in_metadata(_profile("ghost"))
        client.update_device_profile.assert_not_called()

    def test_delete_profile_missing_noop(self):
        client = mock.Mock()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            self.ds._delete_profile_from_metadata("ghost")
        client.delete_device_profile.assert_not_called()

    def test_update_watcher_missing_noop(self):
        client = mock.Mock()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            self.ds._update_provision_watcher_in_metadata(_watcher("ghost"))
        client.update_provision_watcher.assert_not_called()

    def test_delete_watcher_missing_noop(self):
        client = mock.Mock()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            self.ds._delete_provision_watcher_from_metadata("ghost")
        client.delete_provision_watcher.assert_not_called()


class TestRestartAutoEvents(DeviceServiceExtendedBase):
    def test_restart_without_manager(self):
        self.ds._logger = mock.Mock()
        with mock.patch.object(self.ds, "_auto_event_manager", return_value=None):
            self.ds._restart_auto_events("dev1")
        self.ds._logger.debug.assert_called()


class TestRunAndShutdown(DeviceServiceExtendedBase):
    def _stub_run_internals(self, ds):
        ds._init_messaging_client = mock.Mock()
        ds._start_auto_events = mock.Mock()
        ds._init_http_controller = mock.Mock()
        ds._start_device_validation_handler = mock.Mock()
        ds._start_async_pumps = mock.Mock()
        ds._start_command_subscription = mock.Mock()
        ds._start_system_events_subscription = mock.Mock()
        ds.driver = mock.Mock()

    def test_run_without_uvicorn(self):
        ds = _service()
        self._stub_run_internals(ds)
        with mock.patch("builtins.__import__",
                        side_effect=ImportError("no uvicorn")):
            ds.run()
        ds.driver.start.assert_called_once()

    def test_run_serves_with_uvicorn(self):
        ds = _service()
        self._stub_run_internals(ds)
        ds.controller = mock.Mock()
        fake_uvicorn = mock.Mock()
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "uvicorn":
                return fake_uvicorn
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            ds.run()
        fake_uvicorn.run.assert_called_once()
        self.assertTrue(ds._shutdown_event.is_set())

    def test_run_secure_mode_without_ssl(self):
        os.environ["EDGEX_SECURE_MODE"] = "true"
        ds = _service(_MockConfig(device=_MockDeviceOpts()))
        self._stub_run_internals(ds)
        with mock.patch.object(ds, "_register_with_security_services") as reg:
            with mock.patch("builtins.__import__",
                            side_effect=ImportError("no uvicorn")):
                ds.run()
            reg.assert_called_once()
        ds.driver.start.assert_called_once()

    def test_run_secure_mode_with_ssl(self):
        os.environ["EDGEX_SECURE_MODE"] = "true"
        ds = _service(_MockConfig(device=_MockDeviceOpts(
            ssl_certfile="/c.pem", ssl_keyfile="/k.pem")))
        self._stub_run_internals(ds)
        ds.controller = mock.Mock()
        fake_uvicorn = mock.Mock()
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "uvicorn":
                return fake_uvicorn
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            ds.run()
        self.assertEqual(fake_uvicorn.run.call_args.kwargs["ssl_certfile"],
                         "/c.pem")
        self.assertEqual(fake_uvicorn.run.call_args.kwargs["ssl_keyfile"],
                         "/k.pem")

    def test_run_secure_mode_serves_without_ssl(self):
        os.environ["EDGEX_SECURE_MODE"] = "true"
        ds = _service(_MockConfig(device=_MockDeviceOpts()))
        self._stub_run_internals(ds)
        ds.controller = mock.Mock()
        ds._logger = mock.Mock()
        fake_uvicorn = mock.Mock()
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "uvicorn":
                return fake_uvicorn
            return real_import(name, *args, **kwargs)

        with mock.patch.object(ds, "_register_with_security_services") as reg:
            with mock.patch("builtins.__import__", side_effect=fake_import):
                ds.run()
            reg.assert_called_once()
        self.assertIsNone(fake_uvicorn.run.call_args.kwargs["ssl_certfile"])
        self.assertIsNone(fake_uvicorn.run.call_args.kwargs["ssl_keyfile"])
        ds._logger.warning.assert_called()

    def test_shutdown_disconnects_messaging_client(self):
        ds = _service()
        client = mock.Mock()
        ds._messaging_client = client
        executor = mock.Mock()
        ds._metadata_executor = executor
        provider = mock.Mock()
        ds._secret_provider = provider
        ds._config_watch_stop = {"s": threading.Event()}
        ds._config_watch_threads = {"s": _FakeThread()}
        ds._async_pump_thread = _FakeThread()
        ds._discovered_pump_thread = _FakeThread()
        ds._command_sub_thread = _FakeThread()
        ds._system_events_thread = _FakeThread()
        ds._device_return_thread = _FakeThread()
        ds._shutdown()
        client.disconnect.assert_called_once()
        executor.shutdown.assert_called_once()
        provider.close.assert_called_once()

    def test_shutdown_disconnect_error_logged(self):
        ds = _service()
        client = mock.Mock()
        client.disconnect.side_effect = OSError("net")
        ds._messaging_client = client
        ds._logger = mock.Mock()
        ds._shutdown()
        ds._logger.debug.assert_called()


class TestInitHttpController(DeviceServiceExtendedBase):
    def test_already_initialized(self):
        self.ds.controller = mock.Mock()
        with mock.patch("device_sdk_py.service.device_service.RestController") as rc:
            self.ds._init_http_controller()
            rc.assert_not_called()

    def test_init_with_pending_routes(self):
        controller = mock.Mock()
        with mock.patch("device_sdk_py.service.device_service.RestController",
                        return_value=controller):
            self.ds.add_custom_route("/a", False, lambda: None)
            self.ds.add_custom_route("/b", True, lambda: None)
            self.ds._init_http_controller()
        controller.init_rest_routes.assert_called_once()
        self.assertEqual(controller.add_route.call_count, 2)
        self.assertEqual(self.ds._pending_custom_routes, [])

    def test_init_pending_route_error(self):
        controller = mock.Mock()
        controller.add_route.side_effect = EdgexError(KIND_CONTRACT_INVALID,
                                                      "reserved")
        self.ds._logger = mock.Mock()
        with mock.patch("device_sdk_py.service.device_service.RestController",
                        return_value=controller):
            self.ds.add_custom_route("/reserved", False, lambda: None)
            self.ds._init_http_controller()
        self.ds._logger.warning.assert_called()

    def test_init_sets_custom_config(self):
        self.ds.custom_config = {"a": 1}
        controller = mock.Mock()
        with mock.patch("device_sdk_py.service.device_service.RestController",
                        return_value=controller):
            self.ds._init_http_controller()
        controller.set_custom_config_info.assert_called_once_with({"a": 1})

    def test_init_passes_metrics_provider(self):
        controller = mock.Mock()
        with mock.patch("device_sdk_py.service.device_service.RestController",
                        return_value=controller) as rc:
            self.ds._init_http_controller()
        metrics_provider = rc.call_args.kwargs["metrics_provider"]
        self.assertTrue(callable(metrics_provider))
        self.assertEqual(metrics_provider(), self.ds.metrics_manager().get_all_metrics())


if __name__ == "__main__":
    unittest.main()