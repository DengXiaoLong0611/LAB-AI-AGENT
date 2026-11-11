# 试验数据存证最小验证原型

这组示例用于学习图片中“AI Agent 处理试验数据后进行区块链存证”的想法。
使用项目现有模拟风洞采样和分析函数，所有新增代码集中在本文件夹。

## 对这个想法的判断

值得探索。最直接的价值是让协作者核对拿到的数据、参数、分析结果是否与当初提交的一致，并通过保留的环境信息排查复现条件差异。

真实性需要可信采集链路支撑。错误传感器、错误标定、人工编造的数据同样可以生成哈希并上链。
物理试验复现还涉及来流、温湿度、安装误差、控制时序和随机性，需要按试验方法预先定义比较指标与容差。
图片中的“95%不能复现”需要补充可核查来源后才能作为定量论据。

## 阅读顺序

1. `evidence.py` 生成记录、计算指纹、保存和核验。
2. `demo_local.py` 验证指纹、篡改检测、环境记录变化和分析复算。
3. `demo_ethereum.py` 把指纹提交到本地 Ethereum 测试链，再从链上回读核验。
4. `ExperimentEvidence.sol` 定义智能合约，`demo_contract.py` 编译、部署并验证规则。

## 运行

在项目根目录执行。当前工作区已安装独立环境，可以直接运行后两条命令。
重新配置环境时先执行前两条。需要 Python 3.11 至 3.14 和 uv。

```bash
uv venv blockchain_experiment_demo/.venv --python 3.13
uv pip install --python blockchain_experiment_demo/.venv/bin/python -r blockchain_experiment_demo/requirements.txt
blockchain_experiment_demo/.venv/bin/python blockchain_experiment_demo/demo_local.py
blockchain_experiment_demo/.venv/bin/python blockchain_experiment_demo/demo_ethereum.py
```

脚本使用本地模拟数据和测试币，运行过程离线可用。首次安装依赖需要网络。
正常输出包含 `[通过]`。断言失败时程序以非零状态退出。运行验证时保留 Python 默认设置，使用 `-O` 会关闭断言。
原始 JSON 记录保存在 `output/` 中，以 SHA-256 指纹命名。

## 一份记录包含什么

- `config` 保存目标风速、采样率、样本数、设备编号、标定版本等。
- `commands` 保存施加的风扇场指令。
- `measurement` 保存原始风速样本、测点、时间和模拟标记。
- `analysis` 保存项目原有工具计算的平均风速、标准差和湍流强度等。
- `environment` 保存 Python 版本、操作系统、相关源码指纹和示例温度。

温度为演示声明值。实际应用需接入环境仪器并补充依赖锁文件、固件版本等。
时间来自本机时钟，模拟采样函数每次生成当前时间，因此重复运行通常得到不同指纹。

## 第一段示例证明什么

运行 `demo_local.py` 会检查四件事。

1. 保存后重新读取的记录与指纹一致。
2. 修改一个原始样本后，核验失败。
3. 修改温度记录后，核验失败。
4. 同一批原始样本经当前分析函数复算，得到相同结果。

第三项展示环境元数据纳入存证后的变化检测。它不建立温度与试验结果之间的因果关系。
第四项检验分析复算。真实风洞重做试验需要单独验证。
离线示例的参考指纹保存在内存中，可信参考指纹需要独立保管。

## 第二段示例证明什么

`demo_ethereum.py` 使用 Web3、eth-tester 和 PyEVM 执行本地 Ethereum 交易，
将整份记录的 32 字节 SHA-256 指纹写入交易 `data`，等待打包后回读交易。
它核验交易执行状态、区块归属、提交账户和指纹，并演示分析结果被修改后的核验失败。

这一段示例选择 Ethereum 普通交易存证。智能合约示例见下一节。
链上只保留指纹，完整数据保存在本地 JSON。协作者需要取得 JSON 和可信的链、交易哈希才能核验。
当前测试链由本机进程控制，退出后链状态消失，交易哈希仅用于这次本地演示。
公开网络的持久性、独立见证、区块确认和链重组处理需要另行验证。
提交账户表示交易的发送者，真实实验人员身份需要额外的身份绑定。

## 第三段示例，Solidity 智能合约

当前工作区已安装编译器，直接执行。

```bash
blockchain_experiment_demo/.venv/bin/python blockchain_experiment_demo/demo_contract.py
```

在其他电脑上，先按前文安装 Python 依赖，再安装 Node.js 与固定版本编译器。

```bash
npm ci --prefix blockchain_experiment_demo
```

`compile_contract.cjs` 使用 solc 0.8.30 将 Solidity 源码编译为 ABI 和字节码，
Python 脚本随后在本地 PyEVM 测试链部署合约并执行验证。

合约提供两个方法。

- `register(experimentId, digest)` 将指纹存入当前提交账户的试验编号空间，同一账户的同一编号只能登记一次。
- `verify(submitter, experimentId, candidateDigest)` 将待核验指纹与指定账户的原始存证比较。

脚本会验证编译、部署、未登记记录、成功登记、篡改检测、重复登记回滚、账户隔离以及空输入拒绝。
防覆盖由合约状态和 `require` 执行，失败交易在 EVM 中回滚。
任何账户都可以在自己的空间提交数据，因此核验时必须指定可信提交账户。
合约登记的时间来自区块时间，采样时间仍由链外记录提供。
这是学习原型，已在本地 EVM 验证，公开网络部署与安全审计仍需单独完成。

## 与现有 Agent 的衔接位置

现有采集工具返回 `samples_mps` 后，分析工具计算统计量。
可以在采集与分析成功后加入确定性的存证工具，流程如下。

```text
设备采样 → 保存原始数据 → 分析 → 整理配置与环境 → 计算指纹 → 提交存证 → 回读核验
```

本原型直接调用 `SimulatedDevices` 和 `analyze_samples`，方便观察确定性的数据流程。
Agent 集成时应由程序读取已保存的采样文件并计算指纹，模型负责选择工具与解释核验结果。
现有 `LabStore` 可保存链编号、交易哈希、记录文件位置和提交状态。
正式集成还需要处理失败重试、防重复提交和链上确认状态。
