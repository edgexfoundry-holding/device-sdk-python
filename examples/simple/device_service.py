# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache 2.0
"""A minimal runnable Device Service ("device-simple") for the EdgeX device-sdk-python.

This is the Python counterpart of the Go ``device-simple`` example.  It wires a trivial
ProtocolDriver (which synthesises a fake reading on every Get) into the SDK bootstrap and
serves the standard EdgeX REST API on the configured port (default 59986).

The pre-defined DeviceProfile / Device / ProvisionWatcher shipped under ``res/`` are
loaded and registered into the internal caches by the bootstrap (mirrors the Go
``bootstrap`` handler which populates the caches from the resources path).  No entities
are registered in-process by this file.

Run with::

    python -m examples.simple.device_service

Then probe::

    curl http://localhost:59986/api/v3/ping
    curl http://localhost:59986/api/v3/version
    curl http://localhost:59986/api/v3/device/name/fake/Get
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Optional

# Make the in-repo ``src/`` package importable when running the example directly.
_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.interfaces import ProtocolDriver, ExtendedProtocolDriver
from device_sdk_py.models import (
    CommandRequest,
    CommandValue,
    VALUETYPE_BOOL,
    VALUETYPE_FLOAT32,
    VALUETYPE_INT32,
    VALUETYPE_STRING,
)
from device_sdk_py.service.bootstrap import bootstrap


class _Paths:
    """Mimics the EdgeX ``configurationstruct.Paths`` (res root)."""

    def __init__(self, res_root: str) -> None:
        self.res_root = res_root


class _Service:
    """Mimics the EdgeX ``configurationstruct.Service`` (host / port)."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port


class _MessageBus:
    """Mimics the EdgeX ``MessageBus`` config (MQTT broker + base topic prefix)."""

    def __init__(self, host: str, port: int, base_topic_prefix: str = "edgex",
                 message_bus_type: str = "mqtt", auth_mode: str = "none",
                 optional: Optional[Dict[str, str]] = None,
                 # TLS/SSL options for Secure MessageBus
                 cert_file: str = "", key_file: str = "", ca_file: str = "",
                 skip_cert_verify: bool = False) -> None:
        self.host = host
        self.port = port
        self.base_topic_prefix = base_topic_prefix
        self.type = message_bus_type
        self.auth_mode = auth_mode
        self.optional = optional or {}
        # TLS/SSL options for Secure MessageBus
        if cert_file:
            self.optional["CertFile"] = cert_file
        if key_file:
            self.optional["KeyFile"] = key_file
        if ca_file:
            self.optional["CaFile"] = ca_file
        if skip_cert_verify:
            self.optional["SkipCertVerify"] = "true"


class _Device:
    """Mimics the EdgeX ``configurationstruct.Device`` (labels, resource dirs, discovery, device down, security)."""

    def __init__(self, labels=None) -> None:
        self.labels = list(labels or [])
        self.profiles_dir = "./res/profiles"
        self.devices_dir = "./res/devices"
        self.provision_watchers_dir = "./res/provisionwatchers"
        self.discovery = None
        # Device Down auto-recovery options
        self.allowed_fails = 3
        self.device_down_timeout = 30
        self.async_buffer_size = 100
        self.max_cmd_result_len = 1024
        self.max_event_size = 4096
        self.reading_units = True
        self.send_changed_readings_only = False
        # Secure mode options
        self.secure_mode = False
        self.ssl_certfile = ""
        self.ssl_keyfile = ""
        self.secretstore_token_file = ""
        self.vault_addr = ""
        self.openbao_addr = ""

        self._watchers_dir = "./res/provisionwatchers"
        self.discovery = None


