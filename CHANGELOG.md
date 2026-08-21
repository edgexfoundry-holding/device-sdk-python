# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [4.0.1] - 2026-08-21

### Added
- **Core Keeper registry support**: EdgeX v4 replaces Consul with core-keeper; `Registry.Type` now defaults to `core-keeper`
- **TLS/mTLS certificate management**: `TLSManager` with hot-reload, certificate expiry monitoring, mTLS contexts, and self-signed certificate generation (`internal/clients/tls.py`); `cryptography` is an optional lazy dependency
- **SecretStore integration**: `SecretProvider` abstraction with `InMemorySecretProvider` (insecure mode) and `OpenBaoSecretProvider` (secure mode, KV v2, token renewal)
- **JWT token auto-refresh**: `JWTAuthenticator` with JWKS support, proactive refresh threshold, and retry on expiry
- **Core Data client**: event/reading submission with batch queue, retry, and circuit breaker
- **Core Command client**: command dispatch with retry and circuit breaker
- **Unified system events callback layer**: all `_on_*` handlers delegate to `application/callback.py`
- **Device return retry loop**: `AllowedFails`/`DeviceDownTimeout` handling (`application/devicereturn.py`)
- **Profile scan application layer** (`application/profilescan.py`)
- **Configuration model aligned with Go**: `ConfigurationStruct` with Go-compatible `to_go_dict()` serialization; `/api/v3/config` output matches the Go SDK byte-for-byte
- **AutoEventManager interface** (`interfaces/manager.py`)

### Changed
- **Default service port changed from 59986 to 59990** to avoid conflict with device-rest
- `provision.py` split into the `internal/provision/` package (common, devices, profiles, provisionwatchers)
- Busy-wait in the system events loop replaced with a blocking `queue.get(timeout=0.1)`
- Optional dependencies (`cryptography`, `pyOpenSSL`) are now lazily imported so the SDK core stays zero-dependency

## [4.0.0] - 2026-08-06

### Added
- **REST API (EdgeX v3)**: Complete implementation of `/api/v3/ping`, `version`, `config`, `metrics`, `device/name/{name}/{command}` (GET/PUT), `discovery`, `profilescan` with full query parameter support (`ds-pushevent`, `ds-returnevent`, `ds-regexcommand`)
- **MessageBus Event Publishing**: Events published to `edgex/events/device/<svc>/<profile>/<device>/<source>` with JSON/CBOR encoding, MaxEventSize enforcement, and MessageEnvelope (ContentType, Correlation-Id)
- **Command Subscription**: MQTT subscription to `edgex/command/request/<svc>/#` with semaphore-limited concurrency (default 32), response on `edgex/response/<svc>/<requestId>`, supports `ds-pushevent`/`ds-returnevent`/`ds-regexcommand`
- **Metadata System Events Callback**: Subscriptions to `edgex/system-events/<svc>/#`, `device-profile/delete/#`, provision-watcher topics; dispatches Device/Profile/Watcher/Service CRUD to cache
- **Async Readings Pump**: Background consumer for `AsyncValues` channel → transform → publish
- **Discovered Devices Pump**: Background consumer for discovered devices → ProvisionWatcher allow/block list matching → local cache registration + system event
- **Data Transformations**: Mask → Shift → Base → Scale → Offset (read path), inverse (write path), with overflow→"overflow" and NaN→"NaN" handling
- **Assertions & Mappings**: Per ADR 0011; assertion failure sets OperatingState=DISABLED; ResourceOperation mappings applied (read: final, write: reverse)
- **CBOR Encoding**: Automatic `application/cbor` for binary readings in HTTP responses and MessageBus events
- **Configuration**: Full EdgeX v4 `MessageBus` section (Type, BaseTopicPrefix, AuthMode, Optional.*), YAML + environment variable overrides
- **Device System Events SDK API**: `PublishDeviceDiscoveryProgressSystemEvent`, `PublishProfileScanProgressSystemEvent`, `PublishGenericSystemEvent`
- **Validation Subscription**: `edgex/<svc>/validate/device` (ported from Go)
- **Examples**: `device-simple` with synthetic driver, profiles/devices/watchers YAML resources
- **Tests**: unit tests covering models, cache, transformer, autoevent, HTTP endpoints, bootstrap

### Changed
- Architecture strictly mirrors `device-sdk-go` v4.1.0-dev (module `device-sdk-go/v4`)
- Package structure: `src/device_sdk_py/` with `interfaces`, `models`, `internal`, `service`

### Security
- Non-root Docker user (`edgex:edgex`)
- TLS configuration support in MessageBus (SkipCertVerify, CertFile, KeyFile, CAFile, PEM blocks)
- Auth modes: none, usernamepassword, clientcert, cacert
