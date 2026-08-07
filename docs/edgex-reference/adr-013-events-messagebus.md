# ADR 013 - Device Services Send Events via Message Bus

**Status: Approved**

## Context
Currently Events sent via HTTP to Core Data → MessageBus. This ADR details direct Device Service → MessageBus publishing.

## Decision

### Message Bus Implementations
- **ZMQ**: Single publisher only - valid if only 1 Device Service
- **MQTT** (default) / **Redis Streams**: Multiple publishers - valid for multiple Device Services
- Go SDK uses `go-mod-messaging`, C SDK implements own abstraction

### Go Device SDK
- New bootstrap handler initializes MessageBus client from config
- Optionally publish Events to MessageBus instead of POST to Core Data
- Controlled by config, **publish is default**

### Core Data & Persistence
- Core Data becomes optional subscriber to MessageBus Events
- Retains HTTP POST ability for transition period
- `PersistData` ignored for MessageBus path
- **Marked As Pushed removed** - rely on time-based scrubbing (race condition)

### V2 Event DTO
- All Events use V2 Event DTO (already in Core Data V2 AddEvent API)
- Receiving services validate and stop on error

### Message Envelope
- Contains: `ContentType` (JSON/CBOR), `Correlation-Id`
- **Checksum removed** (was for V1 CBOR marking)
- C SDK recreates this envelope

### MessageBus Topics
**Publish Topic**: `edgex/events/<device-profile-name>/<device-name>/<source-name>`
- `sourceName` = Resource or Command name
- Allows Application Services to filter subscriptions

**Subscribe Examples**:
- `edgex/events/#` - All (Core Data)
- `edgex/events/Random-Integer-Device/#` - By profile
- `edgex/events/Random-Integer-Device/Random-Integer-Device1` - By device
- `edgex/events/Random-Integer-Device/#/Int16` - By resource
- `edgex/events/Modbus-Device/#/HVACValues` - By command

### Configuration (v4: MessageQueue → MessageBus)

**Device Services** (`MessageBus:` section):
| Property | Description |
|----------|-------------|
| Type | mqtt/nats-core/nats-jetstream |
| Host/Port/Protocol | Broker connection |
| PublishTopicPrefix | Default "events" |
| Optional: ClientId, Qos, KeepAlive, Retained, AutoReconnect, ConnectTimeout, SkipCertVerify, ClientAuth, SecretPath |

**Core Data**: Subscribes to `edgex/events/#`

**Application Services**: Subscribe to filtered topics

### Secure Connections
- Secret Provider for All provides secrets
- TLS/certificates via SecretStore

### Consequences
- Events no longer go through Core Data by default
- Application Services receive V2 Event DTO directly
- Topic-based filtering reduces load
- Transition period with both HTTP POST and MessageBus publish

Source: https://docs.edgexfoundry.org/4.0.2/design/adr/013-Device-Service-Events-Message-Bus/