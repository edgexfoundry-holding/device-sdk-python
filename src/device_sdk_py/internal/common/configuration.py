# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
`device-sdk-go/v4` configuration model.

Ported from `device-sdk-go/v4/internal/config/types.go` (the `ConfigurationStruct`
and its nested structs) so that `GET /api/v3/config` returns the same structure as
the Go SDK: PascalCase section names, Go zero values for unset options and ``null``
for nil maps / slices.

The dataclass fields keep the Go names.  The SDK reads the options defensively via
lowercase ``getattr(configuration, "device", ...)`` etc., so every model exposes the
snake_case aliases through `_GoModel.__getattr__`.  Fields that only exist on the
Python side (secure mode, JWT auth, ``paths.res``, ...) are kept as regular fields
but are excluded from ``to_go_dict()``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: Default topic prefix shared by all EdgeX v3 services.
DEFAULT_TOPIC_PREFIX = "edgex"

#: go-mod-bootstrap defaults for the message bus ``Optional`` map.
_DEFAULT_MESSAGE_BUS_OPTIONAL: Dict[str, str] = {
    "AutoProvision": "true",
    "AutoReconnect": "true",
    "ConnectTimeout": "5",
    "DefaultPubRetryAttempts": "2",
    "Deliver": "new",
    "Durable": "",
    "Format": "nats",
    "KeepAlive": "10",
    "Qos": "0",
    "QueueGroup": "",
    "Retained": "false",
    "RetryOnFailedConnect": "true",
    "SkipCertVerify": "false",
    "Subject": "edgex/#",
}

#: go-mod-bootstrap defaults for the service CORS configuration.
_DEFAULT_CORS_ALLOWED_HEADERS = (
    "Authorization, Accept, Accept-Language, Content-Language, Content-Type, "
    "X-Correlation-ID"
)
_DEFAULT_CORS_EXPOSE_HEADERS = (
    "Cache-Control, Content-Language, Content-Length, Content-Type, Expires, "
    "Last-Modified, Pragma, X-Correlation-ID"
)


def _to_pascal(name: str) -> str:
    """Convert ``async_buffer_size`` to ``AsyncBufferSize``."""
    return "".join(part.capitalize() for part in name.split("_"))


def _sorted_dict(mapping: Dict[str, Any]) -> Dict[str, Any]:
    """Go json.Marshal sorts map keys; keep nested maps deterministic."""
    return dict(sorted(mapping.items()))


class _GoModel:
    """Expose snake_case aliases for the Go-style PascalCase dataclass fields."""

    def __getattr__(self, name: str) -> Any:
        fields = getattr(type(self), "__dataclass_fields__", {})
        pascal = _to_pascal(name)
        if pascal in fields:
            return object.__getattribute__(self, pascal)
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}")


@dataclass
class SecurityOptions(_GoModel):
    """The go-mod-bootstrap ``SecurityOptions`` struct."""

    Mode: str = ""
    OpenZitiController: str = "openziti:1280"

    def to_go_dict(self) -> Dict[str, Any]:
        return _go_dict(
            Mode=self.Mode,
            OpenZitiController=self.OpenZitiController,
        )


@dataclass
class ClientInfo(_GoModel):
    """The go-mod-bootstrap ``ClientInfo`` struct."""

    Host: str = ""
    Port: int = 0
    Protocol: str = "http"
    UseMessageBus: bool = False
    SecurityOptions: Optional[SecurityOptions] = None
    # Python extension (not serialized): pre-computed Core Metadata base URL.
    BaseUrl: str = ""


@dataclass
class DiscoveryInfo(_GoModel):
    """The go-mod-bootstrap ``DiscoveryInfo`` struct."""

    Enabled: bool = False
    Interval: str = "0s"


@dataclass
class AutoEventInfo(_GoModel):
    """The device-sdk-go ``AutoEventInfo`` struct."""

    SendChangedReadingsOnly: bool = False


