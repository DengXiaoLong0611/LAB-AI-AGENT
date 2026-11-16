"""真机通道验证：用 labagent 的 WindTunnelDevices 直接驱动风扇（不测风速、不走 agent）。

用法（在 motor_command_agent 目录，wind-tunnel conda 环境）：
    .../bin/python3 real_check.py a0   预热（发一帧全 0）
    通道验证（raw 直发档位，绕开展开/换算）：
    .../bin/python3 real_check.py a1   单格（中间一个风扇）
    .../bin/python3 real_check.py a2   整行（中间第 13 行）
    .../bin/python3 real_check.py a3   整列（中间第 17 列）
    .../bin/python3 real_check.py a4   全场低档（档位 50）
    .../bin/python3 real_check.py a5   单格高档（档位 200，排查「档位太低 vs 帧没到」）
    .../bin/python3 real_check.py a6   单点满载（只中间一个风扇档位 253，测单风扇满载风速）
    全链路验证（普通原语：spec 校验 → 展开 m/s → 档位 → 下发）：
    .../bin/python3 real_check.py p1   uniform 全场 3 m/s
    .../bin/python3 real_check.py p2   profile 幂律（顶部高→底部低）
    .../bin/python3 real_check.py p3   gradient 横向递增

安全：
- 读 motor_command_agent/.env 的 FAN_DRY_RUN / FAN_SERIAL_PORT。
- FAN_DRY_RUN=1（默认）只打印、不写串口，可在家先冒烟。
- FAN_DRY_RUN=0 才真正写串口：确认串口被本程序独占（关掉 Arduino IDE 串口监视器），
  风扇阵列独立电源处于可随时断电状态。
"""

import sys
import time

from labagent.models import RAW_MAX, ExperimentSpec
from labagent.tools.devices import (
    FAN_BAUD,
    FAN_DRY_RUN,
    FAN_SERIAL_PORT,
    WindTunnelDevices,
)

ROWS, COLS = 24, 32


def make_config() -> ExperimentSpec:
    return ExperimentSpec.model_validate(
        {
            "name": "真机通道验证",
            "points": [
                {
                    "id": "p0",
                    "x_mm": 0,
                    "y_mm": 0,
                    "fan_x": 0,
                    "fan_y": 0,
                    "target_mps": 3.0,
                },
            ],
            "rows": ROWS,
            "columns": COLS,
            "max_iterations": 1,
            "tolerance_mps": 1.0,
        }
    )


def zeros() -> list[list[int]]:
    return [[0] * COLS for _ in range(ROWS)]


def _print_row_levels(levels, rows=(0, 6, 12, 18, 23)):
    """打印抽样行的档位，用于肉眼核对趋势（profile/gradient）。"""
    print("抽样行档位（看趋势）：")
    for r in rows:
        row = levels[r]
        print(f"  row {r:>2}: {row[0]} … {row[-1]}（行内均值 {sum(row) // len(row)}）")


