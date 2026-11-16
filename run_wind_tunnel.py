"""真机风场控制工作台入口。

用 WindTunnelDevices（真机适配器）启动服务，数据目录 lab_data_wt（与虚拟 lab_data 分开）。
区别于 `python -m labagent`（默认 SimulatedDevices，纯模拟、不写串口）。

启动（在 motor_command_agent 目录）：
    .../wind-tunnel/bin/python3 run_wind_tunnel.py
浏览器打开 http://127.0.0.1:8765
"""
import uvicorn

from labagent.api import create_app
from labagent.tools.devices import WindTunnelDevices

app = create_app("lab_data_wt", device_factory=WindTunnelDevices)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, workers=1, timeout_graceful_shutdown=3)
