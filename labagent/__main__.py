"""python -m labagent 启动单机平台。"""

import argparse
from pathlib import Path

import uvicorn

from .api import create_app


def main():
    parser = argparse.ArgumentParser(description="流体实验 Agent 本地工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path("lab_data"))
    args = parser.parse_args()
    uvicorn.run(
        create_app(args.data_dir),
        host="127.0.0.1",
        port=args.port,
        workers=1,
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    main()
