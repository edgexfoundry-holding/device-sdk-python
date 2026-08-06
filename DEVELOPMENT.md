# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

# 开发手册

本文档面向 `device-sdk-python` 的维护与扩展者，记录架构约束、编码规范、测试策略、发布流程。

---

## 1. 架构约束（不可违背）

1. **100% 照抄 device-sdk-go v4**  
   - 目录结构、模块名、函数签名、常量名、错误码、消息总线 topic 格式，必须与 Go 参考实现完全一致。  
   - 仅在 Python 语言特性（类型注解、dataclass、async/await、异常代替多返回值）上做最小适配。

2. **app-functions-sdk-python 仅作工具库**  
   - 允许复用：`messaging/mqtt/client.py` 抽象、`contracts` 常量、`bootstrap/di` 容器概念。  
   - 禁止：引入其业务逻辑、DTO、pipeline、trigger 等核心流程代码。

3. **版权头统一**  
   ```python
   # Copyright (C) 2026 YIQISOFT
   # SPDX-License-Identifier: Apache-2.0
   ```
   严禁出现 `IOTech`、`Canonical` 等原版权信息。

4. **配置驱动，零硬编码**  
   - 端口、host、topic prefix、Qos、KeepAlive 等全部从 `configuration.yaml` / 环境变量读取。  
   - `59986` 仅作为兜底默认值出现在 `_DEFAULT_HTTP_PORT` 常量。

5. **单测优先，网络隔离**  
   - 单测不得依赖外部 MQTT/Metadata/Redis。  
   - `_start_device_validation_handler`、`_start_command_subscription` 等在 client 不可用时静默降级为 log。

---

## 2. 目录与模块职责

| 路径 | 职责 | 对应 Go 模块 |
|------|------|--------------|
| `interfaces/` | ProtocolDriver、DeviceServiceSDK 抽象基类 | `pkg/interfaces/` |
| `models/` | CommandValue、CommandRequest、AsyncValues、DiscoveredDevice | `pkg/models/` |
| `internal/cache/` | Devices/Profiles/ProvisionWatchers 单例缓存 | `internal/cache/` |
| `internal/transformer/` | Mask/Shift/Base/Scale/Offset、assertion、mapping、CBOR | `internal/transformer/` |
| `internal/autoevent/` | 定时采集 executor + manager | `internal/autoevent/` |
| `internal/application/` | command_read/write（核心业务逻辑） | `internal/application/` |
| `internal/controller/http/` | REST 路由：command、discovery、common endpoints | `internal/controller/http/` |
| `internal/controller/messaging/` | MQTT client、event publish、command sub、system events callback | `internal/controller/messaging/` |
| `internal/common/` | 常量、错误码、工具函数 | `internal/common/` |
| `internal/metadata/` | Core Metadata client（占位） | `internal/metadata/` |
| `service/` | DeviceService 装配、Bootstrap 入口 | `pkg/service/`, `service/` |
| `examples/simple/` | 最小可运行示例 | `example/driver/simpledriver.go` |

---

## 3. 编码规范

### 3.1 导入风格
```python
# 标准库
import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# 第三方
import paho.mqtt.client as mqtt
import cbor2

# 本地：使用绝对导入（包名 device_sdk_py）
from device_sdk_py.models import CommandValue
from device_sdk_py.internal.common.consts import API_VERSION
from device_sdk_py.internal.common.utils import EdgexError
```
- 禁止相对导入跨越超过 2 层（如 `from ...models`），改用绝对导入。
- 循环依赖时在 `TYPE_CHECKING` 块中导入。

### 3.2 命名映射表
| Go | Python |
|----|--------|
| `CamelCase` 函数/方法 | `snake_case` |
| `PascalCase` 类型/常量 | `PascalCase`（保持导出兼容） |
| `UPPER_SNAKE_CASE` 常量 | `UPPER_SNAKE_CASE` |
| `errors.EdgeX` | `EdgexError` (exception) |
| 多返回值 `(T, error)` | 正常返回 `T`，异常 `raise EdgexError` |
| `context.Context` | 隐式（通过 correlation_id 传递） |
| `sync.Mutex` | `threading.Lock` / `threading.Semaphore` |
| `chan T` | `queue.Queue[T]` |

