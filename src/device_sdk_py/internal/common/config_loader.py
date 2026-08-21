# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
YAML + environment -> ConfigurationStruct loader.

Mirrors the Go SDK's go-mod-bootstrap config loading: read ``configuration.yaml``,
apply EdgeX v4 env overrides (``EDGEX_*``), then the legacy Python env vars used
by the example service (``SERVICE_HOST``, ``EDGEX_CORE_METADATA_URL``, etc.).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import yaml

from .configuration import (
    ConfigurationStruct,
    ClientInfo,
    DeviceInfo,
    DiscoveryInfo,
    AutoEventInfo,
    MessageBusInfo,
    RegistryInfo,
    ServiceInfo,
    CORSConfigurationInfo,
    WritableInfo,
    ReadingInfo,
    TelemetryInfo,
    TelemetryMetrics,
    SecurityOptions,
    PathsInfo,
    InsecureSecretsInfo,
    default_configuration,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_str(data: Dict[str, Any], key: str, default: str = "") -> str:
    val = data.get(key)
    return str(val) if val is not None else default


def _get_int(data: Dict[str, Any], key: str, default: int = 0) -> int:
    val = data.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _get_bool(data: Dict[str, Any], key: str, default: bool = False) -> bool:
    val = data.get(key)
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "1", "yes")


def _env_str(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int = 0) -> int:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")


def _apply_security_options(
        target: SecurityOptions, data: Dict[str, Any]) -> None:
    if not data:
        return
    target.Mode = _get_str(data, "Mode")
    target.OpenZitiController = _get_str(data, "OpenZitiController")


def _apply_cors(target: CORSConfigurationInfo, data: Dict[str, Any]) -> None:
    if not data:
        return
    target.CORSAllowCredentials = _get_bool(data, "CORSAllowCredentials")
    target.CORSAllowedHeaders = _get_str(data, "CORSAllowedHeaders",
                                         target.CORSAllowedHeaders)
    target.CORSAllowedMethods = _get_str(data, "CORSAllowedMethods",
                                         target.CORSAllowedMethods)
    target.CORSAllowedOrigin = _get_str(data, "CORSAllowedOrigin",
                                        target.CORSAllowedOrigin)
    target.CORSExposeHeaders = _get_str(data, "CORSExposeHeaders",
                                        target.CORSExposeHeaders)
    target.CORSMaxAge = _get_int(data, "CORSMaxAge", target.CORSMaxAge)
    target.EnableCORS = _get_bool(data, "EnableCORS")


# ---------------------------------------------------------------------------
# Section appliers
# ---------------------------------------------------------------------------


def _apply_service(cfg: ConfigurationStruct, data: Dict[str, Any]) -> None:
    if not data:
        return
    s = cfg.Service
    s.Host = _get_str(data, "Host")
    s.Port = _get_int(data, "Port")
    s.StartupMsg = _get_str(data, "StartupMsg")
    s.HealthCheckInterval = _get_str(data, "HealthCheckInterval", s.HealthCheckInterval)
    s.MaxResultCount = _get_int(data, "MaxResultCount", s.MaxResultCount)
    s.RequestTimeout = _get_str(data, "RequestTimeout", s.RequestTimeout)
    s.MaxRequestSize = _get_int(data, "MaxRequestSize", s.MaxRequestSize)
    s.ServerBindAddr = _get_str(data, "ServerBindAddr")
    s.EnableNameFieldEscape = _get_bool(data, "EnableNameFieldEscape")
    _apply_cors(s.CORSConfiguration, data.get("CORSConfiguration", {}))
    _apply_security_options(s.SecurityOptions, data.get("SecurityOptions", {}))


