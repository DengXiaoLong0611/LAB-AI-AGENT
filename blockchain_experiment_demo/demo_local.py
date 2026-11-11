"""第一步，离线理解指纹、篡改检测、环境差异和分析复算。"""

from copy import deepcopy
import json

from evidence import collect_record, fingerprint, save_record, verify_record
from labagent.tools.analysis import analyze_samples


def main():
    record = collect_record()
    path = save_record(record)
    expected = fingerprint(record)
    # 重新从磁盘读入，验证实际保存的数据。
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert verify_record(loaded, expected)
    print("[通过] 原始记录与存证指纹一致")

    modified = deepcopy(loaded)
    modified["measurement"]["samples_mps"][0] += 1
    assert not verify_record(modified, expected)
    print("[通过] 修改一个风速样本后，核验失败，成功发现篡改")

    changed_environment = deepcopy(loaded)
    changed_environment["environment"]["room_temperature_c"] = 30.0
    assert not verify_record(changed_environment, expected)
    print("[通过] 温度记录变化后，整份记录的指纹也发生变化")

    # 使用同一份原始样本和当前分析代码复算。这验证分析复算，物理重做另需试验。
    measured = loaded["measurement"]
    recomputed = analyze_samples(
        measured["samples_mps"], sample_rate_hz=measured["sample_rate_hz"],
    )
    assert recomputed == loaded["analysis"]
    print("[通过] 原始样本复算得到相同分析结果")
    print(f"记录文件 {path}")
    print(f"SHA-256 {expected}")


if __name__ == "__main__":
    main()
