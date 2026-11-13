# 06 接入王凯 set_fan_field 工具

本文记录把王凯在 `wind_tunnel_agent` 里开发并完成虚拟验证（阶段 1.5）的
`set_fan_field` 风扇工具，接入本平台（`labagent`）的改动与验证方法。

原则：**不引入独立包、不做 import 调包**，而是把王凯验证过的逻辑（原语 schema、
展开算法、档位换算、帧打包）逐层改写进本平台已有的 `models.py / specs.py /
tools/devices.py` 里，保持本平台的分层与命名约定。

---

## 1. 背景与目标

- 王凯侧（`../wind_tunnel_agent/`）：已用 LangChain `@tool` 完成
  `set_fan_field` 的虚拟测试，底层库是 `devices/fan_specs.py`（原语 schema）与
  `devices/fan_controller.py`（展开 + 档位 + 770 字节帧 + 串口），dry-run 时不发串口。
- 本平台（`labagent/`）：已有同名 `set_fan_field` 工具（`agent.py` 的 `TOOL_MODELS`），
  但执行体是**模拟器** `SimulatedDevices`，且 `models.py` 里有一套“临时兼容”的原语模型，
  `specs.py` 里有一套展开算法——与王凯的版本在 gaussian、profile、上限、raw 等处不一致。

本次目标：把王凯验证过的**语义**填进本平台的对应层，使

1. 本平台的 `set_fan_field` 工具按王凯的语义校验与展开；
2. 假设备（`SimulatedDevices`）与真链路（新增 `WindTunnelDevices`）对同一 spec
   算出**同一个风场**；
3. 真链路在 dry-run 下能产出 770 字节帧并落进操作日志，证明“确实调用了我们的逻辑”。

---

## 2. 架构对应（本平台四步 ↔ 王凯的文件）

本平台把“控制一次设备”切成四步，每步一个文件；王凯的代码按这四步对应填入：

| 步骤 | 本平台文件 | 干的活 | 对应王凯的 |
|---|---|---|---|
| 想 | `labagent/agent.py` + `runner.py` | 决定调哪个工具、给什么参数 | `wind_tunnel_agent/agent.py`（LLM 节点） |
| 查 | `labagent/models.py` | 校验原语参数格式 | `devices/fan_specs.py` |
| 算 | `labagent/specs.py` | 原语展开成 24×32 风速矩阵 | `devices/fan_controller.py` 的 `_expand` |
| 做 | `labagent/tools/devices.py` | 假执行 / 真下发 | `devices/fan_controller.py` 的档位换算 + `_pack` + 串口 |

王凯的 `tools.py`（`@tool` 壳）不迁移——本平台已有同名工具的壳。

---

## 3. 逐文件修改清单

### 3.1 `labagent/models.py`（查：原语 schema）

- 新增硬件事实常量：`MAX_WIND_SPEED_MPS=20.0`（253 档 ↔ 20 m/s，真机按此线性标定）、
  `RAW_MAX=253`、`FAN_ROWS=24`、`FAN_COLS=32`、`FAN_ROW_REF_HEIGHT_M=1.0`
  （顶部行 row0 参考高度，真机实测 row0=物理顶部），以及
  `Speed = Annotated[float, Field(ge=0, le=MAX_WIND_SPEED_MPS)]`。
- 原语类全部对齐王凯 `fan_specs.py` 的语义：
  - `Uniform/Gradient/Rect/Row/Column` 的风速字段由 `float(ge=0)` 改为 `Speed`（≤20）。
  - `Gradient` 保持 `start/end` 字段 + `from/to` 别名（JSON 字段仍为 `from`/`to`）。
  - `Rect` 增加端点顺序校验（`x0≤x1` 且 `y0≤y1`）。
  - `Gaussian` 的 `cx/cy` 由 `float` 改为 `int`，`amplitude/baseline` 用 `Speed`，
    并在注释里标明**叠加语义**（而非覆盖）。
  - `Profile` 删除 `heights_m` 字段，改为：`power` 律只带 `alpha/z_ref/v_ref`（高度从设备
    行高映射取）；`custom` 律带 `values`。并加 `model_validator` 检查两种 law 的字段组合。
  - 新增 `Raw` 原语（24×32 档位矩阵，0~253）。
  - `Primitive` 联合类型加入 `Raw`。
