# Core Metadata - Device System Events

System Events triggered by add/update/delete of device metadata objects (Device, DeviceProfile, DeviceService, ProvisionWatcher). Published to MessageBus.

## System Event DTO (EdgeX 3.0+)
New event types: `deviceservice`, `deviceprofile`, `provisionwatcher`

| Property | Description | Value |
|----------|-------------|-------|
| Type | Type of System Event | `device`, `deviceservice`, `deviceprofile`, `provisionwatcher` |
| Action | System Event action | `add`, `update`, `delete` |
| Source | Source of event | `core-metadata` |
| Owner | Owner of data | Device service name or `core-metadata` |
| Tags | Additional data | empty |
| Details | Trigger object | Added/updated/deleted Device/Profile/Service/Watcher |
| Timestamp | Event time | nanoseconds |

## Publish Topic
Base: `MessageQueue.PublishTopicPrefix` (default `edgex/system-events`)

Topic parts for filtering:
- `source = core-metadata`
- `type = device|deviceservice|deviceprofile|provisionwatcher`
- `action = add|update|delete`
- `owner = [device service name]`
- `profile = [device profile name]`

### Example Topics
```
edgex/system-events/core-metadata/device/add/device-onvif-camera/onvif-camera
edgex/system-events/core-metadata/device/update/device-rest/sample-numeric
edgex/system-events/core-metadata/device/delete/device-virtual/Random-Boolean-Device
```

## Relevance to Device Service SDK (Gap #6 in DEVLOG.md)
Device Services must **subscribe** to these topics to keep their cache in sync with Core Metadata:
- `edgex/system-events/<svc>/#` - Device/Profile/DeviceService events for this service
- `edgex/system-events/device-profile/delete/#` - Profile delete (special)
- Instance name scenario: `edgex/system-events/provision-watcher/<baseSvc>/#`

### Actions to Handle:
1. **Device**: Add/Update/Delete
2. **DeviceProfile**: Update/Delete
3. **DeviceService**: Update
4. **ProvisionWatcher**: Add/Update/Delete

Source: https://docs.edgexfoundry.org/4.0.2/microservices/core/metadata/details/DeviceSystemEvents/