"""第二步，把记录指纹写进本地 Ethereum 交易，再从已打包交易中核验。"""

from copy import deepcopy
import json

from eth_tester import EthereumTester, PyEVMBackend
from web3 import EthereumTesterProvider, Web3

from evidence import collect_record, fingerprint, save_record, verify_record


def main():
    # 本地 EVM 测试链提供测试账户与测试币，每次启动创建一条新链。
    # 显式选择 PyEVMBackend，确保实际执行 EVM 后端。
    tester = EthereumTester(backend=PyEVMBackend())
    web3 = Web3(EthereumTesterProvider(tester))
    account = web3.eth.accounts[0]

    path = save_record(collect_record())
    record = json.loads(path.read_text(encoding="utf-8"))
    digest = fingerprint(record)

    # 最小 Ethereum 存证只需把 32 字节指纹放到交易 data 字段。
    # 完整数据保存在链外，链上提供可独立读取的指纹及交易提交账户。
    transaction_hash = web3.eth.send_transaction({
        "from": account, "to": account, "value": 0,
        "data": bytes.fromhex(digest), "gas": 100000,
    })
    receipt = web3.eth.wait_for_transaction_receipt(transaction_hash)
    assert receipt["status"] == 1
    transaction = web3.eth.get_transaction(transaction_hash)
    assert transaction["blockHash"] == receipt["blockHash"]
    assert transaction["from"] == account
    # 从链上回读指纹，核验时使用链上的值作为参照。
    on_chain_digest = bytes(transaction["input"]).hex()
    assert verify_record(record, on_chain_digest)
    print("[通过] 交易已打包，链上指纹与本地试验记录一致")

    modified = deepcopy(record)
    modified["analysis"]["mean_mps"] += 1
    assert not verify_record(modified, on_chain_digest)
    print("[通过] 修改分析结果后，与链上指纹核验失败")
    print(f"区块高度 {receipt['blockNumber']}")
    print(f"交易哈希 {web3.to_hex(transaction_hash)}")
    print(f"提交账户 {account}")
    print(f"记录文件 {path}")
    print("本次使用进程内测试链，进程退出后链数据消失。")


if __name__ == "__main__":
    main()
