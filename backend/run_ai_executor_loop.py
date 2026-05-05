"""
run_ai_executor_loop.py

Continuously checks for valid AI SMC trade setups by running demo_trade_executor.py
at a fixed interval.

Place this file in:
C:\Users\osama\cursor project\ea-ai-platform\backend

Example:
python run_ai_executor_loop.py --interval 300

300 seconds = every 5 minutes
900 seconds = every 15 minutes
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent
LOG_DIR = BACKEND_DIR / "storage" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / "ai_executor_loop.log"


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_log(message: str):
    line = f"[{timestamp()}] {message}"
    print(line)

    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_executor_once():
    cmd = [sys.executable, "demo_trade_executor.py"]

    write_log("Running demo_trade_executor.py...")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(BACKEND_DIR),
            capture_output=True,
            text=True,
            timeout=180,
        )

        if result.stdout:
            for line in result.stdout.strip().splitlines():
                write_log(line)

        if result.stderr:
            for line in result.stderr.strip().splitlines():
                write_log("ERROR: " + line)

        write_log(f"Executor finished with return code: {result.returncode}")

    except subprocess.TimeoutExpired:
        write_log("ERROR: demo_trade_executor.py timed out after 180 seconds.")

    except Exception as exc:
        write_log(f"ERROR: executor loop failed: {exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Seconds between setup checks. 300 = 5 minutes, 900 = 15 minutes.",
    )
    args = parser.parse_args()

    interval = max(60, int(args.interval))

    write_log("AI executor loop started.")
    write_log(f"Check interval: {interval} seconds.")
    write_log("Press CTRL+C to stop.")

    while True:
        try:
            run_executor_once()
            write_log(f"Sleeping for {interval} seconds...")
            time.sleep(interval)

        except KeyboardInterrupt:
            write_log("AI executor loop stopped by user.")
            break


if __name__ == "__main__":
    main()