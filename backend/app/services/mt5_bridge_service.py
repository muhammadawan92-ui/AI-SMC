from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

MT5_AVAILABLE = False
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    logger.info("MetaTrader5 package not available — MT5 bridge will use mock/log mode")


class MT5BridgeService:
    def __init__(self) -> None:
        self._connected = False
        self._mock = settings.mock_mode or not MT5_AVAILABLE

    def connect(self, account: int = 0, password: str = "", server: str = "") -> dict:
        if self._mock:
            self._connected = True
            return {"success": True, "mock": True, "account": account or settings.mt5_account}
        if not MT5_AVAILABLE:
            return {"success": False, "error": "MetaTrader5 package not installed"}
        try:
            if not mt5.initialize(
                path=settings.mt5_terminal_path or None,
                login=account or settings.mt5_account,
                password=password or settings.mt5_password,
                server=server or settings.mt5_server,
            ):
                return {"success": False, "error": str(mt5.last_error())}
            self._connected = True
            info = mt5.account_info()
            return {
                "success": True,
                "account": info.login,
                "balance": info.balance,
                "equity": info.equity,
                "server": info.server,
                "currency": info.currency,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def disconnect(self) -> None:
        if MT5_AVAILABLE and self._connected and not self._mock:
            mt5.shutdown()
        self._connected = False

    def get_account_info(self) -> dict:
        if self._mock:
            symbol = _default_mock_symbol()
            return {
                "balance": 10000.00, "equity": 10150.00, "margin": 200.00,
                "free_margin": 9998.50, "profit": 1.50, "leverage": 500,
                "currency": "USD", "server": "MockBroker-Demo",
                "mock": True,
                "mock_symbol": symbol,
                "mock_source": "Synthetic demo values from mt5_bridge_service.py",
            }
        if not self._connected:
            return {"error": "Not connected"}
        info = mt5.account_info()
        return info._asdict() if info else {"error": str(mt5.last_error())}

    def get_open_positions(self) -> list[dict]:
        if self._mock:
            symbol = _default_mock_symbol()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return [
                {
                    "ticket": 12345678,
                    "symbol": symbol,
                    "type": "buy",
                    "volume": 0.01,
                    "open_price": 1.34970 if symbol.upper().startswith("GBPUSD") else 2340.50,
                    "sl": 1.34870 if symbol.upper().startswith("GBPUSD") else 2330.00,
                    "tp": 1.35170 if symbol.upper().startswith("GBPUSD") else 2360.00,
                    # Keep realistic synthetic PnL for 0.01 lots.
                    "profit": 1.50 if symbol.upper().startswith("GBPUSD") else 1.50,
                    "open_time": now,
                }
            ]
        if not self._connected:
            return []
        positions = mt5.positions_get()
        if positions is None:
            return []
        return [p._asdict() for p in positions]

    def get_closed_positions(self, days: int = 30) -> list[dict]:
        if self._mock:
            return _generate_mock_history()
        if not self._connected:
            return []
        from datetime import timedelta
        end = datetime.now()
        start = end - timedelta(days=days)
        deals = mt5.history_deals_get(start, end)
        if deals is None:
            return []
        return [d._asdict() for d in deals]

    def get_symbol_info(self, symbol: str) -> dict:
        if self._mock:
            return {"symbol": symbol, "bid": 2340.50, "ask": 2341.00, "spread": 0.50}
        if not self._connected:
            return {}
        info = mt5.symbol_info_tick(symbol)
        return info._asdict() if info else {}


class MT5LogReader:
    """Reads MT5 Expert and Journal log files from disk (no MT5 connection needed)."""

    def __init__(self, data_dir: Optional[str] = None) -> None:
        self.data_dir = Path(data_dir or settings.mt5_data_dir or "")

    def find_expert_logs(self) -> list[Path]:
        if not self.data_dir.exists():
            return []
        logs_dir = self.data_dir / "MQL5" / "Logs"
        if not logs_dir.exists():
            return []
        return sorted(logs_dir.glob("*.log"), key=os.path.getmtime, reverse=True)[:10]

    def find_journal_logs(self) -> list[Path]:
        if not self.data_dir.exists():
            return []
        journal_dir = self.data_dir / "logs"
        if not journal_dir.exists():
            return []
        return sorted(journal_dir.glob("*.log"), key=os.path.getmtime, reverse=True)[:5]

    def parse_expert_log(self, log_path: Path) -> list[dict]:
        entries: list[dict] = []
        try:
            content = log_path.read_text(encoding="utf-8", errors="ignore")
            for line in content.splitlines():
                parsed = _parse_log_line(line)
                if parsed:
                    entries.append(parsed)
        except Exception as e:
            logger.error("Log parse error: %s", e)
        return entries

    def parse_uploaded_log(self, content: str) -> list[dict]:
        entries: list[dict] = []
        for line in content.splitlines():
            parsed = _parse_log_line(line)
            if parsed:
                entries.append(parsed)
        return entries


def _parse_log_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line:
        return None
    # MT5 log format: YYYY.MM.DD HH:MM:SS.mmm<TAB>level<TAB>message
    parts = line.split("\t", 3)
    if len(parts) >= 3:
        return {
            "timestamp": parts[0].strip(),
            "level": parts[1].strip() if len(parts) > 1 else "info",
            "source": parts[2].strip() if len(parts) > 2 else "",
            "message": parts[3].strip() if len(parts) > 3 else parts[-1].strip(),
            "raw": line,
        }
    # Try space-separated
    if len(line) > 20 and line[4] == "." and line[7] == ".":
        return {
            "timestamp": line[:23].strip() if len(line) > 23 else line[:10],
            "level": "info",
            "source": "expert",
            "message": line[23:].strip() if len(line) > 23 else line,
            "raw": line,
        }
    return {"timestamp": "", "level": "info", "source": "log", "message": line, "raw": line}


def _generate_mock_history() -> list[dict]:
    symbol = _default_mock_symbol()
    now = datetime.now()
    t1 = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    t2 = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    t3 = now.strftime("%Y-%m-%d %H:%M:%S")
    return [
        {
            "ticket": 11111,
            "symbol": symbol,
            "type": "buy",
            "volume": 0.01,
            "open_price": 1.34700 if symbol.upper().startswith("GBPUSD") else 2330.0,
            "close_price": 1.34820 if symbol.upper().startswith("GBPUSD") else 2345.0,
            "profit": 1.20,
            "open_time": t1,
            "close_time": t2,
        },
        {
            "ticket": 11112,
            "symbol": symbol,
            "type": "sell",
            "volume": 0.01,
            "open_price": 1.34900 if symbol.upper().startswith("GBPUSD") else 2360.0,
            "close_price": 1.34820 if symbol.upper().startswith("GBPUSD") else 2355.0,
            "profit": 0.80,
            "open_time": t2,
            "close_time": t3,
        },
        {
            "ticket": 11113,
            "symbol": symbol,
            "type": "buy",
            "volume": 0.01,
            "open_price": 1.35000 if symbol.upper().startswith("GBPUSD") else 2348.0,
            "close_price": 1.34910 if symbol.upper().startswith("GBPUSD") else 2340.0,
            "profit": -0.90,
            "open_time": t2,
            "close_time": t3,
        },
    ]


def _default_mock_symbol() -> str:
    # Prefer the user's configured symbol whitelist first.
    if settings.symbol_whitelist_list:
        return settings.symbol_whitelist_list[0]
    return "GBPUSD"


_bridge: Optional[MT5BridgeService] = None


def get_mt5_bridge() -> MT5BridgeService:
    global _bridge
    if _bridge is None:
        _bridge = MT5BridgeService()
    return _bridge
