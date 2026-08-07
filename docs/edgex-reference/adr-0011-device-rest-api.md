# ADR 0011 - Device Service REST API

**Status: Approved**

## Context
REST API for Device Services in EdgeX v2.x. Supersedes earlier "Device Service Functional Requirements". Implemented in Device Service SDKs.

## Decision

### Common Endpoints
- `config`, `metrics`, `ping`, `version`

### Callback Endpoints (for Core Metadata updates)
| Endpoint | Methods |
|----------|---------|
| `callback/device` | `PUT`, `POST` |
| `callback/device/name/{name}` | `DELETE` |
| `callback/profile` | `PUT` |
| `callback/watcher` | `PUT`, `POST` |
| `callback/watcher/name/{name}` | `DELETE` |

**Object Deletion**: `DELETE` to `callback/{type}/name/{name}`
**Object Creation/Update**: `POST`/`PUT` to `callback/{type}` with DTO payload

### Device Command Endpoint
`GET/PUT /device/name/{name}/{command}`

| Return Code | Meaning |
|-------------|---------|
| 200 | Success |
| 404 | Device doesn't exist or command unknown |
| 405 | Write to read-only resource |
| 423 | Device locked (admin) or disabled (operating) |
| 500 | Driver unable to process |

**Response Body (GET)**: JSON EventResponse with readings

### Data Formats
| Type | EdgeX Types | Representation |
|------|-------------|----------------|
| Boolean | Bool | "true"/"false" |
| Integer | Uint8-64, Int8-64 | Numeric string |
| Float | Float32, Float64 | Decimal with exponent |
| String | String | string |
| Binary | Bytes | octet array |
| Array | *Array types | JSON Array |

**NOTE**: Binary reading → entire Event encoded as CBOR

### Readings and Events
**Reading fields**: deviceName, profileName, resourceName, origin, value, valueType (or binaryValue + mediaType)
**Event fields**: deviceName, profileName, origin, readings[]

### Query Parameters (ds- prefix reserved)
| Parameter | Values | Default | Meaning |
|-----------|--------|---------|---------|
| `ds-pushevent` | true/false | false | Push event to EdgeX system |
| `ds-returnevent` | true/false | true | Return Event in HTTP response |

### Device States
- **Admin State**: `LOCKED` (default `UNLOCKED`) - blocks access, returns 423
- **Operating State**: `DOWN` (default `UP`) - device not working, returns 423

### Data Transformations (order for outgoing/reversed for incoming)
1. **mask** (Integers) - bitwise AND
2. **shift** (Integers) - bit shift (positive=right, negative=left)
3. **base** (Integers/Floats) - base^reading
4. **scale** (Integers/Floats) - multiply
5. **offset** (Integers/Floats) - add

**Overflow handling**: set value to "overflow", valueType to String

### Assertions and Mappings
- **Assertions**: on failure → 500 + "Assertion failed for device resource: ..., with value: ...", device OperatingState set to DISABLED, ignores `ds-returnevent`
- **Mappings**: DeviceCommand mappings, applied last for reads, reversed for writes

### lastConnected Timestamp
Updated on every successful GET/PUT command

### Discovery Endpoints
- `POST /discovery`
- `POST /profilescan`
- DELETE variants

Source: https://docs.edgexfoundry.org/4.0.2/design/adr/device-service/0011-DeviceService-Rest-API/