def main():
    level = sys.argv[1] if len(sys.argv) > 1 else "a0"
    print("=" * 64)
    print(
        f"FAN_DRY_RUN={FAN_DRY_RUN}  FAN_SERIAL_PORT={FAN_SERIAL_PORT}  "
        f"FAN_BAUD={FAN_BAUD}"
    )
    if not FAN_DRY_RUN:
        print(">>> 真机模式：即将写串口！确认串口独占 + 独立电源可随时断电。")
    print("=" * 64)

    dev = WindTunnelDevices(make_config())
    dev.connect()
    try:
        if level == "a0":
            dev.activate()
            print("A0 预热完成：已发一帧全 0。")
            print(f"帧字节数 = {len(dev.last_frame)}（应为 770）")
            return

        # 先预热（a1~p3 通用）
        dev.activate()

        # ---- 通道验证（raw：绕过展开/换算，直发档位）----
        if level in {"a1", "a2", "a3", "a4", "a5", "a6"}:
            matrix = zeros()
            ROW_MID, COL_MID, LEVEL = ROWS // 2, COLS // 2, 80
            if level == "a1":
                matrix[ROW_MID][COL_MID] = LEVEL
                note = f"A1 单格：只开 (row{ROW_MID}, col{COL_MID}) 档位 {LEVEL} —— 应只有中间一个风扇转"
            elif level == "a2":
                for c in range(COLS):
                    matrix[ROW_MID][c] = LEVEL
                note = f"A2 整行：第 {ROW_MID} 行档位 {LEVEL} —— 应中间一整行转"
            elif level == "a3":
                for r in range(ROWS):
                    matrix[r][COL_MID] = LEVEL
                note = f"A3 整列：第 {COL_MID} 列档位 {LEVEL} —— 应中间一整列转"
            elif level == "a5":
                matrix[ROW_MID][COL_MID] = 200
                note = f"A5 单格高档：只开 (row{ROW_MID}, col{COL_MID}) 档位 200 —— 应只有中间一个风扇明显转"
            elif level == "a6":
                matrix[ROW_MID][COL_MID] = RAW_MAX
                note = f"A6 单点满载：只开 (row{ROW_MID}, col{COL_MID}) 档位 {RAW_MAX}（当前标定 = 20 m/s）—— 只这一个风扇全速，用手持风速仪对准它测满载风速"
            else:
                matrix = [[50] * COLS for _ in range(ROWS)]
                note = "A4 全场低档：档位 50 —— 应所有风扇转、趋势一致"
            print(note)
            result = dev.set_fan_field(
                [{"type": "raw", "matrix": matrix}], operation_id=f"check-{level}"
            )
            print("返回摘要：", result)

        # ---- 全链路验证（普通原语：spec 校验 → 展开 m/s → 档位 → 下发）----
        elif level == "p1":
            spec = [{"type": "uniform", "value": 3.0}]
            print("P1 全链路 uniform 3 m/s：期望全场同速 ≈ 38 档")
            result = dev.set_fan_field(spec, operation_id="check-p1")
            print("返回摘要：", result)
            print("期望档位：全场 38（3.0 m/s × 253/20 = 37.95 → 38）")
        elif level == "p2":
            spec = [
                {
                    "type": "profile",
                    "axis": "y",
                    "law": "power",
                    "alpha": 1,
                    "z_ref": 1,
                    "v_ref": 5.0,
                }
            ]
            print(
                "P2 全链路 profile 幂律(axis=y, alpha=1, v_ref=5)："
                "期望底部 row0≈0 → 顶部 row23≈63（行号自底部起）"
            )
            result = dev.set_fan_field(spec, operation_id="check-p2")
            print("返回摘要：", result)
            _print_row_levels(dev.current_levels)
        elif level == "p3":
            spec = [{"type": "gradient", "axis": "x", "from": 0.0, "to": 5.0}]
            print(
                "P3 全链路 gradient(axis=x, 0→5 m/s)："
                "期望横向递增 col0≈0 → col31≈63 档"
            )
            result = dev.set_fan_field(spec, operation_id="check-p3")
            print("返回摘要：", result)
            _print_row_levels(dev.current_levels)
        else:
            raise SystemExit(
                "未知级别：a0/a1/a2/a3/a4/a5/a6（通道 raw）/ p1/p2/p3（全链路普通原语）"
            )

        print(f"帧字节数 = {len(dev.last_frame)}（应为 770）")
        print(
            "帧头/帧尾：",
            hex(dev.last_frame[0]),
            hex(dev.last_frame[-1]),
            "（应为 0xfe / 0xff）",
        )
        print(">>> 保持串口 3 秒让风扇转起来观察（避免 close 立刻复位 Arduino）...")
        time.sleep(3)
    finally:
        dev.close()


if __name__ == "__main__":
    main()