### 3.3 错误码映射
| Go `errors.Kind` | Python `EdgexErrorKind` | HTTP Status |
|------------------|-------------------------|-------------|
| `KindContractInvalid` | `KIND_CONTRACT_INVALID` | 400 |
| `KindEntityDoesNotExist` | `KIND_ENTITY_DOES_NOT_EXIST` | 404 |
| `KindNotAllowed` | `KIND_NOT_ALLOWED` | 405 |
| `KindServiceLocked` | `KIND_SERVICE_LOCKED` | 423 |
| `KindServerError` | `KIND_SERVER_ERROR` | 500 |
| `KindNotImplemented` | `KIND_NOT_IMPLEMENTED` | 501 |
| `KindStatusConflict` | `KIND_STATUS_CONFLICT` | 409 |

### 3.4 常量来源
- HTTP 路由、Header、Query 参数、设备状态、权限字符串 → `internal/common/consts.py`（合并 Go `consts.go` + `go-mod-core-contracts/common`）。
- SDK 内部保留前缀 `ds-`、`urlRawQuery`、`CorrelationHeader` 等同理。

### 3.5 文档字符串
每个公开函数/类必须包含：
```python
def foo(bar: str) -> int:
    """Short description.

    Mirrors `GoFunctionName` in go-file.go: 说明对应 Go 源码位置。

    Args:
        bar: 参数说明。

    Returns:
        返回值说明。

    Raises:
        EdgexError: 何时抛出。
    """
```

---

## 4. 消息总线协议细节

### 4.1 Topic 规范（EdgeX v4）
```
Events:      edgex/events/device/<svc>/<profile>/<device>/<source>
Commands:    edgex/command/request/<svc>/<device>/<command>/<get|set>
Responses:   edgex/response/<svc>/<requestId>
SysEvents:   edgex/system-events/<svc>/<type>/<action>
             edgex/system-events/device-profile/delete/#
             edgex/system-events/provision-watcher/<baseSvc>/#
Validation:  edgex/<svc>/validate/device
```
- `<basePrefix>` 默认 `edgex`，可通过 `MessageBus.BaseTopicPrefix` 配置。
- Name field escaping：设备/Profile/命令名需符合 RFC3986（Go 用 `PathBuilder.EnableNameFieldEscape`），Python 目前直接 join，后续补全。

### 4.2 MessageEnvelope (v4)
```json
{
  "apiVersion": "v3",
  "correlationId": "uuid",
  "requestId": "uuid",
  "contentType": "application/json",
  "payload": { ... },          // JSON object 或 base64 编码的 CBOR bytes
  "receivedTopic": "edgex/...",
  "queryParams": { "ds-pushevent": "true" }
}
```
- `Checksum` 字段已移除（v3+）。
- `contentType` 为 `application/cbor` 时，payload 为 base64 编码的 CBOR bytes。

### 4.3 发布流程
1. `command_read/write` → `transformer.command_values_to_event` → `Event`
2. `publish_event()` → `encode_event_request()`（binary reading → CBOR，否则 JSON）
3. `MessageEnvelope` 包装 → `MaxEventSize` 检查 → `client.publish(envelope, topic)`

---

## 5. 核心数据流

### 5.1 同步命令 (REST)
```
GET /api/v3/device/name/{name}/{command}
  → command.ReadController.get_command()
  → filter_query_params(ds-pushevent/ds-returnevent/ds-regexcommand)
  → application.command_read()
     → _validate_service_and_device_state()  // 423 check
     → Profiles().device_command/resource 找到定义
     → driver.handle_read_commands()
     → transformer.command_values_to_event()
        → transform_read_result (Mask→Shift→Base→Scale→Offset)
        → check_assertion (失败 → OperatingState=DISABLED)
        → map_command_value (ResourceOperation mappings)
     → Event
  → ds-pushevent → _send_event_handler() → MessageBus
  → ds-returnevent → EventResponse (JSON/CBOR)
```

### 5.2 异步读数
```
ProtocolDriver → sdk.async_values_channel().put(AsyncValues)
  → DeviceService._pump_async_values() (后台线程)
     → command_values_to_event()
     → _send_event_handler() → MessageBus
```