@dataclass
class DeviceInfo(_GoModel):
    """The device-sdk-go ``DeviceInfo`` struct.

    Python-only extensions (secure mode, JWT auth, res dirs, ...) are stored here so
    the SDK's defensive lowercase reads keep working, but are not serialized.
    """

    AllowedFails: int = 0
    AsyncBufferSize: int = 16
    AutoEvents: AutoEventInfo = field(default_factory=AutoEventInfo)
    DataTransform: bool = True
    DeviceDownTimeout: int = 0
    DevicesDir: str = ""
    Discovery: DiscoveryInfo = field(default_factory=DiscoveryInfo)
    EnableAsyncReadings: bool = True
    Labels: Optional[List[str]] = None
    MaxCmdOps: int = 128
    MaxCmdValueLen: int = 256
    ProfilesDir: str = ""
    ProvisionWatchersDir: str = ""
    # Python extensions (not serialized).
    SecureMode: bool = False
    SslCertFile: str = ""
    SslKeyFile: str = ""
    SecretStoreTokenFile: str = ""
    VaultAddr: str = ""
    OpenBaoAddr: str = ""
    JwtJwksUrl: str = ""
    JwtPublicKey: str = ""
    JwtIssuer: str = ""
    JwtAudience: str = ""
    MaxEventSize: int = 0
    MaxCmdResultLen: int = 0
    ReadingUnits: bool = True
    MaxConcurrentCommands: int = 0

    @property
    def send_changed_readings_only(self) -> bool:
        return self.AutoEvents.SendChangedReadingsOnly

    @send_changed_readings_only.setter
    def send_changed_readings_only(self, value: bool) -> None:
        self.AutoEvents.SendChangedReadingsOnly = bool(value)

    @property
    def async_readings_enabled(self) -> bool:
        return self.EnableAsyncReadings

    @async_readings_enabled.setter
    def async_readings_enabled(self, value: bool) -> None:
        self.EnableAsyncReadings = bool(value)


@dataclass
class MessageBusInfo(_GoModel):
    """The go-mod-bootstrap ``MessageBusInfo`` struct."""

    AuthMode: str = "none"
    BaseTopicPrefix: str = DEFAULT_TOPIC_PREFIX
    Disabled: bool = False
    Host: str = ""
    Optional: Dict[str, str] = field(
        default_factory=lambda: dict(_DEFAULT_MESSAGE_BUS_OPTIONAL))
    Port: int = 0
    Protocol: str = "mqtt"
    SecretName: str = "mqtt-bus"
    Type: str = "mqtt"
    # Python extension (not serialized).
    PublishTopicPrefix: str = ""


@dataclass
class RegistryInfo(_GoModel):
    """The go-mod-bootstrap ``RegistryInfo`` struct."""

    Host: str = ""
    Port: int = 0
    Type: str = "core-keeper"


@dataclass
class CORSConfigurationInfo(_GoModel):
    """The go-mod-bootstrap ``CORSConfigurationInfo`` struct."""

    CORSAllowCredentials: bool = False
    CORSAllowedHeaders: str = _DEFAULT_CORS_ALLOWED_HEADERS
    CORSAllowedMethods: str = "GET, POST, PUT, PATCH, DELETE"
    CORSAllowedOrigin: str = "https://localhost"
    CORSExposeHeaders: str = _DEFAULT_CORS_EXPOSE_HEADERS
    CORSMaxAge: int = 3600
    EnableCORS: bool = False


@dataclass
class ServiceInfo(_GoModel):
    """The go-mod-bootstrap ``ServiceInfo`` struct."""

    CORSConfiguration: CORSConfigurationInfo = field(
        default_factory=CORSConfigurationInfo)
    EnableNameFieldEscape: bool = False
    HealthCheckInterval: str = "10s"
    Host: str = ""
    MaxRequestSize: int = 0
    MaxResultCount: int = 1024
    Port: int = 0
    RequestTimeout: str = "5s"
    SecurityOptions: SecurityOptions = field(default_factory=SecurityOptions)
    ServerBindAddr: str = ""
    StartupMsg: str = ""
    # Python extensions (not serialized).
    BaseAddress: str = ""
    AdvertisedHost: str = ""
    AutoDetectHost: bool = False


@dataclass
class ReadingInfo(_GoModel):
    """The device-sdk-go ``ReadingInfo`` struct."""

    ReadingUnits: bool = True


@dataclass
class TelemetryMetrics(_GoModel):
    """The device-sdk-go ``TelemetryMetrics`` struct."""

    EventsSent: bool = False
    LastConnected: bool = False
    ReadingsSent: bool = False
    SecurityGetSecretDuration: bool = False
    SecurityRuntimeSecretTokenDuration: bool = False
    SecuritySecretsRequested: bool = False
    SecuritySecretsStored: bool = False


@dataclass
class TelemetryInfo(_GoModel):
    """The device-sdk-go ``TelemetryInfo`` struct."""

    Interval: str = "30s"
    Metrics: TelemetryMetrics = field(default_factory=TelemetryMetrics)
    Tags: Optional[Dict[str, str]] = None


