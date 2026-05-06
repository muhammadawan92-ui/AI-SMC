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
    print(line, flush=True)

    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_executor_once():
    cmd = [sys.executable, "-u", "demo_trade_executor.py"]

    write_log("Running demo_trade_executor.py...")

    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(BACKEND_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        start_time = time.time()
        timeout_seconds = 180

        while True:
            line = process.stdout.readline()

            if line:
                write_log(line.rstrip())

            if process.poll() is not None:
                break

            if time.time() - start_time > timeout_seconds:
                process.kill()
                write_log("ERROR: demo_trade_executor.py timed out after 180 seconds.")
                return

        write_log(f"Executor finished with return code: {process.returncode}")

    except Exception as exc:
        write_log(f"ERROR: executor loop failed: {exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Seconds between setup checks. 300 = 5 minutes.",
    )
    args = parser.parse_args()

    interval = max(60, int(args.interval))

    write_log("AI executor loop started.")
    write_log(f"Backend folder: {BACKEND_DIR}")
    write_log(f"Check interval: {interval} seconds.")
    write_log(f"Log file: {LOG_FILE}")
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