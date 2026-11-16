# LabAgent 风洞多模态实验工作台

基于 LangGraph、FastAPI 和 React 的单机风洞实验平台，将文字、表格和图片中的风场需求转换为经过校验的原语 JSON，接入风扇设置、测点扫描、分析和反馈调整流程。

## 主要功能

- 多模态输入，支持自然语言、Excel、CSV、TSV 和图片，可同时提交文字与多个附件。
- 风场解析，识别风速矩阵、坐标与目标风速测点表、说明性表格、风场示意图及剖面图。
- 网页预览，展示识别说明、假设提示、原语 JSON 与逐单元风速热图，支持应用到实验配置。
- 实验闭环，提供离线反馈基线和模型 ReAct 决策，记录扫描结果、误差趋势、工具调用和实验产物。
- 模型配置，默认使用支持图片输入的 `deepseek-flash`，可独立配置文本模型和视觉模型。
- 设备适配，默认使用模拟器，提供 `WindTunnelDevices` 风扇原语展开、档位换算和串口帧链路。

## 快速开始

需要 Python 3.11–3.14 和 uv。从项目根目录运行。

```bash
uv sync --extra test
cp .env.example .env
```

在 `.env` 中填写 `DEEPSEEK_API_KEY`，然后启动网页服务。

```bash
uv run python -m labagent
```

