import os
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")


def connect_mt5():
    terminal_path = os.getenv("MT5_TERMINAL_PATH", "").strip()

    if terminal_path:
        ok = mt5.initialize(path=terminal_path)
    else:
        ok = mt5.initialize()

    if not ok:
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    account_info = mt5.account_info()
    if account_info is None:
        raise RuntimeError(f"MT5 account info failed: {mt5.last_error()}")

    print("Connected to MT5")
    print("Account:", account_info.login)
    print("Server:", account_info.server)
    print("Balance:", account_info.balance)
    print("Currency:", account_info.currency)


def get_candles(symbol, timeframe, bars=50):
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Could not select symbol {symbol}: {mt5.last_error()}")

    # start_pos=1 ignores the current forming candle and uses closed candles only
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, bars)

    if rates is None:
        raise RuntimeError(f"Could not get candles for {symbol}: {mt5.last_error()}")

    df = pd.DataFrame(rates)

    if df.empty:
        raise RuntimeError(f"No candle data returned for {symbol}")

    df["time"] = pd.to_datetime(df["time"], unit="s")

    return df[["time", "open", "high", "low", "close", "tick_volume", "spread"]]


def main():
    symbol = os.getenv("TRADING_SYMBOL", "GBPUSD")

    connect_mt5()

    timeframes = {
        "H1": mt5.TIMEFRAME_H1,
        "M15": mt5.TIMEFRAME_M15,
        "M5": mt5.TIMEFRAME_M5,
    }

    for name, timeframe in timeframes.items():
        print(f"\n===== {symbol} {name} CLOSED CANDLES =====")
        candles = get_candles(symbol, timeframe, bars=20)
        print(candles.tail(5).to_string(index=False))

    mt5.shutdown()


if __name__ == "__main__":
    main()