@dataclass
class InsecureSecretsInfo(_GoModel):
    """The go-mod-bootstrap ``InsecureSecretsInfo`` struct."""

    SecretName: str = ""
    SecretData: Dict[str, str] = field(default_factory=dict)


@dataclass
class WritableInfo(_GoModel):
    """The device-sdk-go ``WritableInfo`` struct."""

    InsecureSecrets: Dict[str, InsecureSecretsInfo] = field(default_factory=dict)
    LogLevel: str = "INFO"
    Reading: ReadingInfo = field(default_factory=ReadingInfo)
    Telemetry: TelemetryInfo = field(default_factory=TelemetryInfo)


@dataclass
class PathsInfo(_GoModel):
    """Python-only ``paths`` section (res root) - not part of the Go config."""

    ResRoot: str = ""
    Res: str = ""


@dataclass
class ConfigurationStruct(_GoModel):
    """The device-sdk-go ``ConfigurationStruct``.

    Fields keep the Go names; snake_case aliases are available through `_GoModel`
    (``configuration.device``, ``configuration.service``, ...). ``to_go_dict()``
    returns the Go-shaped structure serialized by ``GET /api/v3/config``.
    """

    Clients: Dict[str, ClientInfo] = field(default_factory=dict)
    Device: DeviceInfo = field(default_factory=DeviceInfo)
    Driver: Optional[Dict[str, Any]] = None
    MaxConcurrentCommands: int = 0
    MaxEventSize: int = 0
    MessageBus: MessageBusInfo = field(default_factory=MessageBusInfo)
    Registry: RegistryInfo = field(default_factory=RegistryInfo)
    Service: ServiceInfo = field(default_factory=ServiceInfo)
    Writable: WritableInfo = field(default_factory=WritableInfo)
    Paths: PathsInfo = field(default_factory=PathsInfo)

    def to_go_dict(self) -> Dict[str, Any]:
        """Serialize in the exact shape go-mod-bootstrap returns for ``/config``.

        Top-level keys are written in Go field order (alphabetical); nested maps keep
        Go's sorted-key ordering.  Zero values are kept and nil maps/slices become
        ``None`` (JSON ``null``), matching the Go ``json.Marshal`` output.
        """
        return {
            "Clients": _sorted_dict(
                {name: client.to_go_dict() for name, client in self.Clients.items()}),
            "Device": self.Device.to_go_dict(),
            "Driver": self.Driver,
            "MaxConcurrentCommands": self.MaxConcurrentCommands,
            "MaxEventSize": self.MaxEventSize,
            "MessageBus": self.MessageBus.to_go_dict(),
            "Registry": self.Registry.to_go_dict(),
            "Service": self.Service.to_go_dict(),
            "Writable": self.Writable.to_go_dict(),
        }

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<ConfigurationStruct {list(self.__dataclass_fields__)}>"


def _go_dict(**kwargs: Any) -> Dict[str, Any]:
    return dict(sorted(kwargs.items()))


def default_configuration() -> ConfigurationStruct:
    """Return a ``ConfigurationStruct`` with the go-mod-bootstrap defaults."""
    return ConfigurationStruct()


# ---------------------------------------------------------------------------
# to_go_dict serializers
# ---------------------------------------------------------------------------

def _client_to_go_dict(client: ClientInfo) -> Dict[str, Any]:
    return _go_dict(
        Host=client.Host,
        Port=client.Port,
        Protocol=client.Protocol,
        UseMessageBus=client.UseMessageBus,
        SecurityOptions=client.SecurityOptions.to_go_dict()
        if client.SecurityOptions is not None else None,
    )


def _device_to_go_dict(device: DeviceInfo) -> Dict[str, Any]:
    return _go_dict(
        AllowedFails=device.AllowedFails,
        AsyncBufferSize=device.AsyncBufferSize,
        AutoEvents=_go_dict(
            SendChangedReadingsOnly=device.AutoEvents.SendChangedReadingsOnly),
        DataTransform=device.DataTransform,
        DeviceDownTimeout=device.DeviceDownTimeout,
        DevicesDir=device.DevicesDir,
        Discovery=_go_dict(
            Enabled=device.Discovery.Enabled,
            Interval=device.Discovery.Interval,
        ),
        EnableAsyncReadings=device.EnableAsyncReadings,
        Labels=device.Labels if device.Labels is not None else None,
        MaxCmdOps=device.MaxCmdOps,
        MaxCmdValueLen=device.MaxCmdValueLen,
        ProfilesDir=device.ProfilesDir,
        ProvisionWatchersDir=device.ProvisionWatchersDir,
    )