打开 [本地工作台](http://127.0.0.1:8765)。现有 `.lab-venv` 用户安装新增依赖后也可直接启动。

```bash
uv pip install --python .lab-venv/bin/python -r pyproject.toml --extra test
.lab-venv/bin/python -m labagent
```

离线反馈基线与标准表格本地解析可在无 API Key 的环境使用。自然语言、图片和复杂表格的语义解析需要模型接口。

## 多模态风场请求

1. 在“风场实验”的“多模态风场请求”中填写文字或选择附件。
2. 选择解析模型及矩阵首行对应的位置。
3. 点击“解析并预览风场”，检查识别说明、假设提示、热图与原语 JSON。
4. 点击“应用到实验配置”，核对测点、运行预算和误差要求。
5. 点击“启动实验”，查看执行记录和分析结果，下载实验包。

### 表格输入

| 类型 | 读取方式 |
|---|---|
| Excel `.xlsx`、`.xls` | 支持读取多个工作表 |
| CSV、TSV | 支持常见文本编码及逗号、制表符、分号分隔 |
| 纯数值风速矩阵 | 行列数与实验配置一致，默认 24×32，数值单位 m/s |
| 标准测点表 | 使用 `fan_x,fan_y,target_mps` 列名，可附带 `id,x_mm,y_mm,target_ti` |
| 说明性表格或表格截图 | 结合附带文字由模型进行语义解析 |

单个标准矩阵或标准测点表可直接在本地转换为原语。带文字说明的表格、多工作表和其他布局由模型综合理解。

风扇索引从 0 起，`x` 向右、`y` 向上。矩阵默认第一行对应底部 `y=0`，可在网页切换为顶部优先。标准测点表未指定的风扇单元设为 0 m/s。提供完整 `x_mm,y_mm` 列时导入探针坐标，否则沿用当前测点位置。测点的目标风速按生成风场同步更新。

### 图片与附件限制

- 图片格式支持 PNG、JPEG（`.jpg` / `.jpeg`）、WebP、GIF。
- 每张图片宽、高均不超过 8192 像素。
- 一次最多 5 个附件，每个最多 8 MB，合计最多 16 MB。
- 每份表格最多读取 20,000 个单元格，`.xlsx` 解压后体积最多 40 MB。

可上传风场示意图、风速热图、剖面曲线图和表格截图。建议附上风速单位、标尺和坐标方向，并核对模型给出的假设。

### 模型配置

当前 DeepSeek 官方 `deepseek-flash` 支持图片输入，默认文本与视觉请求都可使用 Flash。网页提供“自动”“文本模型”“视觉模型”选择，自动模式会将包含图片的请求交给视觉模型。[DeepSeek 视觉接口文档](https://api-docs.deepseek.com/guides/vision)

```dotenv
DEEPSEEK_API_KEY=填写你的密钥
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

可通过 `TEXT_MODEL`、`TEXT_API_KEY`、`TEXT_BASE_URL` 单独配置文字与表格解析及 ReAct 决策模型，通过 `VISION_MODEL`、`VISION_API_KEY`、`VISION_BASE_URL` 配置视觉模型。留空时继承基础 DeepSeek 配置。自定义服务需兼容 OpenAI Chat Completions 和 JSON 输出，视觉服务需支持 `image_url` 内容块。

完整变量见 [.env.example](.env.example)。本地 `.env` 由程序加载，密钥保存在本机。

## 项目结构

```text
labagent/
  inputs.py           多模态附件读取、模型解析与风场配置生成
  models.py           实验契约、测点与风场原语校验
  specs.py            原语归一化与物理风速矩阵展开
  agent.py            离线与模型 ReAct 决策
  runner.py           实验执行、恢复与预算
  api.py              网页、解析、任务及下载接口
  tools/              设备、分析与反馈策略
  web/                React 网页工作台
motor/                电机指令解析与模拟协议
blockchain_experiment_demo/  实验证据上链演示
开发说明/              安装、架构与硬件对接文档
```

多模态解析接口为 `POST /api/requests/parse`，返回识别说明、提示、原语、风速矩阵和可应用的实验配置。请求格式与操作细节见 [启动与操作](开发说明/01-启动与操作.md)。其他架构、硬件适配和开发说明见 [开发说明索引](开发说明/README.md)。

## 验证

```bash
uv run python -m unittest discover -s tests -v
```

多模态功能已验证文字与图片的真实 Flash API 调用、24×32 混合输入矩阵逐单元一致性，以及浏览器上传、预览、应用配置与模拟实验流程。实际风洞运行需结合目标设备完成串口、探针与热线仪联调和标定。

## 电机自然语言控制模块

给王凯串口程序对接前使用的小模块：自然语言 → LangGraph 解析节点 → Pydantic 校验 → 单行 JSON → 模拟接收端再次校验 → 模拟 ACK。

这是**暂定协议和纯软件验证**，不是已确认的 Arduino 接口。没有串口写入、HTTP 请求或后台运行。

## 代码结构

电机相关代码统一放在 `motor/` 包中，与根目录的记忆和思考模块并列：

```text
motor/               电机命令模块
  __init__.py        对外导出
  models.py          Pydantic 数据模型和 LangGraph 状态
  parsers.py         规则解析、DeepSeek 解析和聊天
  protocol.py        JSON 协议帧校验
  graph.py           LangGraph 节点和工作流
  cli.py             命令行参数与交互循环
motor_agent.py       兼容启动入口
memory_module.py     记忆模块
thinking_module.py   思考模块
```

原来的 `python motor_agent.py ...` 命令仍然可用，也可以通过 `from motor import run_command` 使用电机模块。

LLM 交互模式下，如果连续 30 次输入失败，下一次输入会自动切换到本地
`rule` 模式；在切换前，成功的一次输入会将连续失败次数清零。切换后本次交互会话保持
`rule` 模式。

## 运行

```bash
cd ~/Life/Coding4life/02_Learning/langGraph/motor_command_agent
uv sync
uv run python motor_agent.py --demo
uv run python motor_agent.py '让电机顺时针转90度'
uv run python motor_agent.py '电机反转一百二十度'
uv run python -m unittest discover -s tests -v
```

## DeepSeek 交互测试与记录

项目依赖已包含 DeepSeek 所需组件，直接同步即可：

```powershell
uv sync
```

然后启动交互模式：

```powershell
uv run python motor_agent.py --mode llm --interactive --history-db motor_history.sqlite3
```

每次在 `电机>` 后输入一句话，例如 `让电机顺时针转90度`。程序会调用 DeepSeek
解析，再经过本地 Pydantic 校验和模拟接收。输入 `quit` 或 `exit` 退出。当前**不会
打开串口，也不会驱动真实电机**。

查看最近记录：

```powershell
uv run python motor_agent.py --history --history-db motor_history.sqlite3
```

记录包含原始输入、是否通过、生成的命令或错误和时间；不会保存 API key 或模型提示词。

## 与 DeepSeek 连续聊天

如果想像普通聊天一样和 DeepSeek 对话，而不是让它解析电机指令，使用独立的
`--chat` 模式：

```powershell
uv run python motor_agent.py --chat
```

然后可以连续输入：

```text
你> 什么是牛顿第二定律？
你> 它可以怎样用于电机实验？
你> 帮我设计一个简单的验证方案
```

这个模式会保留当前会话上下文，但**不会解析、保存或执行电机控制 JSON**。
输入 `quit` 或 `exit` 退出。电机控制仍使用：

```powershell
uv run python motor_agent.py --mode llm --interactive
```

默认 `rule` 模式不需要 API key、不调用大模型，采用两个严格句型，不能理解任意自然语言。拒绝的指令返回 `ok=false`，进程退出码为 1，且不会生成协议帧。成功退出码为 0。

## 大模型模式（可选）

```bash
uv sync
export DEEPSEEK_API_KEY='你的 DeepSeek API 密钥'
export DEEPSEEK_MODEL='deepseek-flash'
uv run python motor_agent.py --mode llm '请让马达逆时针转90度'
```

程序通过 LangChain `ChatOpenAI` 的 OpenAI 兼容接口请求 DeepSeek（`https://api.deepseek.com`），并用 `with_structured_output` 产生结构化候选，再交给独立校验节点。程序自动读取项目根目录的 `.env`，可从 `.env.example` 复制配置。不要提交真实密钥。模型需要支持工具调用。LLM 可能误解语义，数值校验不能证明它理解正确，电机模块在 LLM 模式下采用模拟接收，正式硬件执行前必须增加人工确认。
LLM 模式还会先检查输入是否包含明显的电机控制意图；例如“今天天气怎么样”会直接拒绝，不会被模型转换成零度指令。

## 第一版暂定 JSON 协议

```json
{"id":"cmd-ec045f13","command":"move_relative","angle_deg":90}
```

- 编码 UTF-8，一条 JSON 后接一个 `\n`，最大 1024 字节；不包含 Markdown 代码块。
- `id` 由程序生成，格式为 `cmd-` 加十六进制编号。
- `command` 当前固定为 `move_relative`，表示相对当前位置转动。
- `angle_deg` 必须是整数，范围为 -360 到 360；顺时针为正数，逆时针为负数。
- Python 串口桥应先按上述格式校验，再调用自己的串口程序；当前的 `decode_frame` 是软件模拟，绝不代表 Arduino 固件已验证。

模拟 ACK 示意：

```json
{"id":"cmd-ec045f13","status":"simulated","message":"模拟接收成功；未打开串口，未驱动电机"}
```

## 中文代码阅读说明

主程序和测试文件已经补充中文注释，包含模块用途、函数职责、数据流及关键语法。
第一次阅读建议先看 [解析器说明](README_PHARSER.md)，再从 `motor_agent.py` 底部的 `main()` 沿调用关系阅读。
这些说明不改变程序运行逻辑，当前仍然只模拟接收，不打开串口。

## 等王凯确认

1. 电机类型与驱动板：直流电机、步进电机、舵机？实际控制量是 RPM、PWM（0–255）、角度还是步数？
2. 字段名、设备编号、正反方向、单位、合法范围及停止命令。
3. Mac 串口端口、波特率、编码、换行分帧规则，以及接收 JSON 的是 Python 桥还是 Arduino。
4. 执行确认 ACK、异常响应、超时自动停止、断连保护、请求去重和物理急停。

得到正式格式后，主要调整 `MoveCommandCandidate` / `MoveCommand` 及解析输出映射，LangGraph 的节点结构可以保留。不要把示例上限直接拿来控制硬件。

## 记忆与科学思考模块

项目现在还包含两个独立模块：

- `memory_module.py`：使用 SQLite 持久化观察、结论、约束和安全事项。默认数据库是
  `agent_memory.sqlite3`，也可以通过 `MemoryStore("路径")` 注入其他数据库文件。
- `thinking_module.py`：用 LangGraph 执行 `retrieve → reason → review`，将问题拆成
  理解、待验证假设、证据需求、下一步动作和风险。默认是离线结构化规划，不把假设冒充
  成事实，也不会自动执行动作。

示例：

```bash
uv run python memory_module.py --db demo.sqlite3 add "电机实验必须先确认急停" --category safety --importance 5
uv run python thinking_module.py --db demo.sqlite3 "如何设计电机负载实验？"
```

思考模块的 `MemoryStore` 是显式依赖，后续可以把 `reason` 节点替换为
LangChain 的结构化聊天模型，保留 `ScientificPlan` 校验和 `review` 安全门。
涉及真实设备时，思考结果只能作为行动建议，仍需人工确认、独立的协议校验和硬件急停。
