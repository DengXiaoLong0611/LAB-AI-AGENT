# 单机风洞实验平台 Implementation Plan

> 使用 executing-plans 在当前会话逐步实现。用户已授权开发，无需再次询问。

**Goal** 建立可在网页调试的单机实验反馈平台。

**Architecture** FastAPI 提供 API 与 React 静态网页，串行后台执行 LangGraph，SQLite 保存任务事实与 checkpoint，设备适配通过明确 Python 接口注入。

**Tech Stack** Python >=3.11，LangGraph，Pydantic，FastAPI，React 18。

**Spec** docs/superpowers/specs/2026-11-05-lab-platform-design.md

## Global Constraints

- 保留现有 motor 命令入口和测试，新增 labagent 包。
- 默认模拟，工具按 devices、analysis、control 分类。
- 单进程串行设备访问，未知操作结果先核对。
- 长期记忆保留证据与标定版本，原始数据独立保存。

## Review Focus

- 含 NaN、越界、错误测点和不一致原语的输入在设备调用前拒绝。
- 设备完成后回报或 checkpoint 丢失时不能重复动作。
- 网页断连重连能读取历史事件，暂停取消有明确确认状态。
- 全零风速、缺失采样与坏采样不得产生伪造的湍流度和达标结果。
- 旧入口与既有数据库可继续使用。

## Task 1 契约、设备与分析

Files 为 labagent/models.py、specs.py、tools/devices.py、tools/analysis.py、tools/control.py。
Interfaces 为 ExperimentSpec、validate_field、DeviceAdapter、analyze_samples、FeedbackStrategy。
- [x] 新增原语、采样与控制策略的失败测试并运行。
- [x] 实现严格验证、模拟设备、华航回调适配器和统计分析。
- [x] 运行组件测试，确认错误输入与有效数据行为。

## Task 2 持久化与实验执行

Files 为 labagent/store.py、agent.py、runner.py。
Interfaces 为 LabStore、ExperimentRunner、build_experiment_graph、make_deepseek_policy。
- [x] 新增数据库去重、记忆过滤、闭环与暂停恢复测试并运行。
- [x] 实现数据库、类型化模型工具调用、LangGraph 节点和后台运行器。
- [x] 验证收敛、异常、取消、未知操作和恢复。

## Task 3 API 与网页

Files 为 labagent/api.py、__main__.py、web/index.html、web/app.js、web/style.css。
Interfaces 为 create_app(data_dir, device_factory, policy_factory) 与 /api/tasks、事件流、产物、debug、memories。
- [x] 新增 API、网页资源与下载测试并运行。
- [x] 实现单机后台生命周期、任务控制、事件与分层工具调试及 React 界面。
- [x] 真实 HTTP 与浏览器检查创建任务、进度、图表和下载。

## Task 4 验证与交付

- [x] 运行全部测试、编译检查和独立重启恢复检查。
- [x] 检查说明与真实接口一致，补充启动与王凯对接指南。
- [x] 记录验证结果和未进行的实机验证。

## 执行记录

Ruling 当前目录没有 Git 仓库，在现有目录新增文件，无法使用 worktree 或提交。旧 .venv 无 Python 可执行文件，使用独立 .lab-venv 验证，保留已有环境文件。

验证记录见 开发说明/04-验证与后续开发.md。修正取消与终态停止的事务竞争，持久化停止意图与最终分析，恢复优先完成待停止动作。
