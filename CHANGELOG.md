# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- **Tests**: 22 unit tests covering models, cache, transformer, autoevent, HTTP endpoints, bootstrap

### Changed
- Architecture strictly mirrors `device-sdk-go` v4.1.0-dev (module `device-sdk-go/v4`)
- Copyright headers updated to `YIQISOFT 2026`
- Package structure: `src/device_sdk_py/` with `interfaces`, `models`, `internal`, `service`

### Security
- Non-root Docker user (`edgex:edgex`)
- TLS configuration support in MessageBus (SkipCertVerify, CertFile, KeyFile, CAFile, PEM blocks)
- Auth modes: none, usernamepassword, clientcert, cacert

## [Unreleased]

### Planned
- NATS Core message bus implementation
- Secure mode (mTLS, token auth) integration with Core Keeper
- Core Metadata client implementation (replace placeholder)
- Name field escaping (RFC3986) for topic construction
- Prometheus metrics exposition (`/api/v3/metrics` full implementation)
- Secret provider integration
- Performance benchmarks and load testing