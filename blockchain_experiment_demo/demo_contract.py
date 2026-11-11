"""编译 Solidity → 本地部署 → 登记试验 → 核验 → 检查防覆盖规则。"""

from copy import deepcopy
import json
from pathlib import Path
import subprocess

from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed
from web3 import EthereumTesterProvider, Web3

from evidence import collect_record, fingerprint, save_record


def main():
    # 1. 编译同目录下的 Solidity 源码。Node.js 仅用于运行 solc 编译器。
    directory = Path(__file__).resolve().parent
    compiled = subprocess.run(
        ["node", str(directory / "compile_contract.cjs")],
        check=True, capture_output=True, text=True,
    )
    artifact = json.loads(compiled.stdout)
    print("[通过] Solidity 源码编译成功")

    # 2. 创建进程内测试链，使用测试账户和测试币部署合约。
    web3 = Web3(EthereumTesterProvider(EthereumTester(backend=PyEVMBackend())))
    alice, bob = web3.eth.accounts[:2]
    factory = web3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    deployment = factory.constructor().transact({"from": alice})
    receipt = web3.eth.wait_for_transaction_receipt(deployment)
    assert receipt["status"] == 1
    contract = web3.eth.contract(address=receipt["contractAddress"], abi=artifact["abi"])
    print(f"[通过] 合约部署成功 {contract.address}")

    # 3. 复用项目现有采样与分析。将试验编号转换为固定长度 bytes32。
    path = save_record(collect_record())
    record = json.loads(path.read_text(encoding="utf-8"))
    experiment_id = web3.keccak(text="wind-demo-001")
    digest = bytes.fromhex(fingerprint(record))
    assert not contract.functions.verify(alice, experiment_id, digest).call()
    print("[通过] 尚未登记的试验核验返回 False")

    transaction = contract.functions.register(experiment_id, digest).transact({"from": alice})
    receipt = web3.eth.wait_for_transaction_receipt(transaction)
    assert receipt["status"] == 1
    saved_digest, submitted_at = contract.functions.records(alice, experiment_id).call()
    assert bytes(saved_digest) == digest and submitted_at > 0
    assert contract.functions.verify(alice, experiment_id, digest).call()
    print("[通过] 登记成功，合约存储的指纹与原始记录一致")

    # 4. 修改样本再计算指纹，合约会返回 False。
    modified = deepcopy(record)
    modified["measurement"]["samples_mps"][0] += 1
    modified_digest = bytes.fromhex(fingerprint(modified))
    assert not contract.functions.verify(alice, experiment_id, modified_digest).call()
    print("[通过] 样本被修改后，合约核验返回 False")

    # 5. 指定 gas，绕过预估阶段，让重复提交真正进入区块并由 EVM 回滚。
    duplicate = contract.functions.register(experiment_id, modified_digest).transact(
        {"from": alice, "gas": 200000},
    )
    failed = web3.eth.wait_for_transaction_receipt(duplicate)
    assert failed["status"] == 0
    assert contract.functions.verify(alice, experiment_id, digest).call()
    print("[通过] 重复登记交易回滚，原有存证保留")

    # 6. Bob 使用相同编号只影响 Bob 的空间，Alice 的存证保持原值。
    bob_transaction = contract.functions.register(experiment_id, modified_digest).transact(
        {"from": bob},
    )
    assert web3.eth.wait_for_transaction_receipt(bob_transaction)["status"] == 1
    assert contract.functions.verify(bob, experiment_id, modified_digest).call()
    assert contract.functions.verify(alice, experiment_id, digest).call()
    print("[通过] 不同账户的同名试验分别保存")

    # 7. 用只读模拟调用检查非法输入。预期必须触发合约拒绝。
    for experiment, value in [(bytes(32), digest), (web3.keccak(text="empty"), bytes(32))]:
        try:
            contract.functions.register(experiment, value).call({"from": alice})
        except TransactionFailed:
            pass
        else:
            raise AssertionError("合约未拒绝空编号或空指纹")
    print("[通过] 空试验编号、空指纹均被拒绝")
    print(f"原始记录 {path}")
    print("本地测试链在程序退出后消失。")


if __name__ == "__main__":
    main()
