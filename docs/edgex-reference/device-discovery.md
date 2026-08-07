# Device Service - Device Discovery and Provision Watchers

## Static Provisioning
Device service is provided with a Device file containing device definitions to statically provision. The device service connects and establishes new devices it manages in EdgeX from device definition configuration. The devices and connection info is known at startup.

## Dynamic Provisioning (Device Discovery)
Device service is given general info about where to look (e.g., network address range). It continually scans on a schedule for new devices within the guides of location and device parameters provided by configuration.

### Provision Watcher
A filter applied to new devices found during scanning. Created via Core Metadata provision watcher API.
- Contains ProtocolProperty names and values (may be regex)
- May contain "blocking" identifiers (non-regex matching) to avoid specific devices
- Multiple Provision Watchers can be provided; devices added if they match any one
- Includes specification of Profile name, initial AdminState, and optionally AutoEvents

## Admin State
- `LOCKED` or `UNLOCKED`
- When `LOCKED`, requests return HTTP 423

## Sensor Reading Schedule
Auto events determine when device service collects data from devices and publishes to MessageBus.

Source: https://docs.edgexfoundry.org/4.0.2/microservices/device/details/DeviceDiscovery/