def _apply_message_bus(cfg: ConfigurationStruct, data: Dict[str, Any]) -> None:
    if not data:
        return
    m = cfg.MessageBus
    m.Host = _get_str(data, "Host")
    m.Port = _get_int(data, "Port")
    m.Protocol = _get_str(data, "Protocol", m.Protocol)
    m.Type = _get_str(data, "Type", m.Type)
    m.AuthMode = _get_str(data, "AuthMode", m.AuthMode)
    m.BaseTopicPrefix = _get_str(data, "BaseTopicPrefix", m.BaseTopicPrefix)
    m.Disabled = _get_bool(data, "Disabled")
    m.SecretName = _get_str(data, "SecretName", m.SecretName)
    # Optional map
    optional = data.get("Optional", {})
    if optional:
        m.Optional.update({str(k): str(v) for k, v in optional.items()})
    # Python extension
    m.PublishTopicPrefix = _get_str(data, "PublishTopicPrefix",
                                    m.PublishTopicPrefix)


def _apply_registry(cfg: ConfigurationStruct, data: Dict[str, Any]) -> None:
    if not data:
        return
    r = cfg.Registry
    r.Host = _get_str(data, "Host")
    r.Port = _get_int(data, "Port")
    r.Type = _get_str(data, "Type")


def _apply_writable(cfg: ConfigurationStruct, data: Dict[str, Any]) -> None:
    if not data:
        return
    w = cfg.Writable
    w.LogLevel = _get_str(data, "LogLevel", w.LogLevel)
    _apply_reading(w.Reading, data.get("Reading", {}))
    _apply_telemetry(w.Telemetry, data.get("Telemetry", {}))
    insecure = data.get("InsecureSecrets", {})
    if insecure:
        for name, info in insecure.items():
            if not isinstance(info, dict):
                continue
            sec = InsecureSecretsInfo()
            sec.SecretName = _get_str(info, "SecretName")
            sec.SecretData = {str(k): str(v) for k, v in info.get("SecretData", {}).items()}
            w.InsecureSecrets[name] = sec


def _apply_reading(target: ReadingInfo, data: Dict[str, Any]) -> None:
    if not data:
        return
    target.ReadingUnits = _get_bool(data, "ReadingUnits", target.ReadingUnits)


def _apply_telemetry(target: TelemetryInfo, data: Dict[str, Any]) -> None:
    if not data:
        return
    target.Interval = _get_str(data, "Interval", target.Interval)
    metrics_data = data.get("Metrics", {})
    if metrics_data:
        for field_name in target.Metrics.__dataclass_fields__:
            setattr(target.Metrics, field_name, _get_bool(metrics_data, field_name, False))
    target.Tags = data.get("Tags") if data.get("Tags") else None