- `ExperimentSpec.max_speed_mps` 默认值与上限统一为 `MAX_WIND_SPEED_MPS = 20`（253 档 = 20 m/s 是硬件上限）。

### 3.2 `labagent/specs.py`（算：展开算法）

- 新增 `_heights(size)`：沿轴等间距占位高度（`0 → FAN_ROW_REF_HEIGHT_M`），供幂律剖面用。
- `expand_field` 对齐王凯 `FanController._expand`：
  - `raw` 原语在风速展开阶段**跳过**（它只作用于档位层，由设备层处理）；
  - `gaussian` 由“全场覆盖”改为**叠加**（`field[y][x] += baseline + amplitude·exp(…)`），
    并在叠加后检查不超过 `max_speed_mps`；
  - `profile` 幂律的高度改为调用 `_heights(size)`，**不再要求原语带 `heights_m`**；
    y 轴真机实测方向相反（row0=物理顶部=高风、row23=底部），故 `axis=="y"` 时高度序列反转；
  - 其余原语（uniform/gradient/rect/row/column）与王凯一致，保持不变。

### 3.3 `labagent/tools/devices.py`（做：真链路）

- 顶部加载项目根目录 `.env`（读 `FAN_DRY_RUN`、`FAN_SERIAL_PORT`）。
- 新增模块级 `FAN_DRY_RUN / FAN_SERIAL_PORT / FAN_BAUD` 与 `speed_to_level(speed)`。
- 新增 `WindTunnelDevices(SimulatedDevices)`，忠实移植 `FanController` 的完整下发链路
  （协议对齐 `WT_1_NEW/6X8_V2_1frame.py` 上位机脚本与 `.ino` 固件）：
  - `connect()`：真机开串口后 `sleep(BOOT_SETTLE_S=4.0)` 等 Arduino 复位+bootloader+setup
    （Mac 开串口拉 DTR 复位，原 Windows 脚本的 1.5 不够）；另有 `close()` / `list_ports()`。
  - `activate()` / `_ensure_activated()`：预热玄学——连发 2 帧全 0（Mac 实测 1 帧不够，
    真指令须落在第 3 帧）、每帧后 `sleep(ACTIVATE_SLEEP_S=1.0)`、置 `_activated`；
    真机首次下发前自动执行（对齐 `control_activate`）。
  - `set_fan_field(spec, operation_id)`：真机首次自动 `connect()` → `_ensure_activated()` →
    `expand_field`（与模拟器同一展开）→ `speed_to_level` 换算档位 → `_apply_raw` 覆盖档位 →
    `_send_levels` 打包 770 字节帧（`0xFE + 768 + 0xFF`，真机下发前校验 payload==768）。
    返回 `{completed, simulated, operation_id, n_layers, layers, speed_min, speed_max,
    level_mean, level_max, frame_bytes}`（`_summarize` / `_describe` 对齐）。
  - `move_probe / measure_hotwire` 沿用模拟器（尚未接真机）；`stop` 返回 dry-run 友好的文案。
  - `simulated = FAN_DRY_RUN`：dry-run 视为模拟设备（不触发“真实设备须声明 device_id”的护栏），
    真机（`FAN_DRY_RUN=0`）视为真实设备，走护栏 + 串口。

### 3.4 `labagent/agent.py`（想：工具描述）

- `TOOL_DESCRIPTIONS["set_fan_field"]` 改为与王凯 docstring 一致的描述，列出全部原语
  及 gaussian 叠加语义，删去王凯 docstring 里 `calibrate_fan_field` 的引用（本平台无此工具）。

### 3.5 `.env` / `.env.example`

- 追加 `FAN_SERIAL_PORT`（占位 `COM16`）与 `FAN_DRY_RUN=1`。

### 3.6 新增文件

- `run_wind_tunnel.py`：用 `WindTunnelDevices` 启动工作台的入口（对应原 03 文档的
  “创建实机服务”示例）。
- `real_check.py`：真机通道验证脚本（a0 预热 / a1~a6 raw 通道 / p1~p3 全链路原语），
  发完帧后 `sleep(3)` 再 close，避免关串口复位把风扇打回全 0。
- `tests/test_wind_tunnel_integration.py`：接入后的单元与集成验证。

---

