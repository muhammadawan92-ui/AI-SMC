"""
Continuously refreshes AI_SMC_OVERLAY.csv for the MT5 AI_SMC_Overlay indicator.
Place this file in your backend folder beside test_smc_overlay.py.
"""
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
SCRIPT = BACKEND_DIR / "test_smc_overlay.py"
REFRESH_SECONDS = 15

print("Starting SMC overlay loop...")
print(f"Backend folder: {BACKEND_DIR}")
print(f"Script: {SCRIPT}")
print(f"Refresh seconds: {REFRESH_SECONDS}")

while True:
    try:
        if not SCRIPT.exists():
            print(f"[{datetime.now()}] Missing file: {SCRIPT}")
        else:
            result = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=str(BACKEND_DIR),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                print(f"[{datetime.now()}] Overlay refreshed OK")
            else:
                print(f"[{datetime.now()}] Overlay refresh failed")
                print(result.stderr or result.stdout)
    except Exception as exc:
        print(f"[{datetime.now()}] Overlay loop error: {exc}")

    time.sleep(REFRESH_SECONDS)