def _apply_device(cfg: ConfigurationStruct, data: Dict[str, Any]) -> None:
    if not data:
        return
    d = cfg.Device
    d.AllowedFails = _get_int(data, "AllowedFails", d.AllowedFails)
    d.AsyncBufferSize = _get_int(data, "AsyncBufferSize", d.AsyncBufferSize)
    d.DataTransform = _get_bool(data, "DataTransform", d.DataTransform)
    d.DeviceDownTimeout = _get_int(data, "DeviceDownTimeout", d.DeviceDownTimeout)
    d.DevicesDir = _get_str(data, "DevicesDir")
    d.EnableAsyncReadings = _get_bool(data, "EnableAsyncReadings", d.EnableAsyncReadings)
    d.MaxCmdOps = _get_int(data, "MaxCmdOps", d.MaxCmdOps)
    d.MaxCmdValueLen = _get_int(data, "MaxCmdValueLen", d.MaxCmdValueLen)
    d.ProfilesDir = _get_str(data, "ProfilesDir")
    d.ProvisionWatchersDir = _get_str(data, "ProvisionWatchersDir")
    # Labels - keep as list if present
    if "Labels" in data:
        d.Labels = data["Labels"] if data["Labels"] else None
    # Discovery
    disc = data.get("Discovery", {})
    if disc:
        d.Discovery.Enabled = _get_bool(disc, "Enabled")
        d.Discovery.Interval = _get_str(disc, "Interval", d.Discovery.Interval)
    # AutoEvents
    auto = data.get("AutoEvents", {})
    if auto:
        d.AutoEvents.SendChangedReadingsOnly = _get_bool(
            auto, "SendChangedReadingsOnly", d.AutoEvents.SendChangedReadingsOnly)
    # Python extensions
    d.SecureMode = _get_bool(data, "SecureMode")
    d.SslCertFile = _get_str(data, "SslCertFile")
    d.SslKeyFile = _get_str(data, "SslKeyFile")
    d.SecretStoreTokenFile = _get_str(data, "SecretStoreTokenFile")
    d.VaultAddr = _get_str(data, "VaultAddr")
    d.OpenBaoAddr = _get_str(data, "OpenBaoAddr")
    d.JwtJwksUrl = _get_str(data, "JwtJwksUrl")
    d.JwtPublicKey = _get_str(data, "JwtPublicKey")
    d.JwtIssuer = _get_str(data, "JwtIssuer")
    d.JwtAudience = _get_str(data, "JwtAudience")
    d.MaxEventSize = _get_int(data, "MaxEventSize")
    d.MaxCmdResultLen = _get_int(data, "MaxCmdResultLen")
    d.ReadingUnits = _get_bool(data, "ReadingUnits", d.ReadingUnits)
    d.MaxConcurrentCommands = _get_int(data, "MaxConcurrentCommands")
    # send_changed_readings_only is the snake_case alias for AutoEvents.SendChangedReadingsOnly
    if "SendChangedReadingsOnly" in data:
        d.send_changed_readings_only = _get_bool(data, "SendChangedReadingsOnly",
                                                 d.AutoEvents.SendChangedReadingsOnly)


def _apply_clients(cfg: ConfigurationStruct, data: Dict[str, Any]) -> None:
    if not data:
        return
    for name, info in data.items():
        if not isinstance(info, dict):
            continue
        client = ClientInfo()
        client.Host = _get_str(info, "Host")
        client.Port = _get_int(info, "Port")
        client.Protocol = _get_str(info, "Protocol", client.Protocol)
        client.UseMessageBus = _get_bool(info, "UseMessageBus")
        _apply_security_options(client.SecurityOptions, info.get("SecurityOptions", {}))
        client.BaseUrl = _get_str(info, "BaseUrl", _get_str(info, "base_url", ""))
        cfg.Clients[name] = client


def _apply_driver(cfg: ConfigurationStruct, data: Any) -> None:
    if data is not None:
        cfg.Driver = data if isinstance(data, dict) else {"Driver": data}


def _apply_top_level(cfg: ConfigurationStruct, data: Dict[str, Any]) -> None:
    if "MaxEventSize" in data:
        cfg.MaxEventSize = _get_int(data, "MaxEventSize")
        cfg.Device.MaxEventSize = cfg.MaxEventSize
    if "MaxConcurrentCommands" in data:
        cfg.MaxConcurrentCommands = _get_int(data, "MaxConcurrentCommands")
        cfg.Device.MaxConcurrentCommands = cfg.MaxConcurrentCommands


def _apply_paths(cfg: ConfigurationStruct, data: Dict[str, Any], base_dir: str) -> None:
    if not data:
        return
    res_dir = _get_str(data, "Res", _get_str(data, "res", ""))
    if res_dir:
        if os.path.isabs(res_dir):
            cfg.Paths.ResRoot = res_dir
        else:
            cfg.Paths.ResRoot = os.path.abspath(os.path.join(base_dir, res_dir))
    cfg.Paths.Res = _get_str(data, "Res", cfg.Paths.Res)


# ---------------------------------------------------------------------------
# Environment overrides (EdgeX v4 + legacy)
# ---------------------------------------------------------------------------