## 4. 语义对齐决策点（原两处冲突的裁决）

| 决策点 | 王凯原实现 | 本平台原实现 | 本次裁决 |
|---|---|---|---|
| gaussian | 叠加（`+=`） | 全场覆盖 | 采用**叠加**，叠加后不得超上限 |
| profile.power 高度 | 从设备行高映射取 | 要求原语带 `heights_m` | 采用**行高映射**，删 `heights_m` |
| raw 原语 | 有（档位逃生门） | 无 | 补上，且只在档位层生效、跳过风速展开 |
| 风速上限 | 硬性 10 m/s | 默认 20 m/s（可配） | 最终定 **20 m/s**（253 档 = 20 m/s，真机按此线性标定） |
| 帧长度 | 固定 768 字节 | 无 | 真机下发前校验 payload 必须 768 字节 |

注意：gaussian / profile 幂律若算出风速 >20，现在会在展开阶段**报错**（而不是王凯原
`FanController` 里档位换算时的静默截断）。这是有意为之：与硬件上限一致、且假设备与真链路
对同一 spec 判定相同。

### 4.1 上位机协议核对（对照最原始脚本 `WT_1_NEW/6X8_V2_1frame.py`）

逐项与最原始上位机脚本及其 Arduino 固件 `6X8_V2_1frame.ino` 核对，结论：本平台真链路
与其完全一致，无需返工。

| 项 | 最原始脚本 / 固件 | 本平台 `WindTunnelDevices` |
|---|---|---|
| 帧格式 | `0xFE + 768 + 0xFF`（770 字节） | 同；真机下发前校验 payload==768 |
| 矩阵布局 | 24×32，行优先 `ravel('C')`；固件 `row=i/32, col=i%32` | 同（list-of-lists 逐行 extend） |
| 单字节范围 | `np.clip(0, 253)` | `RAW_MAX=253`（254/255 被帧头帧尾占用；固件注释“可用 254”是理论值，会撞帧头，不可用） |
| 波特率 / 等待 | 115200；开串口后 `sleep(1.5)` | 同，但 Mac 真机改 `BOOT_SETTLE_S=4.0`（开串口拉 DTR 复位，需等完 bootloader+setup） |
| 预热玄学 | `control_activate()`：发全 0 帧 + `sleep(1)` | 同，但连发 2 帧全 0（Mac 实测 1 帧不够，真指令须落第 3 帧） |
| PWM 换算 | 字节值 ×16 → 12 位 PWM（>4095 截断，在固件侧） | Python 只发字节值，不换算（与脚本一致） |

文件名里的 `6X8` 指 6×8 个 PCA9685 模块（每块驱动 4×4=16 风扇），不是风扇矩阵尺寸；
24×32=768 是风扇总数，两者不冲突。

---

## 5. 改造后的数据流与类型

```
① reason 决策（baseline 或 DeepSeek）
   ToolDecision.arguments: dict                  {"spec": [...]}
② act 校验归一化
   FanArgs.model_validate → model_dump(by_alias=True) → list[dict]
③ 操作日志（store.operate）：pending → 执行 → completed
④ WindTunnelDevices.set_fan_field(spec: list[dict], operation_id: str)
     ├─ expand_field(spec, config)              → list[list[float]]   # 24×32 m/s
     ├─ speed_to_level(v)                       → int                 # 档位 0~253
     ├─ _apply_raw(spec, levels)                → list[list[int]]
     ├─ _pack(levels)                           → bytes               # 770 字节帧
     └─ (dry_run 则不写串口，帧存 last_frame)
   返回 {"completed": True, "simulated": …, "frame_bytes": 770, "level_mean": …, "level_max": …}
⑤ 回写 checkpoint（全 JSON 安全的 dict）
   current_spec / phase / iteration / history …
```

关键点：LLM 边界与 checkpoint 全程是 JSON dict；pydantic 校验只在 reason/act 门口；
档位、字节流只存在于 `WindTunnelDevices` 内部。

---

## 6. 如何测试

### 6.1 自动测试

```bash
# 在 motor_command_agent 目录，用 wind-tunnel conda 环境
/Users/你的路径/miniconda3/envs/wind-tunnel/bin/python3 -m unittest discover -s tests -v
```

