import os
from pathlib import Path
from datetime import timedelta

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")


def mt5_common_files_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA environment variable not found.")
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"


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

    print("Connected:", account_info.login, account_info.server)


def get_candles(symbol, timeframe, bars=100):
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Could not select symbol {symbol}: {mt5.last_error()}")

    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, bars)

    if rates is None:
        raise RuntimeError(f"Could not get candles for {symbol}: {mt5.last_error()}")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def fmt_time(dt):
    return pd.Timestamp(dt).strftime("%Y.%m.%d %H:%M")


def main():
    symbol = os.getenv("TRADING_SYMBOL", "GBPUSDm")

    connect_mt5()

    h1 = get_candles(symbol, mt5.TIMEFRAME_H1, 100)
    m15 = get_candles(symbol, mt5.TIMEFRAME_M15, 100)

    swing_top = float(h1["high"].tail(50).max())
    swing_bottom = float(h1["low"].tail(50).min())
    equilibrium = (swing_top + swing_bottom) / 2

    premium_bottom = 0.95 * swing_top + 0.05 * swing_bottom
    discount_top = 0.95 * swing_bottom + 0.05 * swing_top

    left_time = h1["time"].tail(50).iloc[0]
    right_time = h1["time"].iloc[-1] + timedelta(hours=8)

    # Temporary M15 demand OB test:
    # latest bearish candle used as demand test zone.
    bearish_m15 = m15[m15["close"] < m15["open"]].tail(1)

    if bearish_m15.empty:
        ob_time = m15["time"].iloc[-5]
        ob_high = float(m15["high"].iloc[-5])
        ob_low = float(m15["low"].iloc[-5])
    else:
        row = bearish_m15.iloc[0]
        ob_time = row["time"]
        ob_high = float(row["high"])
        ob_low = float(row["low"])

    output_path = mt5_common_files_dir() / "AI_SMC_OVERLAY.csv"

    lines = []

    # Format:
    # TYPE;NAME;TIME1;TIME2;PRICE1;PRICE2;TEXT;COLOR

    lines.append(
        f"RECT;AI_SMC_PREMIUM;{fmt_time(left_time)};{fmt_time(right_time)};"
        f"{swing_top:.5f};{premium_bottom:.5f};Premium Zone;red"
    )

    lines.append(
        f"RECT;AI_SMC_DISCOUNT;{fmt_time(left_time)};{fmt_time(right_time)};"
        f"{discount_top:.5f};{swing_bottom:.5f};Discount Zone;green"
    )

    lines.append(
        f"RECT;AI_SMC_M15_DEMAND;{fmt_time(ob_time)};{fmt_time(right_time)};"
        f"{ob_high:.5f};{ob_low:.5f};M15 Demand OB Test;green"
    )

    lines.append(
        f"HLINE;AI_SMC_EQUILIBRIUM;;;{equilibrium:.5f};;Equilibrium;gray"
    )

    lines.append(
        f"HLINE;AI_SMC_SWING_HIGH;;;{swing_top:.5f};;Swing High;red"
    )

    lines.append(
        f"HLINE;AI_SMC_SWING_LOW;;;{swing_bottom:.5f};;Swing Low;green"
    )

    output_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Overlay file written:")
    print(output_path)

    mt5.shutdown()


if __name__ == "__main__":
    main()