# device-sdk-python — 架构设计与开发任务书

目标：实现一个功能上与 `edgexfoundry/device-sdk-go` 完全等价的独立并行 SDK（EdgeX Device Service SDK for Python），
可复用 `edgexfoundry/app-functions-sdk-python` 的 client / bootstrap / configuration / contracts 模块。
`device-sdk-go` 仅作为功能参照，非移植对象。

## 参照项目
- Go 参照：`../device-sdk-go`（功能对照清单）
- Python 参考：`../app-functions-sdk-python`（复用代码库）

## Python 版 ProtocolDriver（用户实现，对应 interfaces/protocoldriver.go）
抽象基类 `ProtocolDriver`，方法：
- `initialize(sdk) -> None`
- `handle_read_commands(device_name, protocols, reqs: list[CommandRequest]) -> list[CommandValue]`
- `handle_write_commands(device_name, protocols, reqs, params) -> None`
- `start() / stop(force)`
- `add_device / update_device / remove_device(device_name, protocols, admin_state)`
- `discover() -> None`
- `validate_device(device) -> None`

## 数据模型（对应 pkg/models）
- `CommandValue`（resource_name, value_type, value, origin, tags），提供一整套类型化取值方法
- `CommandRequest`（resource_name, type, attributes, ...）
- `AsyncValues`（异步上报：device_name, source_name, command_values）
- `DiscoveredDevice`
- `Notify`

## SDK 主类（对应 pkg/service + interfaces/service.go）
`DeviceService` 实现：
- 设备/Profile/Watcher 增删改查（**复用 app-functions-sdk-python 的 clients**：device, deviceprofile, deviceservice）
- 内存缓存（devices/profiles/provisionwatchers）
- AutoEvent 定时采集（异步发送 AsyncValues）
- HTTP REST 服务（**类似 internal/controller/http**）：`/api/v3/device/{name}/{cmd}`
  GET（读）/PUT（写）、`/api/v3/discovery`、`/api/v3/ping`、`/api/v3/version`、`/api/v3/metrics`
- 消息总线指令（MQTT/Redis/NATS，**复用 app-functions-sdk-python 的 messaging**）
- transformer：读结果 → Event/Reading（复用 commandvalue 映射逻辑）
- 配置加载（**复用 app-functions-sdk-python bootstrap/config**）+ 自定义 config
- 日志（**复用 logging client**）+ 密钥（**复用 secret**）+ 指标（**复用 metrics**）

## 目录结构
```
device-sdk-python/
├── pyproject.toml / setup.py / requirements.txt
├── src/device_sdk_py/
│   ├── __init__.py            # 版本号
│   ├── interfaces/
│   │   ├── protocoldriver.py  # ProtocolDriver 抽象基类
│   │   └── service.py         # DeviceServiceSDK 接口
│   ├── models/                # CommandValue, CommandRequest, AsyncValues, DiscoveredDevice, Notify
│   ├── internal/
│   │   ├── cache/             # devices, profiles, provisionwatchers
│   │   ├── transformer/       # commandvalues -> Event/Reading
│   │   ├── autoevent/         # executor + manager
│   │   ├── controller/http/   # REST 路由
│   │   ├── controller/messaging/
│   │   ├── common/            # 常量、工具
│   │   └── config/
│   ├── bootstrap/             # 启动引导 Bootstrap()
│   └── service/               # 主服务实现
└── example/device-simple/     # 示例：SimpleDriver 对应
```

## 实施步骤（opencode 施工清单）
1. 搭骨架：pyproject、包结构、requirements（复用 app-functions-sdk-python 依赖：pyyaml, requests 等）
2. models：CommandValue 及全类型化 getter
3. interfaces：ProtocolDriver 抽象基类 + DeviceServiceSDK
4. internal/cache：三类缓存
5. internal/transformer：读取->Event/Reading
6. internal/autoevent：定时采集
7. internal/controller/http：REST 端点
8. bootstrap：Bootstrap() 启动引导，复用 app-functions-sdk-python 的 di/config/logging/secret/metrics
9. service：DeviceService 主类装配以上
10. example/device-simple：SimpleDriver 示例 + configuration.yaml + 设备/Profile yaml
11. tests：单测

关键实现约束：
- 严格对照 `device-sdk-go/pkg` 与 `internal` 的接口签名和语义
- 复用 `app-functions-sdk-python/src/app_functions_sdk_py` 的 contracts/clients、bootstrap、configuration、messaging、registry
- Python 3.10+，走 src layout，可 pip 安装