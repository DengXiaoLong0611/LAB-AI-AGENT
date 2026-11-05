# 电机自然语言控制测试

## 单机风洞实验平台

新增的 `labagent/` 提供网页、后台实验、ReAct 工具循环、风场分析和实验记忆。

```bash
.lab-venv/bin/python -m labagent
```

打开 [本地工作台](http://127.0.0.1:8765)。安装、架构图、硬件对接与修改方法见 [开发说明](开发说明/README.md)。


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
uv sync --extra llm
export DEEPSEEK_API_KEY='你的 DeepSeek API 密钥'
export DEEPSEEK_MODEL='deepseek-chat'
uv run python motor_agent.py --mode llm '请让马达逆时针转90度'
```

程序通过 LangChain `ChatOpenAI` 的 OpenAI 兼容接口请求 DeepSeek（`https://api.deepseek.com`），并用 `with_structured_output` 产生结构化候选，再交给独立校验节点。`.env.example` 只是模板，程序不自动读取 `.env`。不要提交真实密钥。模型需要支持工具调用。LLM 可能误解语义，数值校验不能证明它理解正确，因此本项目即使在 LLM 模式也只模拟，正式硬件执行前必须增加人工确认。
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
第一次阅读建议先看 [代码与语法导读](代码与语法导读.md)，再从 `motor_agent.py` 底部的 `main()` 沿调用关系阅读。
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