def _apply_env_overrides(cfg: ConfigurationStruct) -> None:
    """Apply standard EdgeX env vars + legacy example env vars."""
    # Service
    cfg.Service.Host = _env_str("EDGEX_SERVICE_HOST",
                                _env_str("SERVICE_HOST", cfg.Service.Host))
    cfg.Service.Port = _env_int("EDGEX_SERVICE_PORT",
                                _env_int("SERVICE_PORT", cfg.Service.Port))
    cfg.Service.StartupMsg = _env_str("EDGEX_SERVICE_STARTUPMSG", cfg.Service.StartupMsg)

    # MessageBus
    cfg.MessageBus.Host = _env_str("EDGEX_MESSAGEBUS_HOST",
                                   _env_str("MQTT_HOST", cfg.MessageBus.Host))
    cfg.MessageBus.Port = _env_int("EDGEX_MESSAGEBUS_PORT",
                                   _env_int("MQTT_PORT", cfg.MessageBus.Port))
    cfg.MessageBus.Protocol = _env_str("EDGEX_MESSAGEBUS_PROTOCOL", cfg.MessageBus.Protocol)
    cfg.MessageBus.Type = _env_str("EDGEX_MESSAGEBUS_TYPE", cfg.MessageBus.Type)
    cfg.MessageBus.AuthMode = _env_str("EDGEX_MESSAGEBUS_AUTHMODE", cfg.MessageBus.AuthMode)
    cfg.MessageBus.BaseTopicPrefix = _env_str("EDGEX_MESSAGEBUS_BASE_TOPIC_PREFIX",
                                              cfg.MessageBus.BaseTopicPrefix)
    cfg.MessageBus.Disabled = _env_bool("EDGEX_MESSAGEBUS_DISABLED", cfg.MessageBus.Disabled)
    cfg.MessageBus.SecretName = _env_str("EDGEX_MESSAGEBUS_SECRETNAME", cfg.MessageBus.SecretName)

    # Registry
    cfg.Registry.Host = _env_str("EDGEX_REGISTRY_HOST", cfg.Registry.Host)
    cfg.Registry.Port = _env_int("EDGEX_REGISTRY_PORT", cfg.Registry.Port)
    cfg.Registry.Type = _env_str("EDGEX_REGISTRY_TYPE", cfg.Registry.Type)

    # Writable
    cfg.Writable.LogLevel = _env_str("EDGEX_WRITABLE_LOGLEVEL", cfg.Writable.LogLevel)

    # Clients (core-metadata is the common one)
    if "core-metadata" not in cfg.Clients:
        cfg.Clients["core-metadata"] = ClientInfo()
    core_md = cfg.Clients["core-metadata"]
    core_md.Host = _env_str("EDGEX_CLIENTS_CORE_METADATA_HOST",
                            _env_str("EDGEX_CORE_METADATA_HOST", core_md.Host))
    core_md.Port = _env_int("EDGEX_CLIENTS_CORE_METADATA_PORT",
                            _env_int("EDGEX_CORE_METADATA_PORT", core_md.Port))
    core_md.Protocol = _env_str("EDGEX_CLIENTS_CORE_METADATA_PROTOCOL", core_md.Protocol)
    core_md.UseMessageBus = _env_bool("EDGEX_CLIENTS_CORE_METADATA_USEMESSAGEBUS",
                                      core_md.UseMessageBus)
    # Legacy full URL for core-metadata
    core_metadata_url = _env_str("EDGEX_CORE_METADATA_URL", "")
    if core_metadata_url:
        core_md.BaseUrl = core_metadata_url

    # Device
    cfg.Device.MaxEventSize = _env_int("EDGEX_DEVICE_MAXEVENTSIZE", cfg.Device.MaxEventSize)
    cfg.MaxEventSize = cfg.Device.MaxEventSize
    cfg.Device.MaxConcurrentCommands = _env_int("EDGEX_DEVICE_MAXCONCURRENTCOMMANDS",
                                                cfg.Device.MaxConcurrentCommands)
    cfg.MaxConcurrentCommands = cfg.Device.MaxConcurrentCommands
    cfg.Device.AsyncBufferSize = _env_int("EDGEX_DEVICE_ASYNCBUFFERSIZE",
                                          cfg.Device.AsyncBufferSize)
    cfg.Device.DeviceDownTimeout = _env_int("EDGEX_DEVICE_DEVICEDOWNTIMEOUT",
                                            cfg.Device.DeviceDownTimeout)
    cfg.Device.AllowedFails = _env_int("EDGEX_DEVICE_ALLOWEDFAILS", cfg.Device.AllowedFails)
    cfg.Device.DataTransform = _env_bool("EDGEX_DEVICE_DATATRANSFORM", cfg.Device.DataTransform)
    cfg.Device.EnableAsyncReadings = _env_bool("EDGEX_DEVICE_ENABLEASYNCREADINGS",
                                               cfg.Device.EnableAsyncReadings)
    cfg.Device.ReadingUnits = _env_bool("EDGEX_DEVICE_READINGUNITS", cfg.Device.ReadingUnits)
    cfg.Device.SecureMode = _env_bool("EDGEX_DEVICE_SECUREMODE", cfg.Device.SecureMode)
    cfg.Device.SslCertFile = _env_str("EDGEX_DEVICE_SSLCERTFILE", cfg.Device.SslCertFile)
    cfg.Device.SslKeyFile = _env_str("EDGEX_DEVICE_SSLKEYFILE", cfg.Device.SslKeyFile)
    cfg.Device.SecretStoreTokenFile = _env_str("EDGEX_DEVICE_SECRETSTORETOKENFILE",
                                               cfg.Device.SecretStoreTokenFile)
    cfg.Device.VaultAddr = _env_str("EDGEX_DEVICE_VAULTADDR", cfg.Device.VaultAddr)
    cfg.Device.OpenBaoAddr = _env_str("EDGEX_DEVICE_OPENBAOADDR", cfg.Device.OpenBaoAddr)
    cfg.Device.JwtJwksUrl = _env_str("EDGEX_DEVICE_JWTJWKSURL", cfg.Device.JwtJwksUrl)
    cfg.Device.JwtPublicKey = _env_str("EDGEX_DEVICE_JWTPUBLICKEY", cfg.Device.JwtPublicKey)
    cfg.Device.JwtIssuer = _env_str("EDGEX_DEVICE_JWTISSUER", cfg.Device.JwtIssuer)
    cfg.Device.JwtAudience = _env_str("EDGEX_DEVICE_JWTAUDIENCE", cfg.Device.JwtAudience)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_configuration(config_path: str) -> ConfigurationStruct:
    """Load an EdgeX v4 ``configuration.yaml`` into a ``ConfigurationStruct``.

    The loader applies the following overlay order (later wins):
      1. Go defaults (from ``default_configuration()``)
      2. YAML file sections (``Service``, ``MessageBus``, ``Device``, ``Clients``,
         ``Registry``, ``Writable``, ``MaxEventSize``, ``MaxConcurrentCommands``,
         ``Driver``, ``Paths``)
      3. Environment variable overrides (EdgeX ``EDGEX_*`` + legacy ``SERVICE_*``,
         ``EDGEX_CORE_METADATA_URL`` etc.)
    """
    with open(config_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    base_dir = os.path.dirname(os.path.abspath(config_path))
    cfg = default_configuration()

    # YAML overlay
    _apply_service(cfg, data.get("Service", {}))
    _apply_message_bus(cfg, data.get("MessageBus", {}))
    _apply_registry(cfg, data.get("Registry", {}))
    _apply_writable(cfg, data.get("Writable", {}))
    _apply_device(cfg, data.get("Device", {}))
    _apply_clients(cfg, data.get("Clients", {}))
    _apply_driver(cfg, data.get("Driver"))
    _apply_top_level(cfg, data)
    _apply_paths(cfg, data.get("Paths", {}), base_dir)

    # Env overrides
    _apply_env_overrides(cfg)

    return cfg