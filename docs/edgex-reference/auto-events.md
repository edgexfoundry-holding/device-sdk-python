# Device Service - Auto Events

## Concept
AutoEvents automatically and periodically check sensor status and send events/readings to MessageBus. When device service receives device create/update/delete from core metadata, it gets readings from real device automatically.

## AutoEvent Fields
- **sourceName**: deviceResource or deviceCommand name to read
- **interval**: time between readings (integer + ms/s/m/h)
- **onChange**: boolean - only generate events if readings changed since last event
- **onChangeThreshold**: float64 - threshold for numeric readings when onChange=true

### Example Device Definition with AutoEvents
```yaml
device:
  name: device-demo
  adminState: UNLOCKED
  operatingState: UP
  serviceName: device-virtual
  profileName: virtual-profile
  protocols: virtual
  autoEvents:
    - interval: 10s
      onChange: false
      sourceName: Bool
```

## Query Events
```bash
curl http://localhost:59880/api/v3/event/device/name/device-demo
```

Source: https://docs.edgexfoundry.org/4.0.2/microservices/device/details/AutoEvents/