class Configuration:
    """The device-simple configuration consumed by the SDK bootstrap.

    Carries the EdgeX-style sections the ``DeviceService`` reads defensively: ``service``
    (host / port), ``paths`` (res root), ``clients`` (core-metadata endpoint), ``message_bus``
    (MQTT broker for device validation) and ``device`` (labels).  ``Configuration.load`` reads
    these from ``res/configuration.yaml``.
    """

    def __init__(self, res_root: str, host: str = "0.0.0.0", port: int = 59986,
                 enable_metadata: bool = False) -> None:
        self.paths = _Paths(res_root)
        self.service = _Service(host=host, port=port)
        self.device = _Device()
        self.message_bus = None
        # The EdgeX-style ``clients`` map used by ``DeviceService`` to resolve the Core
        # Metadata endpoint.  Enabled from ``main()`` so the runnable service registers
        # itself / profiles / devices / watchers with core-keeper; unit tests keep it off
        # so they stay hermetic.
        self.clients = None
        if enable_metadata:
            base_url = os.environ.get(
                "EDGEX_CORE_METADATA_URL", "http://localhost:59881")
            self.clients = {"core-metadata": {"base_url": base_url}}

    @classmethod
    def load(cls, config_path: str) -> "Configuration":
        """Build a ``Configuration`` from an EdgeX-style ``configuration.yaml``.

        Args:
            config_path: Path to the YAML file.  Relative paths inside it (e.g. ``res/``)
                are resolved against the file's directory.
        """
        import yaml

        with open(config_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        base_dir = os.path.dirname(os.path.abspath(config_path))

        def get_val(d: dict, *keys) -> str:
            for key in keys:
                val = d.get(key)
                if val:
                    return val
            return ""

        service = data.get("Service", {}) or {}
        clients = data.get("Clients", {}) or {}
        message_bus = data.get("MessageBus", {}) or {}
        device = data.get("Device", {}) or {}
        paths = data.get("Paths", {}) or {}

        res_dir = get_val(paths, "Res", "res")
        res_root = res_dir if os.path.isabs(res_dir) else os.path.join(base_dir, res_dir)
        res_root = os.path.abspath(res_root)
        config = cls(res_root=res_root,
                     host=get_val(service, "Host") or "0.0.0.0",
                     port=int(get_val(service, "Port") or 59986))

        # Wire the raw dicts into the sections DeviceService reads defensively.
        hosty = (clients.get("core-metadata") or {})
        if isinstance(hosty, dict) and (hosty.get("Host") or hosty.get("base_url")):
            if hosty.get("base_url"):
                config.clients = {"core-metadata": {"base_url": hosty["base_url"]}}
            elif hosty.get("Host") and hosty.get("Port"):
                config.clients = {"core-metadata": {
                    "host": hosty["Host"], "port": hosty["Port"]}}

        config.message_bus = _MessageBus(
            host=get_val(message_bus, "Host") or "127.0.0.1",
            port=int(get_val(message_bus, "Port") or 1883),
            base_topic_prefix=get_val(message_bus, "BaseTopicPrefix", "baseTopicPrefix") or "edgex",
            message_bus_type=get_val(message_bus, "Type", "type") or "mqtt",
            auth_mode=get_val(message_bus, "AuthMode", "authMode") or "none",
            optional=message_bus.get("Optional", {}) or {})

        config.device = _Device(labels=device.get("Labels") or [])
        config.device.allowed_fails = int(get_val(device, "AllowedFails") or 3)
        config.device.device_down_timeout = int(get_val(device, "DeviceDownTimeout") or 30)
        config.device.async_buffer_size = int(get_val(device, "AsyncBufferSize") or 100)
        config.device.max_cmd_result_len = int(get_val(device, "MaxCmdResultLen") or 1024)
        config.device.max_event_size = int(get_val(device, "MaxEventSize") or 4096)
        config.device.reading_units = get_val(device, "ReadingUnits", "readingUnits") != "false"
        config.device.send_changed_readings_only = get_val(device, "SendChangedReadingsOnly", "sendChangedReadingsOnly") == "true"

        startup_msg = get_val(service, "StartupMsg")
        if startup_msg:
            config.service.startup_msg = startup_msg
        return config


class SimpleDriver(ExtendedProtocolDriver):
    """A toy ProtocolDriver that returns a synthetic, type-correct reading for any Get.
    
    Also implements ExtendedProtocolDriver for profile scanning and discovery control.
    """

    def initialize(self, sdk: Any) -> None:
        # In Go device-simple this is a no-op; retain the SDK for async reads and discovery.
        self._sdk = sdk

    def discover(self) -> None:
        """Trigger protocol-specific device discovery.

        Simulates discovering a device and sends it to the SDK via the discovered
        device channel. In a real driver, this would scan the network/protocol
        for devices and emit DiscoveredDevice objects.
        """
        if not hasattr(self, "_sdk") or self._sdk is None:
            print("Warning: SDK not initialized, cannot discover devices")
            return
        from device_sdk_py.models import DiscoveredDevice
        d = DiscoveredDevice(
            name="simulated-sensor",
            protocols={"modbus": {"address": "1", "port": "502"}},
            description="Simulated Modbus sensor",
            labels=["simulated", "modbus"],
        )
        try:
            self._sdk.discovered_device_channel().put([d])
            print(f"Discovered device: {d.name}")
        except Exception as exc:
            print(f"Failed to send discovered device: {exc}")

    def _default_value(self, value_type: str) -> Any:
        if value_type == VALUETYPE_BOOL:
            return True
        if value_type in (VALUETYPE_INT32,):
            return 42
        if value_type == VALUETYPE_FLOAT32:
            return 3.14
        if value_type == VALUETYPE_STRING:
            return "ok"
        return ""

    def handle_read_commands(self, device_name: str,
                             protocols: Dict[str, Dict[str, Any]],
                             reqs: List[CommandRequest]) -> List[CommandValue]:
        return [
            CommandValue(
                device_resource_name=req.resource_name,
                value_type=req.value_type,
                value=self._default_value(req.value_type),
                origin=time.time_ns(),
            )
            for req in reqs
        ]

    def handle_write_commands(self, device_name: str,
                              protocols: Dict[str, Dict[str, Any]],
                              reqs: List[CommandRequest],
                              params: List[CommandValue]) -> None:
        # No real device to write to.
        pass

    def start(self) -> None:
        print("device-simple driver started")

    def stop(self, force: bool = False) -> None:  # noqa: D401 - match Go signature
        print("device-simple driver stopped")

    def add_device(self, device_name: str, protocols: Dict[str, Dict[str, Any]],
                   admin_state: str) -> None:
        pass

    def update_device(self, device_name: str, protocols: Dict[str, Dict[str, Any]],
                      admin_state: str) -> None:
        pass

    def remove_device(self, device_name: str,
                      protocols: Dict[str, Dict[str, Any]]) -> None:
        pass

    def validate_device(self, device: Any) -> None:
        pass

    def discover(self) -> None:
        """Trigger protocol-specific device discovery.

        Simulates discovering a device and sends it to the SDK via the discovered
        device channel. In a real driver, this would scan the network/protocol
        for devices and emit DiscoveredDevice objects.
        """
        if not hasattr(self, "_sdk") or self._sdk is None:
            print("Warning: SDK not initialized, cannot discover devices")
            return
        from device_sdk_py.models import DiscoveredDevice
        d = DiscoveredDevice(
            name="simulated-sensor",
            protocols={"modbus": {"address": "1", "port": "502"}},
            description="Simulated Modbus sensor",
            labels=["simulated", "modbus"],
        )
        try:
            self._sdk.discovered_device_channel().put([d])
            print(f"Discovered device: {d.name}")
        except Exception as exc:
            print(f"Failed to send discovered device: {exc}")

    # -- ExtendedProtocolDriver methods -----------------------------------------

    def profile_scan(self, device_name: str, profile_name: str, request_id: str,
                     options: Dict[str, Any]) -> "DeviceProfile":
        """Trigger a profile scan for the specified device.

        Simulates discovering the device's resources and commands to create
        a DeviceProfile. In a real driver, this would query the device
        to discover its resources and commands.

        Args:
            device_name: Name of the device to scan.
            profile_name: Name for the generated profile.
            request_id: Correlation ID for the scan request.
            options: Additional scan options.

        Returns:
            The generated DeviceProfile.
        """
        from device_sdk_py.internal.cache import DeviceProfile, DeviceResource, ResourceProperties
        
        self._logger.info("Starting profile scan for device %s -> profile %s (request %s)",
                          device_name, profile_name, request_id)

        # Simulate scan delay
        import time
        time.sleep(0.1)

        # Build a basic DeviceProfile from the device's protocols
        profile = DeviceProfile(
            name=profile_name,
            description=f"Auto-generated profile for {device_name}",
            device_resources=[],
            device_commands=[],
        )
        
        # Get the device from cache to read its protocols
        from device_sdk_py.internal.cache import Devices
        device, ok = Devices().for_name(device_name)
        if ok:
            for proto_name, proto_props in device.protocols.items():
                for prop_name, prop_value in proto_props.items():
                    profile.device_resources.append(DeviceResource(
                        name=prop_name,
                        description=f"Auto-discovered from {proto_name}",
                        properties=ResourceProperties(value_type="String"),
                    ))

        self._logger.info("Profile scan completed for device %s", device_name)
        return profile

    def stop_device_discovery(self, request_id: str, options: Dict[str, Any]) -> None:
        """Stop a running device discovery operation.

        Args:
            request_id: The correlation ID of the discovery to stop.
            options: Additional stop options.
        """
        self._logger.info("StopDeviceDiscovery called: request_id=%s, options=%s", request_id, options)
        # In a real driver, this would signal the discovery loop to stop
        pass

    def stop_profile_scan(self, device_name: str, options: Dict[str, Any]) -> None:
        """Stop a running profile scan operation.

        Args:
            device_name: Name of the device whose profile scan should stop.
            options: Additional stop options.
        """
        self._logger.info("StopProfileScan called: device_name=%s, options=%s", device_name, options)
        # In a real driver, this would signal the profile scan loop to stop
        pass


def build_service(enable_metadata: bool = False) -> Any:
    """Build (but do not run) the device-simple service.

    The bootstrap loads ``res/`` (profiles / devices / provision watchers) into the caches
    before returning the DeviceService.  Exposed for tests and for callers that want to
    register custom routes before ``run()``.  With ``enable_metadata=True`` the service,
    profiles, devices and watchers are also registered with Core Metadata (core-keeper) at
    startup - the configuration (host / port / clients / message bus) is then read from
    ``res/configuration.yaml`` instead of the in-code defaults.
    """
    if enable_metadata:
        config_path = os.path.join(os.path.dirname(__file__), "res", "configuration.yaml")
        configuration = Configuration.load(config_path)
    else:
        res_root = os.path.join(os.path.dirname(__file__), "res")
        configuration = Configuration(res_root=res_root)
    return bootstrap(
        service_key="device-simple",
        service_version="0.0.0",
        driver=SimpleDriver(),
        configuration=configuration,
    )


def main() -> None:
    """Bootstrap and run the device-simple service (blocking)."""
    build_service(enable_metadata=True).run()


if __name__ == "__main__":
    main()