def _message_bus_to_go_dict(message_bus: MessageBusInfo) -> Dict[str, Any]:
    return _go_dict(
        AuthMode=message_bus.AuthMode,
        BaseTopicPrefix=message_bus.BaseTopicPrefix,
        Disabled=message_bus.Disabled,
        Host=message_bus.Host,
        Optional=_sorted_dict(message_bus.Optional),
        Port=message_bus.Port,
        Protocol=message_bus.Protocol,
        SecretName=message_bus.SecretName,
        Type=message_bus.Type,
    )


def _registry_to_go_dict(registry: RegistryInfo) -> Dict[str, Any]:
    return _go_dict(
        Host=registry.Host,
        Port=registry.Port,
        Type=registry.Type,
    )


def _cors_to_go_dict(cors: CORSConfigurationInfo) -> Dict[str, Any]:
    return _go_dict(
        CORSAllowCredentials=cors.CORSAllowCredentials,
        CORSAllowedHeaders=cors.CORSAllowedHeaders,
        CORSAllowedMethods=cors.CORSAllowedMethods,
        CORSAllowedOrigin=cors.CORSAllowedOrigin,
        CORSExposeHeaders=cors.CORSExposeHeaders,
        CORSMaxAge=cors.CORSMaxAge,
        EnableCORS=cors.EnableCORS,
    )


def _service_to_go_dict(service: ServiceInfo) -> Dict[str, Any]:
    return _go_dict(
        CORSConfiguration=_cors_to_go_dict(service.CORSConfiguration),
        EnableNameFieldEscape=service.EnableNameFieldEscape,
        HealthCheckInterval=service.HealthCheckInterval,
        Host=service.Host,
        MaxRequestSize=service.MaxRequestSize,
        MaxResultCount=service.MaxResultCount,
        Port=service.Port,
        RequestTimeout=service.RequestTimeout,
        SecurityOptions=service.SecurityOptions.to_go_dict()
        if service.SecurityOptions is not None else None,
        ServerBindAddr=service.ServerBindAddr,
        StartupMsg=service.StartupMsg,
    )


def _reading_to_go_dict(reading: ReadingInfo) -> Dict[str, Any]:
    return _go_dict(ReadingUnits=reading.ReadingUnits)


def _telemetry_to_go_dict(telemetry: TelemetryInfo) -> Dict[str, Any]:
    metrics = _sorted_dict(
        {name: getattr(telemetry.Metrics, name)
         for name in telemetry.Metrics.__dataclass_fields__})
    return _go_dict(
        Interval=telemetry.Interval,
        Metrics=metrics,
        Tags=telemetry.Tags if telemetry.Tags is not None else None,
    )


def _insecure_secrets_to_go_dict(
        insecure_secrets: Dict[str, InsecureSecretsInfo]) -> Dict[str, Any]:
    return _sorted_dict({
        name: _go_dict(
            SecretName=info.SecretName,
            SecretData=_sorted_dict(info.SecretData),
        )
        for name, info in insecure_secrets.items()
    })


def _writable_to_go_dict(writable: WritableInfo) -> Dict[str, Any]:
    return _go_dict(
        InsecureSecrets=_insecure_secrets_to_go_dict(writable.InsecureSecrets),
        LogLevel=writable.LogLevel,
        Reading=_reading_to_go_dict(writable.Reading),
        Telemetry=_telemetry_to_go_dict(writable.Telemetry),
    )


# Wire the sub-struct serializers into their dataclasses so ``to_go_dict`` composes
# cleanly (defined after the dataclasses to keep the file readable).
ClientInfo.to_go_dict = _client_to_go_dict
DeviceInfo.to_go_dict = _device_to_go_dict
MessageBusInfo.to_go_dict = _message_bus_to_go_dict
RegistryInfo.to_go_dict = _registry_to_go_dict
ServiceInfo.to_go_dict = _service_to_go_dict
WritableInfo.to_go_dict = _writable_to_go_dict


def _strip_comments(text: str) -> str:
    """Go-style YAML uses ``#`` only for comments; keep as a no-op helper."""
    return text


# Backwards compatible alias for early adopters of the config model.
Configuration = ConfigurationStruct
