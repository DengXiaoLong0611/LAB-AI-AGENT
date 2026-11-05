# 这个程序是干什么的？

把一句明确的电机控制指令转换成机器容易读取的 JSON，再在软件里模拟接收。目的是先验证上层 agent 的输出形式，方便以后与王凯的串口程序对接。

**它现在不会打开串口，不会驱动 Arduino，也不会让电机转动。暂定 JSON 必须等王凯确认后再调整。**

## 先看一次完整流程

输入：`让电机以120转每分钟顺时针转动3秒`。

1. `main()`：从终端读取输入，选择离线规则或大模型模式。
2. `run_command()`：把输入放进状态字典，启动 LangGraph。
3. `parse_node()`：调用解析器，得到 `move_relative` 和角度两个字段。
4. `validate_node()`：用 Pydantic 检查类型、范围及字段之间的关系，再加入设备、协议版本和请求编号。
5. `simulate_node()`：把字典转换成 JSON 文本，加换行，编码为 UTF-8 字节。
6. `decode_frame()`：模拟对方接收，重新检查字节、JSON 和协议，再返回模拟回执。

解析或校验失败时直接结束，不进入模拟接收。`trace` 显示实际走过的节点。

## 各个文件怎么读？

- `motor_agent.py`：主程序，已加入中文模块说明、函数说明和关键语法注释。
- `tests/test_agent.py`：自动化测试，用正常输入和错误输入检查行为。
- `pyproject.toml`：项目和依赖配置，不是 Python 执行代码。
- `.env.example`：大模型环境变量示例；程序不会自动读取 `.env` 文件。
- `README.md`：安装、运行、JSON 协议和硬件对接注意事项。

第一次阅读建议先看主程序底部的 `main()`，沿着调用关系向上读，不必从 import 开始背。

## Python 语法速查

| 写法 | 在本程序中的意思 |
| --- | --- |
| `# 中文说明` | 注释，不作为指令执行 |
| `"""说明"""` | 文档字符串，描述模块、类或函数，也能通过帮助工具查看 |
| `import json` | 引入 JSON 模块 |
| `def run_command(...):` | 定义函数；冒号后的缩进部分是函数体 |
| `text: str` / `-> int` | 参数及返回值类型提示，本身不会自动强制检查普通函数输入 |
| `return result` | 返回结果并结束当前函数 |
| `class ... (BaseModel):` | 定义继承 Pydantic BaseModel 的数据模型 |
| `self` / `cls` | 当前对象 / 当前类的约定参数名 |
| `@field_validator(...)` | 装饰器，将下面的方法注册为字段校验器 |
| `{"command": "move_relative"}` | 字典，由键和值组成 |
| `state["text"]` | 读取字典中的 text；不存在时会抛异常 |
| `["parse", "validate"]` | 列表，保存有顺序的多个元素 |
| `(0, "none", 0)` | 元组，在这里把三个字段一起比较 |
| `if ... elif ... else ...` | 根据条件选择执行分支 |
| `for text in texts` | 遍历列表，逐条处理 |
| `try ... except ...` | 尝试执行并捕获异常 |
| `raise ValueError(...)` | 主动报告输入不合法 |
| `lambda text: candidate` | 一个简短函数，测试时用它模拟解析器 |
| `**candidate.model_dump()` | 将字典展开为关键字参数，不是乘方用法 |
| `A if condition else B` | 条件满足取 A，否则取 B |
| `match := ...` | 在表达式里赋值；区别于比较 `==` |
| `f"角度上限：{MAX_ANGLE_DEG}"` | 把变量值插入字符串 |
| `r"\s+"` / `rf"...{number}..."` | 原始字符串 / 同时支持原始字符串与变量插值 |
| `b"\n"` | 换行字节；与文本字符串 `"\n"` 类型不同 |
| `if __name__ == "__main__"` | 只在直接运行本文件时执行入口代码 |

缩进决定代码属于哪个函数、循环或条件。不要随意删掉行首空格。

## 字典、JSON、字节不是一回事

```python
command = {"command": "move_relative", "angle_deg": 90}
json_text = json.dumps(command)
frame = (json_text + "\n").encode("utf-8")
received = json.loads(frame.decode("utf-8"))
```

- `command` 是 Python 字典，方便代码查字段和校验。
- `json_text` 是字符串，包含 JSON 格式文本，方便不同语言的程序交换数据。
- `frame` 是字节，接近未来串口传输需要的形式；当前只保存在内存里。
- `received` 又变回 Python 字典。`dumps` 编码为 JSON 文本，`loads` 从 JSON 文本解析。

`"120"` 是字符串，`120` 才是整数。`integer_only()` 专门做严格转换，不接受小数或布尔值。

## LangGraph 与大模型分别做什么？

LangGraph 是流程编排工具，不是大模型。本程序通过节点和边组织 `parse → validate → simulate`。

- `StateGraph(GraphState)` 定义共享状态的图。
- `add_node()` 注册处理步骤，`add_edge()` 指定固定顺序。
- `add_conditional_edges()` 按状态选择下一步，遇到错误结束。
- `compile()` 生成可执行图，`invoke()` 才实际运行。
- 节点返回的字典是状态更新，图会将相应字段合并进共享状态。

默认 `rule` 模式只识别限定句式，不调用模型。`llm` 模式通过 LangChain 的 `ChatOpenAI` OpenAI 兼容接口请求 DeepSeek，并把 `MoveCommandCandidate` 绑定为结构化输出 Schema。格式校验能拦截很多错误，**不能保证模型对自然语言的理解一定正确**。

## 读懂测试

`unittest.TestCase` 提供断言：`assertTrue` 检查成功，`assertEqual` 检查结果相等，`assertNotIn` 检查没有不该出现的字段，`assertRaises` 检查错误是否被拒绝。

测试里的 `lambda` 是假的解析器，用来验证大模型输出校验，不会发真实模型请求。测试通过只说明这些软件测试场景通过，不代表真实硬件或所有自然语言都验证完了。

```bash
uv run python motor_agent.py --demo
uv run python -m unittest discover -s tests -v
```

`--demo` 演示三条指令；测试命令自动发现 tests 文件夹里的测试，`-v` 显示每个测试名字。