### 5.3 发现流程
```
POST /api/v3/discovery
  → DiscoveryController.discovery()
  → driver.discover() (后台线程)
     → sdk.discovered_device_channel().put([DiscoveredDevice])
  → DeviceService._pump_discovered_devices()
     → 匹配 ProvisionWatchers (allow/block list)
     → MetadataClient.add_device(bypass_validation=True)  // 占位
     → 本地缓存 Devices().add()
     → 发布 discovery progress system event
```

---

## 6. 测试策略

### 6.1 单测分层
| 层 | 文件 | 覆盖重点 |
|----|------|----------|
| Models | `tests/test_models.py` | CommandValue 类型化 getter/setter、二进制/数组/对象编码 |
| Cache | `tests/test_bootstrap.py` | Devices/Profiles/Watchers CRUD、admin/operating state、lastConnected |
| Transformer | `tests/test_transformer.py` | Mask/Shift/Base/Scale/Offset、overflow→"overflow"、assertion、mapping |
| AutoEvent | `tests/test_autoevent.py` | 调度、并发、停止重启 |
| HTTP | `tests/test_simple_example.py` | ping/version/device GET/PUT、ds-* query params、discovery/profile scan |
| Messaging | (待补充) | client connect/pub/sub、command subscription、system events callback |

### 6.2 运行方式
```bash
# 全部
python -m unittest discover tests -v

# 单文件
python -m unittest tests.test_bootstrap.TestDeviceCRUD -v

# 覆盖率
coverage run -m unittest discover tests
coverage html -d htmlcov
```

### 6.3 Mock 规则
- 外部依赖：MQTT client、Metadata client、Secret provider → 在测试中 patch 为 `MagicMock`。
- 时间：`time.time_ns()` → patch 为固定值。
- 随机 ID：`uuid.uuid4()` → patch 为可预测序列。

---

## 7. 常见扩展点

### 7.1 新增数据变换
1. `transformresult.py` 新增 `transform_xxx()` 函数。
2. `transform_read_result()` 调用链中插入（注意顺序：Mask→Shift→Base→Scale→Offset）。
3. 写路径 `_transform_write_parameter()` 反向调用。
4. 单测覆盖正向/逆向/溢出。

### 7.2 新增 MessageBus 类型 (NATS)
1. `client.py` 新增 `NatsMessageClient` 实现 `MessageClient` ABC。
2. `new_message_client()` 根据 `config.type` 分发。
3. 配置 `Optional` 映射 NATS 特有参数。

### 7.3 新增系统事件类型
1. `consts.py` 新增 `XXX_SYSTEM_EVENT_TYPE`、`SYSTEM_EVENT_ACTION_XXX`。
2. `callback.py` `_handle_xxx_system_event()` 分发函数。
3. `subscribe_system_events()` topic 列表加入新 topic。
4. `DeviceService._on_xxx_*()` 回调实现。

---

## 8. 发布清单

| 步骤 | 命令 |
|------|------|
| 版本号更新 | `src/device_sdk_py/__init__.py` + `pyproject.toml` |
| 单测全绿 | `python -m unittest discover tests -v` |
| 示例可跑 | `cd examples/simple && python -m device_service` (后台) + `curl /api/v3/ping` |
| 文档同步 | README.md、DEVELOPMENT.md、CHANGELOG.md |
| 构建分发 | `pip wheel . -w dist/` |
| 标签 | `git tag v4.x.x && git push --tags` |

---

## 9. 参考资料

- [EdgeX Foundry 官方文档 4.1](https://docs.edgexfoundry.org/4.1/)
- [ADR 0011 - Device Service REST API](https://docs.edgexfoundry.org/4.1/design/adr/device-service/0011-DeviceService-Rest-API/)
- [ADR 013 - Device Services Send Events via Message Bus](https://docs.edgexfoundry.org/4.1/design/adr/device-service/013-Device-Services-Send-Events-via-Message-Bus/)
- [device-sdk-go v4 源码](https://github.com/edgexfoundry/device-sdk-go/tree/v4.1.0-dev)
- [go-mod-core-contracts v4](https://github.com/edgexfoundry/go-mod-core-contracts/tree/v4.1.0-dev.40)
- [app-functions-sdk-python](https://github.com/edgexfoundry/app-functions-sdk-python)

---

## 10. 变更记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-08-06 | 4.0.0 | 初始移植完成：REST、MessageBus、Command Sub、System Events、Async/Discovery Pump、CBOR、Assertion、完整配置 |