新增 `tests/test_wind_tunnel_integration.py` 覆盖：档位换算、gaussian 叠加、
profile 高度映射、raw 跳过风速展开、770 字节帧、完整实验收敛并落操作日志。

### 6.2 手动（网页工作台）

```bash
/Users/你的路径/miniconda3/envs/wind-tunnel/bin/python3 run_wind_tunnel.py
# 浏览器打开 http://127.0.0.1:8765
```

默认四测点左高右低；用“离线反馈基线”启动实验，观察：
- “执行记录”里 `set_fan_field` 的返回包含 `frame_bytes: 770`、`level_mean/level_max`；
- “执行记录”里可展开 `spec` 原语列表。

DeepSeek 模式（`.env` 已配 key）下，模型按 `TOOL_DESCRIPTIONS` + 我们的 schema 调用
`set_fan_field`，参数经我们的原语校验后再进 `WindTunnelDevices`。

### 6.3 如何确认“真的调用了我们的工具”

- 操作日志 `operations` 表：`tool="set_fan_field"` 的 `result.frame_bytes == 770`，
  且 `result.simulated == True`（dry-run）。
- `WindTunnelDevices.last_frame` 是完整 770 字节帧（`0xFE ... 0xFF`），可断点/打印核对。
- 事件流 `store.events`：`tool_started` / `tool_completed` 各一条，`arguments` 是原语列表。

### 6.4 真机与 deepseek 验证进展

- 真机 `set_fan_field`：`real_check.py`（a0~a6 / p1~p3）已驱动风扇（任务 1 完成）。
- deepseek 模式：已实测能仅凭 `description` 自然语言把目标风场转成多原语 spec（
  `set_fan_field` 的 `arguments.spec` 由模型自己编，非抄写死 `initial_field`）；
  baseline 模式无 LLM，spec 走写死 `initial_field`。

---

## 7. 已知限制与后续待办

- 真机 `set_fan_field` 已实测通过（任务 1：`real_check.py` a0 预热 + a1~a6 raw 通道 +
  p1~p3 全链路，风扇正常转）。关键坑：**开/关串口都会拉 DTR 复位 Arduino**——发完真指令
  须留时间再 close（`real_check.py` 用 `sleep(3)`）；labagent 整链路由 `WindTunnelDevices`
  全程串口常开，无此坑。真机需显式声明 `device_id`（非 `simulator`）与
  `calibration_version`（非 `simulation-v1`），否则 runner 护栏报错。
- `move_probe / measure_hotwire / set_flap / stop` 仍为模拟器：`WindTunnelDevices` 未重写
  `measure_hotwire`（继承 `SimulatedDevices` 的假测量，是当前风场的确定性函数，非随机）。
  即真机闭环里“测量”仍是假的——任务 2 上真机前必须先接入真热线/真探针。
- 行高映射 y 轴方向已按真机（row0=物理顶部）反转，但 `_heights` 仍是等间距占位，
  需按真实安装高度标定后替换。
- `raw` 原语不参与 `expand_field` 的单轮变化检查（`max_change_mps`），若模型直接给
  大幅变化的 raw 矩阵，当前不会触发限幅告警——真机前需补上。
- 档位 ↔ m/s 为线性近似（253 档 = 20 m/s），真实标定曲线待风速计实测后替换
  `speed_to_level`。
- 目标只有离散测点（`points[].target_mps`），无“目标风场”概念：做不了“逼近一个完整风场”，
  只能“逼近几个点”。也缺“自然语言→spec 翻译层”（`description` 会被塞进 LLM 上下文，但
  不是目标来源；目标永远是 `points`）。

---

## 8. 改动文件清单

| 文件 | 改动 |
|---|---|
| `labagent/models.py` | 加常量 + 原语对齐 + max_speed 收紧 |
| `labagent/specs.py` | `expand_field` 对齐（gaussian 叠加 / profile 高度 / 跳过 raw） |
| `labagent/tools/devices.py` | 加载 .env + `speed_to_level` + `WindTunnelDevices` |
| `labagent/agent.py` | `set_fan_field` 工具描述 |
| `.env` / `.env.example` | 追加风扇配置 |
| `run_wind_tunnel.py` | 新增启动入口 |
| `real_check.py` | 新增真机通道验证脚本 |
| `tests/test_wind_tunnel_integration.py` | 新增集成测试 |
