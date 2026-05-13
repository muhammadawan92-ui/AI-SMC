import os
from pathlib import Path
from datetime import timedelta
import json

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")

BULLISH = 1
BEARISH = -1


def mt5_common_files_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA environment variable not found.")
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"


def fmt_time(dt) -> str:
    return pd.Timestamp(dt).strftime("%Y.%m.%d %H:%M")


def safe_text(text: str) -> str:
    return str(text).replace(";", " | ").replace("\n", " ").strip()


def bias_text(bias: int) -> str:
    if bias == BULLISH:
        return "Bullish"
    if bias == BEARISH:
        return "Bearish"
    return "Neutral"


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def get_visual_mode() -> str:
    mode = os.getenv("SMC_VISUAL_MODE", "clean").strip().lower()
    if mode not in {"clean", "trade", "debug"}:
        return "clean"
    return mode


def get_latest_flip_candidate(candidates: dict):
    valid = [v for v in candidates.values() if v and v.get("ob")]
    if not valid:
        return None
    valid.sort(key=lambda x: x["ob"].get("time"))
    return valid[-1]


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


def get_candles(symbol, timeframe, bars=800) -> pd.DataFrame:
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Could not select symbol {symbol}: {mt5.last_error()}")

    # start_pos=1 ignores current forming candle
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, bars)

    if rates is None:
        raise RuntimeError(f"Could not get candles for {symbol}: {mt5.last_error()}")

    df = pd.DataFrame(rates)

    if df.empty:
        raise RuntimeError(f"No candles returned for {symbol}")

    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def find_pivots(df: pd.DataFrame, length: int):
    highs = []
    lows = []

    if len(df) < length * 2 + 5:
        return highs, lows

    for i in range(length, len(df) - length):
        window = df.iloc[i - length:i + length + 1]

        current_high = float(df.iloc[i]["high"])
        current_low = float(df.iloc[i]["low"])

        if current_high >= float(window["high"].max()):
            highs.append({
                "index": i,
                "confirm_index": i + length,
                "time": df.iloc[i]["time"],
                "price": current_high,
                "crossed": False,
            })

        if current_low <= float(window["low"].min()):
            lows.append({
                "index": i,
                "confirm_index": i + length,
                "time": df.iloc[i]["time"],
                "price": current_low,
                "crossed": False,
            })

    return highs, lows


def find_displacement_index(df: pd.DataFrame, start_index: int, end_index: int, bias: int):
    start_index = max(0, int(start_index))
    end_index = min(len(df) - 1, int(end_index))

    lookback = int(os.getenv("SMC_DISPLACEMENT_LOOKBACK", "30"))
    body_mult = float(os.getenv("SMC_DISPLACEMENT_BODY_MULT", "1.5"))
    range_mult = float(os.getenv("SMC_DISPLACEMENT_RANGE_MULT", "1.2"))
    min_body_ratio = float(os.getenv("SMC_MIN_BODY_TO_RANGE", "0.45"))

    focus_start = max(start_index, end_index - lookback)
    segment = df.iloc[focus_start:end_index + 1].copy()

    if segment.empty:
        return end_index

    bodies = (segment["close"] - segment["open"]).abs()
    ranges = (segment["high"] - segment["low"]).abs()

    avg_body = float(bodies.mean()) if not bodies.empty else 0.0
    avg_range = float(ranges.mean()) if not ranges.empty else 0.0

    candidates = []

    for idx, row in segment.iterrows():
        body = abs(float(row["close"]) - float(row["open"]))
        candle_range = abs(float(row["high"]) - float(row["low"]))

        if candle_range <= 0:
            continue

        body_ratio = body / candle_range

        if bias == BULLISH:
            same_direction = float(row["close"]) > float(row["open"])
        else:
            same_direction = float(row["close"]) < float(row["open"])

        strong_body = avg_body > 0 and body >= avg_body * body_mult
        strong_range = avg_range > 0 and candle_range >= avg_range * range_mult
        clean_body = body_ratio >= min_body_ratio

        if same_direction and clean_body and (strong_body or strong_range):
            candidates.append(idx)

    if candidates:
        return int(candidates[-1])

    return end_index


def _env_bool_core(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def _env_int_core(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return int(default)


def _ob_from_cluster(df: pd.DataFrame, cluster_start: int, chosen_idx: int, bias: int, ob_type: str, displacement_index=None, use_body_only: bool = False):
    cluster_start = int(max(0, cluster_start))
    chosen_idx = int(min(len(df) - 1, chosen_idx))
    cluster = df.loc[cluster_start:chosen_idx]
    if cluster.empty:
        return None

    wick_high = float(cluster["high"].max())
    wick_low = float(cluster["low"].min())

    if use_body_only:
        body_high = float(cluster[["open", "close"]].max(axis=1).max())
        body_low = float(cluster[["open", "close"]].min(axis=1).min())
        zone_high = body_high
        zone_low = body_low
    else:
        zone_high = wick_high
        zone_low = wick_low

    return {
        "type": ob_type,
        "bias": bias,
        "index": int(cluster_start),
        "end_index": int(chosen_idx),
        "displacement_index": int(displacement_index) if displacement_index is not None else int(chosen_idx),
        "time": cluster.iloc[0]["time"],
        "end_time": cluster.iloc[-1]["time"],
        "high": float(zone_high),
        "low": float(zone_low),
        "wick_high": float(wick_high),
        "wick_low": float(wick_low),
        "open": float(cluster.iloc[0]["open"]),
        "close": float(cluster.iloc[-1]["close"]),
        "cluster_count": int(len(cluster)),
        "body_only": bool(use_body_only),
    }


def build_order_block_from_anchor(df: pd.DataFrame, anchor_index: int, bias: int, max_cluster: int = 1, lookback: int = 6, use_body_only: bool = False):
    """
    Builds a refined OB around a swing/rejection anchor.
    For bearish/supply context, it searches backward for the last bullish candle near the swing high.
    For bullish/demand context, it searches backward for the last bearish candle near the swing low.
    This is used for H1 supply/demand context even when H1 has not printed a full BOS/CHoCH yet.
    """
    if df.empty:
        return None

    anchor_index = int(max(0, min(len(df) - 1, anchor_index)))
    search_start = max(0, anchor_index - int(lookback))
    search = df.loc[search_start:anchor_index]
    if search.empty:
        return None

    if bias == BULLISH:
        opposite = search[search["close"] < search["open"]]
        ob_type = "demand"
    else:
        opposite = search[search["close"] > search["open"]]
        ob_type = "supply"

    if opposite.empty:
        # Fallback: use the anchor candle itself if no clean opposite candle exists.
        chosen_idx = anchor_index
    else:
        chosen_idx = int(opposite.index[-1])

    cluster_start = chosen_idx
    cluster_count = 1
    max_cluster = max(1, int(max_cluster))

    while cluster_count < max_cluster:
        previous_idx = cluster_start - 1
        if previous_idx < search_start:
            break
        prev = df.loc[previous_idx]
        is_opposite = (
            float(prev["close"]) < float(prev["open"])
            if bias == BULLISH
            else float(prev["close"]) > float(prev["open"])
        )
        if not is_opposite:
            break
        cluster_start = previous_idx
        cluster_count += 1

    return _ob_from_cluster(
        df,
        cluster_start=cluster_start,
        chosen_idx=chosen_idx,
        bias=bias,
        ob_type=ob_type,
        displacement_index=anchor_index,
        use_body_only=use_body_only,
    )


def find_order_block(df: pd.DataFrame, start_index: int, end_index: int, bias: int, max_cluster: int | None = None, use_body_only: bool | None = None):
    start_index = max(0, int(start_index))
    end_index = min(len(df) - 1, int(end_index))
    if end_index <= start_index:
        return None

    displacement_index = find_displacement_index(df, start_index, end_index, bias)
    ob_lookback = _env_int_core("SMC_OB_LOOKBACK", 20)
    if max_cluster is None:
        max_cluster = _env_int_core("SMC_OB_MAX_CLUSTER", 3)
    if use_body_only is None:
        use_body_only = _env_bool_core("SMC_OB_USE_BODY_ONLY", False)

    search_start = max(start_index, displacement_index - ob_lookback)
    search = df.iloc[search_start:displacement_index].copy()
    if search.empty:
        return None

    if bias == BULLISH:
        opposite = search[search["close"] < search["open"]]
        ob_type = "demand"
    else:
        opposite = search[search["close"] > search["open"]]
        ob_type = "supply"

    if opposite.empty:
        return None

    chosen_idx = int(opposite.index[-1])
    cluster_start = chosen_idx
    cluster_count = 1
    max_cluster = max(1, int(max_cluster))

    while cluster_count < max_cluster:
        previous_idx = cluster_start - 1
        if previous_idx < search_start:
            break
        prev = df.loc[previous_idx]
        is_opposite = (
            float(prev["close"]) < float(prev["open"])
            if bias == BULLISH
            else float(prev["close"]) > float(prev["open"])
        )
        if not is_opposite:
            break
        cluster_start = previous_idx
        cluster_count += 1

    return _ob_from_cluster(
        df,
        cluster_start=cluster_start,
        chosen_idx=chosen_idx,
        bias=bias,
        ob_type=ob_type,
        displacement_index=displacement_index,
        use_body_only=bool(use_body_only),
    )


def detect_structure(df: pd.DataFrame, length: int):
    pivot_highs, pivot_lows = find_pivots(df, length)

    pivot_highs_by_confirm = {}
    pivot_lows_by_confirm = {}

    for p in pivot_highs:
        pivot_highs_by_confirm.setdefault(p["confirm_index"], []).append(p)

    for p in pivot_lows:
        pivot_lows_by_confirm.setdefault(p["confirm_index"], []).append(p)

    current_high_pivot = None
    current_low_pivot = None
    trend = 0
    events = []

    for i in range(len(df)):
        if i in pivot_highs_by_confirm:
            current_high_pivot = pivot_highs_by_confirm[i][-1]

        if i in pivot_lows_by_confirm:
            current_low_pivot = pivot_lows_by_confirm[i][-1]

        close_price = float(df.iloc[i]["close"])
        bar_time = df.iloc[i]["time"]

        if current_high_pivot and not current_high_pivot["crossed"]:
            if close_price > current_high_pivot["price"]:
                tag = "CHoCH" if trend == BEARISH else "BOS"
                ob = find_order_block(df, current_high_pivot["index"], i, BULLISH)

                events.append({
                    "direction": "bullish",
                    "bias": BULLISH,
                    "tag": tag,
                    "break_index": i,
                    "break_time": bar_time,
                    "level_index": current_high_pivot["index"],
                    "level_time": current_high_pivot["time"],
                    "level": current_high_pivot["price"],
                    "break_close": close_price,
                    "order_block": ob,
                })

                current_high_pivot["crossed"] = True
                trend = BULLISH

        if current_low_pivot and not current_low_pivot["crossed"]:
            if close_price < current_low_pivot["price"]:
                tag = "CHoCH" if trend == BULLISH else "BOS"
                ob = find_order_block(df, current_low_pivot["index"], i, BEARISH)

                events.append({
                    "direction": "bearish",
                    "bias": BEARISH,
                    "tag": tag,
                    "break_index": i,
                    "break_time": bar_time,
                    "level_index": current_low_pivot["index"],
                    "level_time": current_low_pivot["time"],
                    "level": current_low_pivot["price"],
                    "break_close": close_price,
                    "order_block": ob,
                })

                current_low_pivot["crossed"] = True
                trend = BEARISH

    return {
        "trend": trend,
        "events": events,
        "pivot_highs": pivot_highs,
        "pivot_lows": pivot_lows,
    }


def last_event(events, bias=None):
    if bias is None:
        return events[-1] if events else None

    filtered = [e for e in events if e["bias"] == bias]
    return filtered[-1] if filtered else None


def choose_internal_event(m15_result, m5_result, m15: pd.DataFrame, m5: pd.DataFrame):
    m15_last = last_event(m15_result["events"])
    m5_last = last_event(m5_result["events"])

    max_m15_age = int(os.getenv("SMC_M15_EVENT_MAX_AGE_BARS", "120"))
    max_m5_age = int(os.getenv("SMC_M5_EVENT_MAX_AGE_BARS", "180"))

    candidates = []

    if m15_last:
        m15_age = len(m15) - 1 - int(m15_last["break_index"])
        if m15_age <= max_m15_age:
            candidates.append({
                "timeframe": "M15",
                "event": m15_last,
                "age": m15_age,
            })

    if m5_last:
        m5_age = len(m5) - 1 - int(m5_last["break_index"])
        if m5_age <= max_m5_age:
            candidates.append({
                "timeframe": "M5",
                "event": m5_last,
                "age": m5_age,
            })

    if not candidates:
        if m5_last:
            return {"timeframe": "M5", "event": m5_last, "age": None}
        if m15_last:
            return {"timeframe": "M15", "event": m15_last, "age": None}
        return None

    candidates.sort(key=lambda x: x["event"]["break_time"])
    return candidates[-1]


def is_ob_invalidated(df: pd.DataFrame, ob: dict, use_close: bool = True) -> bool:
    if not ob:
        return False

    start_idx = int(ob.get("end_index", ob["index"])) + 1

    if start_idx >= len(df):
        return False

    after = df.iloc[start_idx:]

    if after.empty:
        return False

    if ob["type"] == "demand":
        if use_close:
            return bool((after["close"] < ob["low"]).any())
        return bool((after["low"] < ob["low"]).any())

    if ob["type"] == "supply":
        if use_close:
            return bool((after["close"] > ob["high"]).any())
        return bool((after["high"] > ob["high"]).any())

    return False


def ob_overlaps_zone(ob: dict, zone_low: float, zone_high: float) -> bool:
    if not ob:
        return False

    low = min(zone_low, zone_high)
    high = max(zone_low, zone_high)

    return not (ob["low"] > high or ob["high"] < low)


def find_rejection_order_block(df: pd.DataFrame, structure_result: dict, bias: int, timeframe_label: str = "H1"):
    """
    Detects H1 supply/demand from the latest confirmed H1 swing/rejection area.
    This is intentionally NOT dependent on a full H1 BOS/CHoCH, because H1 supply can reject price
    before H1 structure flips bearish. It solves the missing H1 SUPPLY OB visual problem.
    """
    if df is None or df.empty or not structure_result:
        return None

    tf = timeframe_label.upper()
    max_age = _env_int_core(f"SMC_{tf}_OB_MAX_AGE_BARS", _env_int_core("SMC_H1_OB_MAX_AGE_BARS", 240))
    anchor_lookback = _env_int_core(f"SMC_{tf}_OB_ANCHOR_LOOKBACK", _env_int_core("SMC_H1_OB_ANCHOR_LOOKBACK", 6))
    max_cluster = _env_int_core(f"SMC_{tf}_OB_MAX_CLUSTER", _env_int_core("SMC_H1_OB_MAX_CLUSTER", 1))
    use_body_only = _env_bool_core(f"SMC_{tf}_OB_USE_BODY_ONLY", _env_bool_core("SMC_H1_OB_USE_BODY_ONLY", False))

    pivots = structure_result.get("pivot_highs", []) if bias == BEARISH else structure_result.get("pivot_lows", [])
    latest_index = len(df) - 1

    def _candidate_from_anchor(anchor_index: int, source: str, pivot: dict | None = None):
        ob = build_order_block_from_anchor(
            df,
            anchor_index=anchor_index,
            bias=bias,
            max_cluster=max_cluster,
            lookback=anchor_lookback,
            use_body_only=use_body_only,
        )
        if not ob:
            return None
        if is_ob_invalidated(df, ob, use_close=True):
            return None
        clean = dict(ob)
        clean["timeframe"] = timeframe_label
        clean["source"] = source
        clean["h1_rejection_ob"] = True
        if pivot:
            clean["pivot_time"] = pivot.get("time")
            clean["pivot_price"] = pivot.get("price")
            clean["pivot_index"] = pivot.get("index")
        return clean

    # Primary: latest confirmed pivot high/low.
    for pivot in reversed(pivots):
        pivot_index = int(pivot.get("index", 0))
        if latest_index - pivot_index > max_age:
            continue
        candidate = _candidate_from_anchor(pivot_index, f"{timeframe_label.lower()}_rejection_pivot", pivot)
        if candidate:
            return candidate

    # Fallback: recent extreme, useful before a pivot is fully confirmed.
    fallback_bars = _env_int_core(f"SMC_{tf}_OB_RECENT_LOOKBACK", _env_int_core("SMC_H1_OB_RECENT_LOOKBACK", 120))
    tail = df.tail(max(10, fallback_bars))
    if tail.empty:
        return None
    anchor_index = int(tail["high"].idxmax()) if bias == BEARISH else int(tail["low"].idxmin())
    return _candidate_from_anchor(anchor_index, f"{timeframe_label.lower()}_recent_extreme", None)



def last_valid_ob(events, df: pd.DataFrame, bias: int, timeframe_label: str, zone_low=None, zone_high=None):
    for event in reversed(events):
        if event["bias"] != bias:
            continue

        ob = event.get("order_block")
        if not ob:
            continue

        if is_ob_invalidated(df, ob, use_close=True):
            continue

        if zone_low is not None and zone_high is not None:
            if not ob_overlaps_zone(ob, zone_low, zone_high):
                continue

        clean_ob = dict(ob)
        clean_ob["timeframe"] = timeframe_label
        clean_ob["source_event"] = {
            "direction": event["direction"],
            "tag": event["tag"],
            "level": event["level"],
            "level_time": event["level_time"],
            "break_time": event["break_time"],
        }
        return clean_ob

    return None


def most_recent_ob(*obs):
    valid = [ob for ob in obs if ob]
    if not valid:
        return None
    valid.sort(key=lambda x: x["time"])
    return valid[-1]



def price_inside_ob(price: float, ob: dict | None) -> bool:
    if not ob:
        return False
    return float(ob["low"]) <= float(price) <= float(ob["high"])


def get_h1_context_ob(trade_direction, trade_mode: str, current_price: float, h1_supply_ob: dict | None, h1_demand_ob: dict | None):
    """
    H1 OB context has priority over simple premium/discount for retracement refinement.
    Sell retracement: only H1 supply is relevant.
    Buy retracement: only H1 demand is relevant.
    """
    if not env_bool("SMC_H1_OB_REFINEMENT_ENABLED", True):
        return None

    if trade_mode != "retracement":
        return None

    require_price_inside = env_bool("SMC_H1_OB_REQUIRE_PRICE_INSIDE", True)

    if trade_direction == BEARISH and h1_supply_ob:
        if not require_price_inside or price_inside_ob(current_price, h1_supply_ob):
            return h1_supply_ob

    if trade_direction == BULLISH and h1_demand_ob:
        if not require_price_inside or price_inside_ob(current_price, h1_demand_ob):
            return h1_demand_ob

    return None


def apply_h1_retrace_stop_for_visuals(trade_direction, trade_mode: str, entry: float, normal_stop: float, selected_ob: dict | None, rr: float, buffer_price: float = 0.0):
    """
    Visual SL/TP preview only. demo_trade_executor.py has the execution copy.
    Inside H1 OB, SL can be protected by H1 50% or H1 extreme.
    Outside H1 OB, normal LTF OB SL stays unchanged.
    """
    if not selected_ob or not env_bool("SMC_H1_OB_RETRACE_SL_ENABLED", True):
        risk = (entry - normal_stop) if trade_direction == BULLISH else (normal_stop - entry)
        tp = entry + risk * rr if trade_direction == BULLISH else entry - risk * rr
        return normal_stop, tp, "ltf_ob"

    if trade_mode != "retracement":
        risk = (entry - normal_stop) if trade_direction == BULLISH else (normal_stop - entry)
        tp = entry + risk * rr if trade_direction == BULLISH else entry - risk * rr
        return normal_stop, tp, "ltf_ob"

    h1_ob = selected_ob.get("h1_context_ob")
    if not h1_ob:
        risk = (entry - normal_stop) if trade_direction == BULLISH else (normal_stop - entry)
        tp = entry + risk * rr if trade_direction == BULLISH else entry - risk * rr
        return normal_stop, tp, "ltf_ob"

    mode = os.getenv("SMC_H1_OB_RETRACE_SL_MODE", "midpoint").strip().lower()
    if mode not in {"auto", "midpoint", "extreme", "off"}:
        mode = "midpoint"
    if mode == "off":
        risk = (entry - normal_stop) if trade_direction == BULLISH else (normal_stop - entry)
        tp = entry + risk * rr if trade_direction == BULLISH else entry - risk * rr
        return normal_stop, tp, "ltf_ob"

    h1_high = float(h1_ob["high"])
    h1_low = float(h1_ob["low"])
    h1_mid = (h1_high + h1_low) / 2.0

    if trade_direction == BEARISH:
        if mode == "extreme":
            stop = h1_high + buffer_price
            source = "h1_supply_extreme"
        else:
            stop = max(float(normal_stop), h1_mid)
            source = "h1_supply_midpoint_protected"
        risk = stop - entry
        tp = entry - risk * rr
        return stop, tp, source

    if trade_direction == BULLISH:
        if mode == "extreme":
            stop = h1_low - buffer_price
            source = "h1_demand_extreme"
        else:
            stop = min(float(normal_stop), h1_mid)
            source = "h1_demand_midpoint_protected"
        risk = entry - stop
        tp = entry + risk * rr
        return stop, tp, source

    risk = abs(entry - normal_stop)
    tp = entry
    return normal_stop, tp, "ltf_ob"


def add_rect(lines, name, time1, time2, top, bottom, text, color):
    lines.append(
        f"RECT;{name};{fmt_time(time1)};{fmt_time(time2)};"
        f"{top:.5f};{bottom:.5f};{safe_text(text)};{color}"
    )


def add_line(lines, name, time1, time2, price1, price2, text, color):
    lines.append(
        f"LINE;{name};{fmt_time(time1)};{fmt_time(time2)};"
        f"{price1:.5f};{price2:.5f};{safe_text(text)};{color}"
    )


def add_text(lines, name, time1, price, text, color):
    lines.append(
        f"TEXT;{name};{fmt_time(time1)};;{price:.5f};;{safe_text(text)};{color}"
    )


def add_label(lines, name, x, y, text, color):
    lines.append(
        f"LABEL;{name};;;{int(x)};{int(y)};{safe_text(text)};{color}"
    )


def choose_h1_swing_range(h1: pd.DataFrame, h1_result: dict, h1_last_event: dict | None):
    if not h1_last_event:
        tail = h1.tail(120)
        high_idx = tail["high"].idxmax()
        low_idx = tail["low"].idxmin()

        return {
            "swing_high": float(h1.loc[high_idx]["high"]),
            "swing_high_time": h1.loc[high_idx]["time"],
            "swing_high_index": int(high_idx),
            "swing_low": float(h1.loc[low_idx]["low"]),
            "swing_low_time": h1.loc[low_idx]["time"],
            "swing_low_index": int(low_idx),
        }

    break_index = int(h1_last_event["break_index"])

    if h1_last_event["bias"] == BULLISH:
        candidate_lows = [p for p in h1_result["pivot_lows"] if p["index"] < break_index]

        if candidate_lows:
            low_pivot = candidate_lows[-1]
            low_index = int(low_pivot["index"])
        else:
            low_index = int(h1.iloc[max(0, break_index - 120):break_index + 1]["low"].idxmin())

        segment = h1.iloc[low_index:]
        high_index = int(segment["high"].idxmax())

        return {
            "swing_high": float(h1.loc[high_index]["high"]),
            "swing_high_time": h1.loc[high_index]["time"],
            "swing_high_index": high_index,
            "swing_low": float(h1.loc[low_index]["low"]),
            "swing_low_time": h1.loc[low_index]["time"],
            "swing_low_index": low_index,
        }

    candidate_highs = [p for p in h1_result["pivot_highs"] if p["index"] < break_index]

    if candidate_highs:
        high_pivot = candidate_highs[-1]
        high_index = int(high_pivot["index"])
    else:
        high_index = int(h1.iloc[max(0, break_index - 120):break_index + 1]["high"].idxmax())

    segment = h1.iloc[high_index:]
    low_index = int(segment["low"].idxmin())

    return {
        "swing_high": float(h1.loc[high_index]["high"]),
        "swing_high_time": h1.loc[high_index]["time"],
        "swing_high_index": high_index,
        "swing_low": float(h1.loc[low_index]["low"]),
        "swing_low_time": h1.loc[low_index]["time"],
        "swing_low_index": low_index,
    }


def fib_prices(swing_low: float, swing_high: float, bias: int):
    levels = [0.5, 0.618, 0.705, 0.79, 0.886]
    rng = swing_high - swing_low
    result = {}

    for level in levels:
        if bias == BEARISH:
            price = swing_low + rng * level
        else:
            price = swing_high - rng * level
        result[level] = float(price)

    return result


def price_location(price: float, swing_low: float, swing_high: float):
    """
    SMC premium/discount should not be treated as simply above/below 50%.
    Default interpretation:
      below 38.2% = discount
      38.2% to 61.8% = equilibrium / fair value area
      above 61.8% = premium
    """
    rng = swing_high - swing_low
    if rng <= 0:
        return "equilibrium"

    pos = (float(price) - float(swing_low)) / float(rng)
    premium_start = float(os.getenv("SMC_PD_PREMIUM_START", "0.618"))
    discount_start = float(os.getenv("SMC_PD_DISCOUNT_START", "0.382"))

    if pos >= premium_start:
        return "premium"
    if pos <= discount_start:
        return "discount"
    return "equilibrium"


def pd_range_detail(price: float, swing_low: float, swing_high: float):
    rng = swing_high - swing_low
    if rng <= 0:
        return {
            "pd_position": 0.5,
            "pd_label": "equilibrium",
            "equilibrium": (swing_high + swing_low) / 2.0,
            "premium_start_price": None,
            "discount_start_price": None,
        }

    pos = (float(price) - float(swing_low)) / float(rng)
    premium_start = float(os.getenv("SMC_PD_PREMIUM_START", "0.618"))
    deep_premium = float(os.getenv("SMC_PD_DEEP_PREMIUM", "0.705"))
    extreme_premium = float(os.getenv("SMC_PD_EXTREME_PREMIUM", "0.886"))
    discount_start = float(os.getenv("SMC_PD_DISCOUNT_START", "0.382"))
    deep_discount = float(os.getenv("SMC_PD_DEEP_DISCOUNT", "0.295"))
    extreme_discount = float(os.getenv("SMC_PD_EXTREME_DISCOUNT", "0.114"))

    if pos >= extreme_premium:
        label = "extreme_premium"
    elif pos >= deep_premium:
        label = "deep_premium"
    elif pos >= premium_start:
        label = "true_premium"
    elif pos > 0.5:
        label = "above_EQ_not_true_premium"
    elif pos <= extreme_discount:
        label = "extreme_discount"
    elif pos <= deep_discount:
        label = "deep_discount"
    elif pos <= discount_start:
        label = "true_discount"
    elif pos < 0.5:
        label = "below_EQ_not_true_discount"
    else:
        label = "equilibrium"

    return {
        "pd_position": float(pos),
        "pd_label": label,
        "equilibrium": float((swing_high + swing_low) / 2.0),
        "premium_start_price": float(swing_low + rng * premium_start),
        "deep_premium_price": float(swing_low + rng * deep_premium),
        "extreme_premium_price": float(swing_low + rng * extreme_premium),
        "discount_start_price": float(swing_low + rng * discount_start),
        "deep_discount_price": float(swing_low + rng * deep_discount),
        "extreme_discount_price": float(swing_low + rng * extreme_discount),
    }


def decide_trade_context(external_bias: int, current_location: str, internal_event_pack, swing_low, swing_high):
    if not internal_event_pack:
        if external_bias == BULLISH:
            return "WAIT_INTERNAL_CONFIRMATION_BUY", None, "none"
        if external_bias == BEARISH:
            return "WAIT_INTERNAL_CONFIRMATION_SELL", None, "none"
        return "NO_TRADE", None, "none"

    internal_event = internal_event_pack["event"]
    internal_bias = internal_event["bias"]
    internal_break_location = price_location(
        float(internal_event["break_close"]),
        swing_low,
        swing_high,
    )

    if external_bias == BULLISH:
        if internal_bias == BEARISH and (
            internal_break_location == "premium" or current_location == "premium"
        ):
            return "SELL_RETRACEMENT", BEARISH, "retracement"

        if internal_bias == BULLISH and current_location in ["discount", "equilibrium"]:
            return "BUY_CONTINUATION", BULLISH, "continuation"

        if current_location == "premium":
            return "WAIT_BEARISH_INTERNAL_SHIFT", None, "none"

        return "WAIT_BULLISH_INTERNAL_SHIFT_AT_DISCOUNT", None, "none"

    if external_bias == BEARISH:
        if internal_bias == BULLISH and (
            internal_break_location == "discount" or current_location == "discount"
        ):
            return "BUY_RETRACEMENT", BULLISH, "retracement"

        if internal_bias == BEARISH and current_location in ["premium", "equilibrium"]:
            return "SELL_CONTINUATION", BEARISH, "continuation"

        if current_location == "discount":
            return "WAIT_BULLISH_INTERNAL_SHIFT", None, "none"

        return "WAIT_BEARISH_INTERNAL_SHIFT_AT_PREMIUM", None, "none"

    return "NO_TRADE", None, "none"




# ---------------------------------------------------------------------------
# FIB-CONFIRMED LTF OB SELECTION
# ---------------------------------------------------------------------------
# Manual rule implemented here:
#   1) Price reacts from a valid H1 supply/demand context.
#   2) M15/M5 prints the confirming CHoCH/BOS.
#   3) Draw fib on that confirming LTF impulse.
#   4) For sells, confirm supply OB only when the opposite bullish candle sits
#      inside the impulse premium retracement band (default 0.618-0.886).
#   5) For buys, confirm demand OB only when the opposite bearish candle sits
#      inside the impulse discount retracement band (default 0.618-0.886).
# This prevents the EA from choosing a low-quality lower OB just because it is
# the most recent candle before displacement.

def _env_float_core(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return float(default)


def _candle_body_bounds(row):
    return min(float(row["open"]), float(row["close"])), max(float(row["open"]), float(row["close"]))


def _price_ranges_overlap(low_a: float, high_a: float, low_b: float, high_b: float) -> bool:
    a_low, a_high = min(low_a, high_a), max(low_a, high_a)
    b_low, b_high = min(low_b, high_b), max(low_b, high_b)
    return not (a_high < b_low or a_low > b_high)


def build_fib_confirmed_ob_from_event(
    df: pd.DataFrame,
    event: dict,
    bias: int,
    timeframe_label: str,
    zone_low=None,
    zone_high=None,
):
    """
    Builds the OB the same way the user manually validates it with fib:
    - bearish setup: fib from LTF impulse high to impulse low, then choose bullish candle(s)
      in the 0.618-0.886 premium retracement band as supply.
    - bullish setup: fib from LTF impulse low to impulse high, then choose bearish candle(s)
      in the 0.618-0.886 discount retracement band as demand.
    """
    if df is None or df.empty or not event:
        return None

    try:
        break_index = int(event.get("break_index"))
        level_index = int(event.get("level_index", break_index))
    except Exception:
        return None

    if break_index <= 1 or break_index >= len(df):
        return None

    pre_event_lookback = _env_int_core("SMC_FIB_OB_PRE_EVENT_LOOKBACK", 12)
    start = max(0, min(level_index, break_index) - pre_event_lookback)
    end = min(len(df) - 1, break_index)
    segment = df.loc[start:end]
    if segment.empty or len(segment) < 3:
        return None

    fib_min = _env_float_core("SMC_FIB_OB_LEVEL_MIN", 0.618)
    fib_max = _env_float_core("SMC_FIB_OB_LEVEL_MAX", 0.886)
    fib_min, fib_max = min(fib_min, fib_max), max(fib_min, fib_max)
    fib_min = max(0.0, min(1.0, fib_min))
    fib_max = max(0.0, min(1.0, fib_max))

    use_body_overlap = _env_bool_core("SMC_FIB_OB_USE_BODY_OVERLAP", False)
    max_cluster = _env_int_core("SMC_FIB_OB_MAX_CLUSTER", _env_int_core("SMC_OB_MAX_CLUSTER", 1))
    use_body_only = _env_bool_core("SMC_FIB_OB_USE_BODY_ONLY", _env_bool_core("SMC_OB_USE_BODY_ONLY", False))
    selection_mode = os.getenv("SMC_FIB_OB_SELECTION", "closest_to_extreme").strip().lower()

    if bias == BEARISH:
        # Sell impulse: source high -> displacement/structure-break low.
        impulse_high_idx = int(segment["high"].idxmax())
        impulse_tail = df.loc[impulse_high_idx:end]
        if impulse_tail.empty:
            return None
        impulse_low_idx = int(impulse_tail["low"].idxmin())
        impulse_high = float(df.loc[impulse_high_idx]["high"])
        impulse_low = float(df.loc[impulse_low_idx]["low"])
        if impulse_high <= impulse_low or impulse_low_idx <= impulse_high_idx:
            return None

        rng = impulse_high - impulse_low
        fib_zone_low = impulse_low + rng * fib_min
        fib_zone_high = impulse_low + rng * fib_max
        search = df.loc[impulse_high_idx:impulse_low_idx]
        opposite_mask = search["close"] > search["open"]
        ob_type = "supply"
    else:
        # Buy impulse: source low -> displacement/structure-break high.
        impulse_low_idx = int(segment["low"].idxmin())
        impulse_tail = df.loc[impulse_low_idx:end]
        if impulse_tail.empty:
            return None
        impulse_high_idx = int(impulse_tail["high"].idxmax())
        impulse_low = float(df.loc[impulse_low_idx]["low"])
        impulse_high = float(df.loc[impulse_high_idx]["high"])
        if impulse_high <= impulse_low or impulse_high_idx <= impulse_low_idx:
            return None

        rng = impulse_high - impulse_low
        fib_zone_low = impulse_high - rng * fib_max
        fib_zone_high = impulse_high - rng * fib_min
        search = df.loc[impulse_low_idx:impulse_high_idx]
        opposite_mask = search["close"] < search["open"]
        ob_type = "demand"

    if search.empty:
        return None

    candidates = []
    for idx, row in search[opposite_mask].iterrows():
        wick_low = float(row["low"])
        wick_high = float(row["high"])
        body_low, body_high = _candle_body_bounds(row)
        check_low, check_high = (body_low, body_high) if use_body_overlap else (wick_low, wick_high)

        if not _price_ranges_overlap(check_low, check_high, fib_zone_low, fib_zone_high):
            continue

        # Optional extra filter: the candidate OB must also overlap the active H1 refinement zone.
        if zone_low is not None and zone_high is not None:
            if not _price_ranges_overlap(wick_low, wick_high, float(zone_low), float(zone_high)):
                continue

        candidates.append(int(idx))

    if not candidates:
        return None

    if selection_mode == "most_recent":
        chosen_idx = candidates[-1]
    elif selection_mode == "largest_body":
        chosen_idx = max(
            candidates,
            key=lambda i: abs(float(df.loc[i]["close"]) - float(df.loc[i]["open"])),
        )
    else:
        # closest_to_extreme: supply from highest qualified candle; demand from lowest qualified candle.
        if bias == BEARISH:
            chosen_idx = max(candidates, key=lambda i: (float(df.loc[i]["high"]), i))
        else:
            chosen_idx = min(candidates, key=lambda i: (float(df.loc[i]["low"]), -i))

    # Optional cluster only with adjacent opposite candles, kept small by default.
    cluster_start = chosen_idx
    cluster_count = 1
    max_cluster = max(1, int(max_cluster))
    while cluster_count < max_cluster:
        prev_idx = cluster_start - 1
        if prev_idx < int(search.index.min()):
            break
        prev = df.loc[prev_idx]
        is_opposite = (float(prev["close"]) > float(prev["open"])) if bias == BEARISH else (float(prev["close"]) < float(prev["open"]))
        if not is_opposite:
            break
        cluster_start = prev_idx
        cluster_count += 1

    ob = _ob_from_cluster(
        df,
        cluster_start=cluster_start,
        chosen_idx=chosen_idx,
        bias=bias,
        ob_type=ob_type,
        displacement_index=break_index,
        use_body_only=use_body_only,
    )
    if not ob:
        return None

    if is_ob_invalidated(df, ob, use_close=True):
        return None

    ob = dict(ob)
    ob["timeframe"] = timeframe_label
    ob["fib_confirmed"] = True
    ob["fib_ob_method"] = "manual_impulse_premium_discount"
    ob["fib_zone_low"] = float(min(fib_zone_low, fib_zone_high))
    ob["fib_zone_high"] = float(max(fib_zone_low, fib_zone_high))
    ob["fib_level_min"] = float(fib_min)
    ob["fib_level_max"] = float(fib_max)
    ob["impulse_high"] = float(impulse_high)
    ob["impulse_low"] = float(impulse_low)
    ob["impulse_high_idx"] = int(impulse_high_idx)
    ob["impulse_low_idx"] = int(impulse_low_idx)
    ob["source_event"] = {
        "direction": event.get("direction"),
        "tag": event.get("tag"),
        "level": event.get("level"),
        "level_time": event.get("level_time"),
        "break_time": event.get("break_time"),
        "break_close": event.get("break_close"),
    }
    return ob


def last_fib_confirmed_ob(events, df: pd.DataFrame, bias: int, timeframe_label: str, zone_low=None, zone_high=None):
    """Return the most recent valid fib-confirmed OB for the requested direction."""
    if not events:
        return None

    lookback_events = _env_int_core("SMC_FIB_OB_LOOKBACK_EVENTS", 6)
    checked = 0
    for event in reversed(events):
        if event.get("bias") != bias:
            continue
        checked += 1
        ob = build_fib_confirmed_ob_from_event(
            df,
            event,
            bias,
            timeframe_label,
            zone_low=zone_low,
            zone_high=zone_high,
        )
        if ob:
            return ob
        if checked >= lookback_events:
            break
    return None

def select_active_ob(
    trade_direction,
    trade_mode,
    internal_event_pack,
    m15_result,
    m5_result,
    m15,
    m5,
    swing_low,
    swing_high,
    equilibrium,
    fibs,
    h1_supply_ob=None,
    h1_demand_ob=None,
    current_price=None,
):
    if trade_direction is None:
        return None, None, None, None, "none", False

    poi_top = max(fibs[0.618], fibs[0.886])
    poi_bottom = min(fibs[0.618], fibs[0.886])

    premium_low = equilibrium
    premium_high = swing_high

    discount_low = swing_low
    discount_high = equilibrium

    zone_name = "none"
    h1_context_ob = get_h1_context_ob(
        trade_direction,
        trade_mode,
        float(current_price) if current_price is not None else float(equilibrium),
        h1_supply_ob,
        h1_demand_ob,
    )

    if h1_context_ob:
        # This is the key change: inside H1 OB, refine the trade using the H1 OB,
        # not the broad premium/discount half of the swing.
        zone_low = float(h1_context_ob["low"])
        zone_high = float(h1_context_ob["high"])
        zone_name = "h1_supply_refinement_zone" if trade_direction == BEARISH else "h1_demand_refinement_zone"
    elif trade_mode == "continuation":
        zone_low = poi_bottom
        zone_high = poi_top
        zone_name = "external_fib_poi"
    else:
        if trade_direction == BEARISH:
            zone_low = premium_low
            zone_high = premium_high
            zone_name = "premium_retracement_zone"
        else:
            zone_low = discount_low
            zone_high = discount_high
            zone_name = "discount_retracement_zone"

    selected_ob_source = "fallback_last_valid"
    selected_ob_locked = False

    m15_ob = last_valid_ob(
        m15_result["events"], m15, trade_direction, "M15",
        zone_low=zone_low, zone_high=zone_high
    )
    m5_ob = last_valid_ob(
        m5_result["events"], m5, trade_direction, "M5",
        zone_low=zone_low, zone_high=zone_high
    )

    fib_selection_enabled = env_bool("SMC_FIB_CONFIRMED_OB_ENABLED", True)
    fib_require_for_execution = env_bool("SMC_FIB_CONFIRMED_OB_REQUIRE_FOR_EXECUTION", True)
    m15_fib_ob = None
    m5_fib_ob = None

    if fib_selection_enabled:
        m15_fib_ob = last_fib_confirmed_ob(
            m15_result["events"], m15, trade_direction, "M15",
            zone_low=zone_low, zone_high=zone_high
        )
        m5_fib_ob = last_fib_confirmed_ob(
            m5_result["events"], m5, trade_direction, "M5",
            zone_low=zone_low, zone_high=zone_high
        )

        # If a fib-confirmed OB exists, it replaces the generic OB candidate.
        # If none exists and the guard is enabled, no executable zone is allowed.
        if m15_fib_ob:
            m15_ob = m15_fib_ob
        if m5_fib_ob:
            m5_ob = m5_fib_ob
        if fib_require_for_execution and not (m15_fib_ob or m5_fib_ob):
            return None, m15_ob, m5_ob, zone_name, "fib_confirmed_ob_missing", False

    selected_ob = None

    # Inside H1 supply/demand, M15 refinement must be checked before M5.
    # This prevents the EA from choosing the lower M5 OB when the cleaner M15 OB
    # is the one that should protect the trade from normal H1-zone noise.
    if h1_context_ob and env_bool("SMC_H1_OB_M15_FIRST", True):
        selected_ob = m15_ob if m15_ob else m5_ob
        selected_ob_source = "h1_context_m15_first" if selected_ob else "h1_context_no_ltf_ob"
        selected_ob_locked = bool(selected_ob)
    else:
        # Prefer the source OB that created the selected internal break event only outside H1 OB context.
        source_selected_ob = None
        if internal_event_pack and internal_event_pack.get("event"):
            source_event = internal_event_pack["event"]
            source_ob = source_event.get("order_block")
            source_tf = internal_event_pack.get("timeframe")
            source_df = m15 if source_tf == "M15" else m5
            if source_ob:
                source_ok = (
                    source_ob.get("bias") == trade_direction
                    and not is_ob_invalidated(source_df, source_ob, use_close=True)
                    and ob_overlaps_zone(source_ob, zone_low, zone_high)
                )
                if source_ok:
                    source_selected_ob = dict(source_ob)
                    source_selected_ob["timeframe"] = source_tf
                    source_selected_ob["source_selected_ob"] = True
                    source_selected_ob["source_event"] = {
                        "direction": source_event.get("direction"),
                        "tag": source_event.get("tag"),
                        "level": source_event.get("level"),
                        "level_time": source_event.get("level_time"),
                        "break_time": source_event.get("break_time"),
                    }
                    selected_ob_source = "internal_event_source"
                    selected_ob_locked = True

        if fib_selection_enabled and (m15_fib_ob or m5_fib_ob):
            # Manual fib-confirmed OB has priority over generic source OB.
            selected_ob = m15_fib_ob if (h1_context_ob and env_bool("SMC_H1_OB_M15_FIRST", True) and m15_fib_ob) else most_recent_ob(m15_fib_ob, m5_fib_ob)
            selected_ob_source = "fib_confirmed_m15_m5"
            selected_ob_locked = True
        else:
            selected_ob = source_selected_ob if source_selected_ob else most_recent_ob(m15_ob, m5_ob)

    if selected_ob and h1_context_ob:
        selected_ob = dict(selected_ob)
        selected_ob["h1_context_ob"] = h1_context_ob
        selected_ob["inside_h1_ob"] = True
        selected_ob["h1_refinement_priority"] = "M15_FIRST"

    return selected_ob, m15_ob, m5_ob, zone_name, selected_ob_source, selected_ob_locked


def get_flip_reference_ob(diagnostic_decision, internal_event_pack, m15_result, m5_result, m15, m5):
    """
    Return the source OB behind a diagnostic flip state.

    This is visual/diagnostic only. It must not create an executable entry.
    Example: WAIT_BUY_PULLBACK_AFTER_SUPPLY_INVALIDATION should still draw
    the M5/M15 demand OB that produced the bullish BOS/CHoCH.
    """
    if not diagnostic_decision:
        return None, "none", "No diagnostic decision active"

    if diagnostic_decision == "WAIT_BUY_PULLBACK_AFTER_SUPPLY_INVALIDATION":
        wanted_bias = BULLISH
        label_side = "DEMAND"
    elif diagnostic_decision == "WAIT_SELL_PULLBACK_AFTER_DEMAND_INVALIDATION":
        wanted_bias = BEARISH
        label_side = "SUPPLY"
    else:
        return None, "none", f"Unsupported diagnostic decision: {diagnostic_decision}"

    # First preference: the OB stored on the currently selected internal BOS/CHoCH event.
    if internal_event_pack and internal_event_pack.get("event"):
        event = internal_event_pack["event"]
        tf = internal_event_pack.get("timeframe", "M5")
        df = m15 if tf == "M15" else m5
        ob = event.get("order_block")

        if event.get("bias") == wanted_bias and ob:
            if not is_ob_invalidated(df, ob, use_close=True):
                clean_ob = dict(ob)
                clean_ob["timeframe"] = tf
                clean_ob["source_selected_ob"] = True
                clean_ob["diagnostic_only"] = True
                clean_ob["source_event"] = {
                    "direction": event.get("direction"),
                    "tag": event.get("tag"),
                    "level": event.get("level"),
                    "level_time": event.get("level_time"),
                    "break_time": event.get("break_time"),
                }
                clean_ob["diagnostic_label"] = f"{tf} FLIP {label_side} OB"
                return clean_ob, "internal_event_source", None
            return None, "internal_event_source_invalidated", "Internal event source OB is already invalidated"

        if event.get("bias") == wanted_bias and not ob:
            missing_reason = "Internal event has no source order_block"
        else:
            missing_reason = "Current internal event direction does not match diagnostic flip direction"
    else:
        missing_reason = "No internal event pack available"

    # Second preference: latest valid OB in the same direction on the active internal timeframe,
    # without premium/discount zone filtering. This is diagnostic only and keeps the chart useful
    # when find_order_block() did not attach an OB to the event.
    active_tf = internal_event_pack.get("timeframe", "M5") if internal_event_pack else "M5"
    search_order = [("M5", m5_result, m5), ("M15", m15_result, m15)]
    if active_tf == "M15":
        search_order = [("M15", m15_result, m15), ("M5", m5_result, m5)]

    for tf, result, df in search_order:
        fallback_ob = last_valid_ob(result.get("events", []), df, wanted_bias, tf)
        if fallback_ob:
            fallback_ob = dict(fallback_ob)
            fallback_ob["diagnostic_only"] = True
            fallback_ob["diagnostic_label"] = f"{tf} FLIP {label_side} OB"
            return fallback_ob, "fallback_latest_valid_same_direction", missing_reason

    return None, "missing", missing_reason


def select_reference_zones(external_bias, h1_result, h1, m15_result, m15, m5_result, m5, swing_low, swing_high, equilibrium):
    # H1 source zone
    h1_source_ob = last_valid_ob(h1_result["events"], h1, external_bias, "H1")

    # Opposite retracement reference zone
    if external_bias == BULLISH:
        opp_bias = BEARISH
        zone_low = equilibrium
        zone_high = swing_high
    else:
        opp_bias = BULLISH
        zone_low = swing_low
        zone_high = equilibrium

    m15_ref = last_valid_ob(
        m15_result["events"], m15, opp_bias, "M15",
        zone_low=zone_low, zone_high=zone_high
    )
    m5_ref = last_valid_ob(
        m5_result["events"], m5, opp_bias, "M5",
        zone_low=zone_low, zone_high=zone_high
    )

    retrace_ref_ob = most_recent_ob(m15_ref, m5_ref)
    return h1_source_ob, retrace_ref_ob


def get_entry_status(current_price, entry, trade_direction, point):
    if entry is None or trade_direction is None:
        return "NO_ENTRY"

    tolerance_points = float(os.getenv("SMC_ENTRY_TOLERANCE_POINTS", "10"))
    allowed = tolerance_points * point

    if trade_direction == BULLISH:
        if current_price < entry - allowed:
            return "PENDING_BUY_LIMIT"
        if abs(current_price - entry) <= allowed:
            return "AT_BUY_ENTRY"
        return "BUY_LIMIT_NOT_CHASED"

    if trade_direction == BEARISH:
        if current_price > entry + allowed:
            return "PENDING_SELL_LIMIT"
        if abs(current_price - entry) <= allowed:
            return "AT_SELL_ENTRY"
        return "SELL_LIMIT_NOT_CHASED"

    return "NO_ENTRY"


def get_latest_pivot_pair(result):
    last_high = result["pivot_highs"][-1] if result["pivot_highs"] else None
    last_low = result["pivot_lows"][-1] if result["pivot_lows"] else None
    return last_high, last_low


def get_internal_structure_levels(result, df: pd.DataFrame, timeframe_label: str):
    trend = result.get("trend", 0)
    trend_text = "bullish" if trend == BULLISH else "bearish" if trend == BEARISH else "neutral"
    piv_highs = result.get("pivot_highs", [])
    piv_lows = result.get("pivot_lows", [])
    events = result.get("events", [])
    last_ev = events[-1] if events else None

    strong_high = None
    weak_high = piv_highs[-1] if piv_highs else None
    strong_low = None
    weak_low = piv_lows[-1] if piv_lows else None

    if trend == BULLISH:
        break_idx = int(last_ev["break_index"]) if last_ev and last_ev["bias"] == BULLISH else len(df) - 1
        candidates = [p for p in piv_lows if int(p["index"]) <= break_idx]
        strong_low = candidates[-1] if candidates else (piv_lows[-1] if piv_lows else None)
    elif trend == BEARISH:
        break_idx = int(last_ev["break_index"]) if last_ev and last_ev["bias"] == BEARISH else len(df) - 1
        candidates = [p for p in piv_highs if int(p["index"]) <= break_idx]
        strong_high = candidates[-1] if candidates else (piv_highs[-1] if piv_highs else None)

    return {
        "timeframe": timeframe_label,
        "trend": trend_text,
        "strong_high": strong_high,
        "weak_high": weak_high,
        "strong_low": strong_low,
        "weak_low": weak_low,
        "last_events": events[-3:],
    }


def detect_ob_flip_candidates(m15_result, m5_result, m15, m5, current_price):
    def _latest_ob(events, bias: int):
        for e in reversed(events):
            if e.get("bias") == bias and e.get("order_block"):
                return e["order_block"], e
        return None, None

    def _invalidated(ob: dict | None, df: pd.DataFrame):
        if not ob:
            return False
        return is_ob_invalidated(df, ob, use_close=True)

    out = {
        "m5_bullish_flip": None,
        "m5_bearish_flip": None,
        "m15_bullish_flip": None,
        "m15_bearish_flip": None,
    }

    m5_supply, m5_supply_event = _latest_ob(m5_result["events"], BEARISH)
    m5_demand, m5_demand_event = _latest_ob(m5_result["events"], BULLISH)
    m15_supply, m15_supply_event = _latest_ob(m15_result["events"], BEARISH)
    m15_demand, m15_demand_event = _latest_ob(m15_result["events"], BULLISH)

    if m5_supply and _invalidated(m5_supply, m5):
        out["m5_bullish_flip"] = {
            "timeframe": "M5",
            "invalidated_ob_type": "supply",
            "ob": m5_supply,
            "source_event": m5_supply_event,
            "message": "SUPPLY INVALIDATED | BULLISH FLIP CANDIDATE",
            "current_price": current_price,
        }
    if m5_demand and _invalidated(m5_demand, m5):
        out["m5_bearish_flip"] = {
            "timeframe": "M5",
            "invalidated_ob_type": "demand",
            "ob": m5_demand,
            "source_event": m5_demand_event,
            "message": "DEMAND INVALIDATED | BEARISH FLIP CANDIDATE",
            "current_price": current_price,
        }
    if m15_supply and _invalidated(m15_supply, m15):
        out["m15_bullish_flip"] = {
            "timeframe": "M15",
            "invalidated_ob_type": "supply",
            "ob": m15_supply,
            "source_event": m15_supply_event,
            "message": "SUPPLY INVALIDATED | BULLISH FLIP CANDIDATE",
            "current_price": current_price,
        }
    if m15_demand and _invalidated(m15_demand, m15):
        out["m15_bearish_flip"] = {
            "timeframe": "M15",
            "invalidated_ob_type": "demand",
            "ob": m15_demand,
            "source_event": m15_demand_event,
            "message": "DEMAND INVALIDATED | BEARISH FLIP CANDIDATE",
            "current_price": current_price,
        }
    return out


def build_overlay(symbol: str):
    swing_length = int(os.getenv("SMC_SWING_LENGTH", "20"))
    internal_length = int(os.getenv("SMC_INTERNAL_LENGTH", "3"))

    h1 = get_candles(symbol, mt5.TIMEFRAME_H1, 800)
    m15 = get_candles(symbol, mt5.TIMEFRAME_M15, 800)
    m5 = get_candles(symbol, mt5.TIMEFRAME_M5, 800)

    h1_result = detect_structure(h1, swing_length)
    if not h1_result["events"]:
        h1_result = detect_structure(h1, 20)

    m15_result = detect_structure(m15, internal_length)
    m5_result = detect_structure(m5, internal_length)

    tick = mt5.symbol_info_tick(symbol)
    current_price = float(tick.bid) if tick else float(m5.iloc[-1]["close"])

    symbol_info = mt5.symbol_info(symbol)
    point = float(symbol_info.point) if symbol_info else 0.00001

    right_time = m5.iloc[-1]["time"] + timedelta(minutes=30)

    h1_last_event = last_event(h1_result["events"])
    external_bias = h1_last_event["bias"] if h1_last_event else 0

    swing = choose_h1_swing_range(h1, h1_result, h1_last_event)
    swing_high = swing["swing_high"]
    swing_low = swing["swing_low"]
    swing_high_time = swing["swing_high_time"]
    swing_low_time = swing["swing_low_time"]

    equilibrium = (swing_high + swing_low) / 2.0

    if external_bias == 0:
        external_bias = BULLISH if current_price >= equilibrium else BEARISH

    fibs = fib_prices(swing_low, swing_high, external_bias)
    current_location = price_location(current_price, swing_low, swing_high)

    # Active H1 OBs are separate from simple premium/discount.
    # Use swing/rejection OBs first because H1 supply/demand can exist before H1 BOS/CHoCH.
    # Fall back to structure-event OBs only if no rejection OB is available.
    h1_supply_ob = find_rejection_order_block(h1, h1_result, BEARISH, "H1") or last_valid_ob(h1_result["events"], h1, BEARISH, "H1")
    h1_demand_ob = find_rejection_order_block(h1, h1_result, BULLISH, "H1") or last_valid_ob(h1_result["events"], h1, BULLISH, "H1")

    pd_detail = pd_range_detail(current_price, swing_low, swing_high)

    internal_event_pack = choose_internal_event(m15_result, m5_result, m15, m5)

    decision, trade_direction, trade_mode = decide_trade_context(
        external_bias,
        current_location,
        internal_event_pack,
        swing_low,
        swing_high,
    )

    selected_ob, m15_ob, m5_ob, zone_name, selected_ob_source, selected_ob_locked = select_active_ob(
        trade_direction,
        trade_mode,
        internal_event_pack,
        m15_result,
        m5_result,
        m15,
        m5,
        swing_low,
        swing_high,
        equilibrium,
        fibs,
        h1_supply_ob=h1_supply_ob,
        h1_demand_ob=h1_demand_ob,
        current_price=current_price,
    )

    h1_source_ob, retrace_ref_ob = select_reference_zones(
        external_bias,
        h1_result,
        h1,
        m15_result,
        m15,
        m5_result,
        m5,
        swing_low,
        swing_high,
        equilibrium,
    )

    internal_m5_structure = get_internal_structure_levels(m5_result, m5, "M5")
    internal_m15_structure = get_internal_structure_levels(m15_result, m15, "M15")
    ob_flip_candidates = detect_ob_flip_candidates(m15_result, m5_result, m15, m5, current_price)

    # ------------------------------------------------------------------
    # H1 OB CONTEXT OVERRIDE
    # ------------------------------------------------------------------
    # SMC priority should be:
    #     H1 OB context > true PD zone > M15/M5 flip candidate.
    #
    # If price is still inside a valid H1 supply, a lower-timeframe bullish
    # supply invalidation is NOT permission to buy yet. It should be treated
    # as internal noise until H1 supply is invalidated by an H1 close above
    # the H1 supply high. The mirror rule applies for H1 demand.
    h1_supply_valid = bool(h1_supply_ob) and not is_ob_invalidated(h1, h1_supply_ob, use_close=True)
    h1_demand_valid = bool(h1_demand_ob) and not is_ob_invalidated(h1, h1_demand_ob, use_close=True)
    inside_h1_supply = bool(h1_supply_valid and price_inside_ob(current_price, h1_supply_ob))
    inside_h1_demand = bool(h1_demand_valid and price_inside_ob(current_price, h1_demand_ob))

    h1_ob_context = "none"
    if inside_h1_supply:
        h1_ob_context = "inside_h1_supply"
    elif inside_h1_demand:
        h1_ob_context = "inside_h1_demand"

    h1_context_override_enabled = env_bool("SMC_H1_OB_CONTEXT_OVERRIDE_ENABLED", default=True)

    show_ob_invalidations = os.getenv("SMC_FLIP_CANDIDATE_VISUAL_ONLY", "true").lower() != "false" or os.getenv("SMC_SHOW_OB_INVALIDATIONS", "true").lower() == "true"
    flip_visual_only = os.getenv("SMC_FLIP_CANDIDATE_VISUAL_ONLY", "true").lower() == "true"
    diagnostic_decision = None
    if show_ob_invalidations and flip_visual_only:
        has_bull_flip = bool(ob_flip_candidates["m5_bullish_flip"] or ob_flip_candidates["m15_bullish_flip"])
        has_bear_flip = bool(ob_flip_candidates["m5_bearish_flip"] or ob_flip_candidates["m15_bearish_flip"])

        executable_sell_from_h1_supply = bool(
            selected_ob
            and trade_direction == BEARISH
            and str(decision).startswith("SELL_")
        )
        executable_buy_from_h1_demand = bool(
            selected_ob
            and trade_direction == BULLISH
            and str(decision).startswith("BUY_")
        )

        if h1_context_override_enabled and inside_h1_supply and not executable_sell_from_h1_supply:
            diagnostic_decision = "WAIT_SELL_CONFIRMATION_FROM_H1_SUPPLY"
            decision = diagnostic_decision
            trade_mode = "diagnostic"
            trade_direction = None
            selected_ob = None
            zone_name = "h1_supply_rejection"
        elif h1_context_override_enabled and inside_h1_demand and not executable_buy_from_h1_demand:
            diagnostic_decision = "WAIT_BUY_CONFIRMATION_FROM_H1_DEMAND"
            decision = diagnostic_decision
            trade_mode = "diagnostic"
            trade_direction = None
            selected_ob = None
            zone_name = "h1_demand_rejection"
        elif external_bias == BULLISH and pd_detail.get("pd_label") in {"true_premium", "deep_premium", "extreme_premium"} and has_bull_flip:
            diagnostic_decision = "WAIT_BUY_PULLBACK_AFTER_SUPPLY_INVALIDATION"
            decision = diagnostic_decision
            trade_mode = "diagnostic"
            trade_direction = None
            selected_ob = None
            zone_name = "bullish_flip_reference"
        elif external_bias == BEARISH and pd_detail.get("pd_label") in {"true_discount", "deep_discount", "extreme_discount"} and has_bear_flip:
            diagnostic_decision = "WAIT_SELL_PULLBACK_AFTER_DEMAND_INVALIDATION"
            decision = diagnostic_decision
            trade_mode = "diagnostic"
            trade_direction = None
            selected_ob = None
            zone_name = "bearish_flip_reference"

    latest_flip = get_latest_flip_candidate(ob_flip_candidates)

    flip_reference_ob, flip_reference_ob_source, flip_reference_ob_missing_reason = get_flip_reference_ob(
        diagnostic_decision,
        internal_event_pack,
        m15_result,
        m5_result,
        m15,
        m5,
    )
    flip_reference_ob_drawn = False

    lines = []

    visual_mode = get_visual_mode()
    is_clean_visual = visual_mode == "clean"
    is_trade_visual = visual_mode == "trade"
    is_debug_visual = visual_mode == "debug"

    # Clean mode should be a trading dashboard, not a debug chart.
    show_reference_zones = env_bool("SMC_SHOW_REFERENCE_ZONES", default=not is_clean_visual)
    show_h1_structure = env_bool("SMC_SHOW_H1_STRUCTURE", default=True)
    show_h1_strong_weak = env_bool("SMC_SHOW_H1_STRONG_WEAK", default=True)
    show_h1_obs = env_bool("SMC_SHOW_H1_OBS", default=True)
    show_chart_flip_zones = env_bool("SMC_SHOW_FLIP_ZONES_ON_CHART", default=is_debug_visual)
    show_fib_labels_minimal = env_bool("SMC_SHOW_FIB_LABELS_MINIMAL", default=True)

    # DASHBOARD
    external_text = bias_text(external_bias)

    if internal_event_pack:
        internal_event = internal_event_pack["event"]
        internal_text = f"{internal_event_pack['timeframe']} {bias_text(internal_event['bias'])} {internal_event['tag']}"
    else:
        internal_text = "None"

    # ------------------------------------------------------------------
    # DASHBOARD CLEANUP / PRIORITY
    # ------------------------------------------------------------------
    # The dashboard should show the CURRENT dominant state.  A previous
    # bullish flip must not keep showing as the primary message once H1
    # supply + bearish confirmation has produced a sell context.  The
    # mirror rule applies for bearish flips once demand + bullish
    # confirmation is dominant.
    dashboard_suppressed_flip = None
    dashboard_flip = latest_flip
    if dashboard_flip:
        flip_msg = str(dashboard_flip.get("message", "")).upper()
        flip_is_bullish = "BULLISH" in flip_msg or dashboard_flip.get("invalidated_ob_type") == "supply"
        flip_is_bearish = "BEARISH" in flip_msg or dashboard_flip.get("invalidated_ob_type") == "demand"

        sell_context_active = (
            str(decision).startswith("SELL_")
            or decision == "WAIT_SELL_CONFIRMATION_FROM_H1_SUPPLY"
            or h1_ob_context == "inside_h1_supply"
        )
        buy_context_active = (
            str(decision).startswith("BUY_")
            or decision == "WAIT_BUY_CONFIRMATION_FROM_H1_DEMAND"
            or h1_ob_context == "inside_h1_demand"
        )

        if sell_context_active and flip_is_bullish:
            dashboard_suppressed_flip = dashboard_flip
            dashboard_flip = None
        elif buy_context_active and flip_is_bearish:
            dashboard_suppressed_flip = dashboard_flip
            dashboard_flip = None

    # ------------------------------------------------------------------
    # DASHBOARD ACTIVE-ZONE NORMALIZATION
    # ------------------------------------------------------------------
    # The execution decision may remain SELL_RETRACEMENT even after price has
    # moved out of the H1 supply rectangle.  In that case h1_ob_context can be
    # "none" because price is no longer literally inside the H1 OB, but the
    # trade idea is still a rejection/refinement from that H1 supply.  The
    # dashboard should therefore show the dominant H1 context, not fall back to
    # generic "premium_retracement_zone".
    decision_text = str(decision)
    pd_label_text = str(pd_detail.get("pd_label", ""))
    selected_source_text = str(selected_ob_source or "")

    h1_supply_dashboard_context = bool(h1_supply_ob) and (
        h1_ob_context == "inside_h1_supply"
        or selected_source_text.startswith("h1_context")
        or (
            decision_text.startswith("SELL_")
            and ("premium" in str(current_location).lower() or "premium" in pd_label_text.lower())
        )
    )
    h1_demand_dashboard_context = bool(h1_demand_ob) and (
        h1_ob_context == "inside_h1_demand"
        or selected_source_text.startswith("h1_context")
        or (
            decision_text.startswith("BUY_")
            and ("discount" in str(current_location).lower() or "discount" in pd_label_text.lower())
        )
    )

    dashboard_h1_context_text = None
    dashboard_zone_name = zone_name
    if h1_supply_dashboard_context and (decision_text.startswith("SELL_") or "SELL_CONFIRMATION" in decision_text):
        dashboard_zone_name = "h1_supply_refined_sell_zone"
        dashboard_h1_context_text = "h1_supply_rejection"
    elif h1_demand_dashboard_context and (decision_text.startswith("BUY_") or "BUY_CONFIRMATION" in decision_text):
        dashboard_zone_name = "h1_demand_refined_buy_zone"
        dashboard_h1_context_text = "h1_demand_rejection"
    elif h1_ob_context != "none":
        dashboard_h1_context_text = h1_ob_context

    add_label(lines, "AI_SMC_DASHBOARD_1", 12, 22, f"AI SMC | {symbol}", "yellow")
    add_label(lines, "AI_SMC_DASHBOARD_2", 12, 42, f"External H1: {external_text} | Location: {current_location} | PD: {pd_detail['pd_label']}", "yellow")
    add_label(lines, "AI_SMC_DASHBOARD_3", 12, 62, f"Internal: {internal_text}", "white")
    add_label(lines, "AI_SMC_DASHBOARD_4", 12, 82, f"Decision: {decision} | Mode: {trade_mode}", "white")

    dashboard_status_y = 102
    dashboard_active_y = 122

    if dashboard_h1_context_text:
        add_label(lines, "AI_SMC_DASHBOARD_H1_CONTEXT", 12, dashboard_status_y, f"H1 OB Context: {dashboard_h1_context_text}", "orange")
        dashboard_status_y += 20
        dashboard_active_y += 20

    if dashboard_flip:
        add_label(lines, "AI_SMC_DASHBOARD_FLIP", 12, dashboard_status_y, f"Flip: {dashboard_flip['timeframe']} {dashboard_flip['message']}", "orange")
        dashboard_status_y += 20
        dashboard_active_y += 20

    fib_start_time = min(swing_low_time, swing_high_time)
    fib_end_time = right_time

    # EQ + FIBS
    add_line(lines, "AI_SMC_EQ", fib_start_time, fib_end_time, equilibrium, equilibrium, "", "gray")
    add_text(lines, "AI_SMC_EQ_TEXT", right_time, equilibrium + point * 18, "EQ", "gray")

    fib_label_offsets = {
        0.618: 20,
        0.705: -22,
        0.79: 20,
        0.886: -22,
    }

    fib_levels_to_draw = [(0.618, "yellow"), (0.886, "orange")] if (is_clean_visual and show_fib_labels_minimal) else [
        (0.618, "yellow"),
        (0.705, "yellow"),
        (0.79, "yellow"),
        (0.886, "orange"),
    ]

    for level, color in fib_levels_to_draw:
        name = str(level).replace(".", "_")
        y = fibs[level]
        add_line(lines, f"AI_SMC_FIB_{name}", fib_start_time, fib_end_time, y, y, "", color)
        if not is_clean_visual or not show_fib_labels_minimal:
            add_text(lines, f"AI_SMC_FIB_{name}_TEXT", right_time, y + point * fib_label_offsets[level], str(level), color)

    # EXTERNAL STRUCTURE
    if h1_last_event and show_h1_structure:
        event_color = "green" if h1_last_event["bias"] == BULLISH else "red"
        event_label = f"H1 {h1_last_event['direction'].upper()} {h1_last_event['tag']}"

        add_line(
            lines,
            "AI_SMC_H1_LAST_STRUCTURE",
            h1_last_event["level_time"],
            h1_last_event["break_time"],
            h1_last_event["level"],
            h1_last_event["level"],
            "",
            event_color,
        )
        add_text(
            lines,
            "AI_SMC_H1_LAST_STRUCTURE_TEXT",
            h1_last_event["break_time"],
            h1_last_event["level"] + point * 25,
            event_label,
            event_color,
        )

    if show_h1_strong_weak and external_bias == BULLISH:
        add_line(lines, "AI_SMC_STRONG_LOW", swing_low_time, right_time, swing_low, swing_low, "", "green")
        add_line(lines, "AI_SMC_WEAK_HIGH", swing_high_time, right_time, swing_high, swing_high, "", "red")
        add_text(lines, "AI_SMC_STRONG_LOW_TEXT", swing_low_time, swing_low - point * 22, "Strong Low", "green")
        add_text(lines, "AI_SMC_WEAK_HIGH_TEXT", swing_high_time, swing_high + point * 22, "Weak High", "red")
    elif show_h1_strong_weak:
        add_line(lines, "AI_SMC_STRONG_HIGH", swing_high_time, right_time, swing_high, swing_high, "", "red")
        add_line(lines, "AI_SMC_WEAK_LOW", swing_low_time, right_time, swing_low, swing_low, "", "green")
        add_text(lines, "AI_SMC_STRONG_HIGH_TEXT", swing_high_time, swing_high + point * 22, "Strong High", "red")
        add_text(lines, "AI_SMC_WEAK_LOW_TEXT", swing_low_time, swing_low - point * 22, "Weak Low", "green")

    # INTERNAL STRUCTURE
    show_internal_structure = env_bool("SMC_SHOW_INTERNAL_STRUCTURE", default=True)
    if internal_event_pack and show_internal_structure:
        internal_tf = internal_event_pack["timeframe"]
        internal_event = internal_event_pack["event"]
        internal_color = "green" if internal_event["bias"] == BULLISH else "red"
        internal_label = f"{internal_tf} {internal_event['direction'].upper()} {internal_event['tag']}"

        add_line(
            lines,
            "AI_SMC_INTERNAL_STRUCTURE",
            internal_event["level_time"],
            internal_event["break_time"],
            internal_event["level"],
            internal_event["level"],
            "",
            internal_color,
        )
        add_text(
            lines,
            "AI_SMC_INTERNAL_STRUCTURE_TEXT",
            internal_event["break_time"],
            internal_event["level"] - point * 35,
            internal_label,
            internal_color,
        )

    # M5 STRUCTURE — force M5 BOS/CHoCH print separately from the active internal event.
    # This ensures M5 BOS/CHoCH remains visible even if the active confirmation pack
    # is M15 or the dashboard is in clean mode.
    show_m5_structure = env_bool("SMC_SHOW_M5_STRUCTURE", default=True)
    if show_m5_structure and m5_result.get("events"):
        m5_last_event = m5_result["events"][-1]
        m5_color = "green" if m5_last_event["bias"] == BULLISH else "red"
        m5_label = f"M5 {m5_last_event['direction'].upper()} {m5_last_event['tag']}"

        add_line(
            lines,
            "AI_SMC_M5_LAST_STRUCTURE",
            m5_last_event["level_time"],
            m5_last_event["break_time"],
            m5_last_event["level"],
            m5_last_event["level"],
            "",
            m5_color,
        )
        add_text(
            lines,
            "AI_SMC_M5_LAST_STRUCTURE_TEXT",
            m5_last_event["break_time"],
            m5_last_event["level"] - point * 28,
            m5_label,
            m5_color,
        )

    # INTERNAL SWINGS
    # Respect .env directly. Earlier versions blocked this in clean mode.
    show_internal_swings = env_bool("SMC_SHOW_INTERNAL_SWINGS", default=True)

    if show_internal_swings:
        if internal_event_pack and internal_event_pack["timeframe"] == "M15":
            internal_result = m15_result
            internal_tf = "M15"
        else:
            internal_result = m5_result
            internal_tf = "M5"

        last_ih, last_il = get_latest_pivot_pair(internal_result)

        if last_ih:
            add_line(lines, "AI_SMC_INTERNAL_SWING_HIGH", last_ih["time"], right_time, last_ih["price"], last_ih["price"], "", "cyan")
            add_text(lines, "AI_SMC_INTERNAL_SWING_HIGH_TEXT", last_ih["time"], last_ih["price"] + point * 18, f"{internal_tf} swing high", "cyan")

        if last_il:
            add_line(lines, "AI_SMC_INTERNAL_SWING_LOW", last_il["time"], right_time, last_il["price"], last_il["price"], "", "cyan")
            add_text(lines, "AI_SMC_INTERNAL_SWING_LOW_TEXT", last_il["time"], last_il["price"] - point * 18, f"{internal_tf} swing low", "cyan")

    # Respect .env directly. If SMC_SHOW_INTERNAL_STRONG_WEAK=true, show it even in clean mode.
    show_internal_strong_weak = env_bool(
        "SMC_SHOW_INTERNAL_STRONG_WEAK",
        default=True,
    )

    if show_internal_strong_weak:
        def _draw_internal_levels(levels: dict, color: str, text_color: str):
            tf = levels["timeframe"]
            trend = levels.get("trend")

            # In clean/trade mode, show only the two decision-useful levels for the current trend.
            draw_all = is_debug_visual

            if levels.get("strong_low") and (draw_all or trend == "bullish"):
                p = float(levels["strong_low"]["price"])
                t = levels["strong_low"]["time"]
                add_line(lines, f"AI_SMC_{tf}_STRONG_LOW", t, right_time, p, p, "", color)
                add_text(lines, f"AI_SMC_{tf}_STRONG_LOW_TEXT", right_time, p - point * 14, f"{tf} Strong Low", text_color)
            if levels.get("weak_high") and (draw_all or trend == "bullish"):
                p = float(levels["weak_high"]["price"])
                t = levels["weak_high"]["time"]
                add_line(lines, f"AI_SMC_{tf}_WEAK_HIGH", t, right_time, p, p, "", "white")
                add_text(lines, f"AI_SMC_{tf}_WEAK_HIGH_TEXT", right_time, p + point * 14, f"{tf} Weak High", "white")
            if levels.get("strong_high") and (draw_all or trend == "bearish"):
                p = float(levels["strong_high"]["price"])
                t = levels["strong_high"]["time"]
                add_line(lines, f"AI_SMC_{tf}_STRONG_HIGH", t, right_time, p, p, "", color)
                add_text(lines, f"AI_SMC_{tf}_STRONG_HIGH_TEXT", right_time, p + point * 14, f"{tf} Strong High", text_color)
            if levels.get("weak_low") and (draw_all or trend == "bearish"):
                p = float(levels["weak_low"]["price"])
                t = levels["weak_low"]["time"]
                add_line(lines, f"AI_SMC_{tf}_WEAK_LOW", t, right_time, p, p, "", "white")
                add_text(lines, f"AI_SMC_{tf}_WEAK_LOW_TEXT", right_time, p - point * 14, f"{tf} Weak Low", "white")

        # Always draw M5 strong/weak structure when enabled.
        # In debug mode, also draw M15. In trade/clean mode, draw M15 only if it is the active confirmation timeframe.
        _draw_internal_levels(internal_m5_structure, "cyan", "cyan")
        active_tf = internal_event_pack["timeframe"] if internal_event_pack else "M5"
        if is_debug_visual or active_tf == "M15":
            _draw_internal_levels(internal_m15_structure, "magenta", "magenta")

    # Respect .env directly. Keep false by default to avoid noise.
    show_previous_structure = env_bool("SMC_SHOW_PREVIOUS_STRUCTURE", default=False)
    previous_structure_count = int(os.getenv("SMC_PREVIOUS_STRUCTURE_COUNT", "1"))
    if show_previous_structure and previous_structure_count > 0:
        def _draw_previous_events(result: dict, tf: str, color_base: str):
            events = result.get("events", [])[-previous_structure_count:]
            for idx, ev in enumerate(events):
                c = "teal" if ev["bias"] == BULLISH else "maroon"
                tag = f"{tf} {'Bullish' if ev['bias']==BULLISH else 'Bearish'} {ev['tag']}"
                add_line(
                    lines,
                    f"AI_SMC_{tf}_PREV_{idx}",
                    ev["level_time"],
                    ev["break_time"],
                    ev["level"],
                    ev["level"],
                    "",
                    c if color_base == "" else color_base,
                )
                add_text(
                    lines,
                    f"AI_SMC_{tf}_PREV_TEXT_{idx}",
                    ev["break_time"],
                    ev["level"] + point * (10 + 8 * idx),
                    tag,
                    "gray",
                )
        _draw_previous_events(m5_result, "M5", "")
        _draw_previous_events(m15_result, "M15", "")

    if show_ob_invalidations and show_chart_flip_zones:
        def _draw_flip(flip: dict | None, name: str, color: str):
            if not flip:
                return
            ob = flip["ob"]
            add_rect(
                lines,
                f"AI_SMC_{name}_OB",
                ob["time"],
                right_time,
                ob["high"],
                ob["low"],
                "",
                color,
            )
            add_text(
                lines,
                f"AI_SMC_{name}_TEXT",
                right_time,
                (ob["high"] + ob["low"]) / 2.0,
                flip["message"],
                color,
            )

        if is_debug_visual:
            _draw_flip(ob_flip_candidates["m5_bullish_flip"], "M5_BULL_FLIP", "lime")
            _draw_flip(ob_flip_candidates["m5_bearish_flip"], "M5_BEAR_FLIP", "orange")
            _draw_flip(ob_flip_candidates["m15_bullish_flip"], "M15_BULL_FLIP", "green")
            _draw_flip(ob_flip_candidates["m15_bearish_flip"], "M15_BEAR_FLIP", "red")
        elif latest_flip:
            color = "lime" if latest_flip["invalidated_ob_type"] == "supply" else "orange"
            _draw_flip(latest_flip, f"{latest_flip['timeframe']}_LATEST_FLIP", color)

    # H1 ORDER BLOCKS: always separate from broad premium/discount.
    # These zones are the higher-timeframe supply/demand context used for refinement.
    if show_h1_obs:
        if h1_supply_ob:
            add_rect(
                lines,
                "AI_SMC_H1_SUPPLY_OB",
                h1_supply_ob["time"],
                right_time,
                h1_supply_ob["high"],
                h1_supply_ob["low"],
                "",
                "orange",
            )
            add_text(
                lines,
                "AI_SMC_H1_SUPPLY_OB_TEXT",
                right_time,
                (h1_supply_ob["high"] + h1_supply_ob["low"]) / 2.0 + point * 20,
                f"H1 SUPPLY OB | {h1_supply_ob.get('source', 'structure')} | C{h1_supply_ob.get('cluster_count', '?')}",
                "orange",
            )
        if h1_demand_ob:
            add_rect(
                lines,
                "AI_SMC_H1_DEMAND_OB",
                h1_demand_ob["time"],
                right_time,
                h1_demand_ob["high"],
                h1_demand_ob["low"],
                "",
                "blue",
            )
            add_text(
                lines,
                "AI_SMC_H1_DEMAND_OB_TEXT",
                right_time,
                (h1_demand_ob["high"] + h1_demand_ob["low"]) / 2.0 - point * 20,
                f"H1 DEMAND OB | {h1_demand_ob.get('source', 'structure')} | C{h1_demand_ob.get('cluster_count', '?')}",
                "blue",
            )

    # REFERENCE ZONE 1: H1 SOURCE ZONE (hidden in clean mode by default)
    if h1_source_ob and show_reference_zones:
        source_color = "blue" if h1_source_ob["bias"] == BULLISH else "orange"
        source_label = "H1 SOURCE DEMAND" if h1_source_ob["bias"] == BULLISH else "H1 SOURCE SUPPLY"

        add_rect(
            lines,
            "AI_SMC_H1_SOURCE_ZONE",
            h1_source_ob["time"],
            right_time,
            h1_source_ob["high"],
            h1_source_ob["low"],
            "",
            source_color,
        )
        add_text(
            lines,
            "AI_SMC_H1_SOURCE_ZONE_TEXT",
            h1_source_ob["time"],
            (h1_source_ob["high"] + h1_source_ob["low"]) / 2.0,
            source_label,
            source_color,
        )

    # REFERENCE ZONE 2: RETRACEMENT REACTION ZONE (hidden in clean mode by default)
    if retrace_ref_ob and show_reference_zones:
        retrace_color = "magenta" if retrace_ref_ob["bias"] == BEARISH else "cyan"
        retrace_label = f"{retrace_ref_ob['timeframe']} RETRACE {'SUPPLY' if retrace_ref_ob['bias'] == BEARISH else 'DEMAND'}"

        add_rect(
            lines,
            "AI_SMC_RETRACE_REFERENCE_ZONE",
            retrace_ref_ob["time"],
            right_time,
            retrace_ref_ob["high"],
            retrace_ref_ob["low"],
            "",
            retrace_color,
        )
        add_text(
            lines,
            "AI_SMC_RETRACE_REFERENCE_ZONE_TEXT",
            retrace_ref_ob["time"],
            (retrace_ref_ob["high"] + retrace_ref_ob["low"]) / 2.0 + point * 18,
            retrace_label,
            retrace_color,
        )

    # DIAGNOSTIC FLIP REFERENCE OB
    # Draws the source OB behind WAIT_BUY/WAIT_SELL pullback states without creating an executable entry.
    if flip_reference_ob:
        flip_ob_color = "green" if flip_reference_ob.get("bias") == BULLISH else "red"
        flip_ob_label = flip_reference_ob.get("diagnostic_label") or (
            f"{flip_reference_ob.get('timeframe', 'M5')} FLIP {'DEMAND' if flip_reference_ob.get('bias') == BULLISH else 'SUPPLY'} OB"
        )
        add_rect(
            lines,
            "AI_SMC_FLIP_REFERENCE_OB",
            flip_reference_ob["time"],
            right_time,
            flip_reference_ob["high"],
            flip_reference_ob["low"],
            "",
            flip_ob_color,
        )
        add_text(
            lines,
            "AI_SMC_FLIP_REFERENCE_OB_TEXT",
            right_time,
            (flip_reference_ob["high"] + flip_reference_ob["low"]) / 2.0 - point * 18,
            flip_ob_label,
            flip_ob_color,
        )
        flip_reference_ob_drawn = True

    # ACTIVE CURRENT OB
    if selected_ob:
        ob_color = "green" if selected_ob["bias"] == BULLISH else "red"
        fib_tag = "FIB " if selected_ob.get("fib_confirmed") else ""
        ob_label = f"{selected_ob['timeframe']} {fib_tag}ACTIVE {'DEMAND' if selected_ob['bias'] == BULLISH else 'SUPPLY'} OB"

        add_rect(
            lines,
            "AI_SMC_ACTIVE_OB",
            selected_ob["time"],
            right_time,
            selected_ob["high"],
            selected_ob["low"],
            "",
            ob_color,
        )
        add_text(
            lines,
            "AI_SMC_ACTIVE_OB_TEXT",
            right_time,
            (selected_ob["high"] + selected_ob["low"]) / 2.0 - point * 18,
            ob_label,
            ob_color,
        )

    # ENTRY / SL / TP
    entry = None
    stop_loss = None
    take_profit = None
    rr = float(os.getenv("SMC_RR", "3.0"))

    stop_source = "none"
    buffer_pips = float(os.getenv("OB_BUFFER_PIPS", "3"))
    buffer_price = buffer_pips * point * 10 if point < 0.001 else buffer_pips * point

    if selected_ob and trade_direction:
        if trade_direction == BULLISH:
            entry = selected_ob["high"]
            normal_stop = selected_ob["low"] - buffer_price
            stop_loss, take_profit, stop_source = apply_h1_retrace_stop_for_visuals(
                trade_direction, trade_mode, entry, normal_stop, selected_ob, rr, buffer_price
            )
        elif trade_direction == BEARISH:
            entry = selected_ob["low"]
            normal_stop = selected_ob["high"] + buffer_price
            stop_loss, take_profit, stop_source = apply_h1_retrace_stop_for_visuals(
                trade_direction, trade_mode, entry, normal_stop, selected_ob, rr, buffer_price
            )

        if stop_loss is not None:
            risk = abs(entry - stop_loss)
            if risk <= 0:
                take_profit = None

    entry_status = get_entry_status(current_price, entry, trade_direction, point)
    if trade_mode == "diagnostic":
        entry_status = "NO_ENTRY"

    if entry is not None and stop_loss is not None and take_profit is not None:
        trade_start_time = selected_ob["time"] if selected_ob else m5.iloc[-1]["time"]

        add_line(lines, "AI_SMC_ENTRY", trade_start_time, right_time, entry, entry, "", "blue")
        add_line(lines, "AI_SMC_SL", trade_start_time, right_time, stop_loss, stop_loss, "", "red")
        add_line(lines, "AI_SMC_TP", trade_start_time, right_time, take_profit, take_profit, "", "green")

        add_text(lines, "AI_SMC_ENTRY_TEXT", right_time, entry + point * 26, "ENTRY", "blue")
        add_text(lines, "AI_SMC_SL_TEXT", right_time, stop_loss - point * 26, "SL", "red")
        add_text(lines, "AI_SMC_TP_TEXT", right_time, take_profit + point * 26, "TP 1:3", "green")

    add_label(lines, "AI_SMC_DASHBOARD_5", 12, dashboard_status_y, f"Status: {entry_status}", "white")
    add_label(lines, "AI_SMC_DASHBOARD_6", 12, dashboard_active_y, f"Active zone: {dashboard_zone_name}", "white")

    output_path = mt5_common_files_dir() / "AI_SMC_OVERLAY.csv"
    output_path.write_text("\n".join(lines), encoding="utf-8")

    summary = {
        "symbol": symbol,
        "current_price": current_price,
        "external_h1_bias": external_text,
        "current_location": current_location,
        "pd_range_detail": pd_detail,
        "h1_dealing_range": {
            "swing_low": swing_low,
            "swing_high": swing_high,
            "equilibrium": equilibrium,
            "premium_start": pd_detail.get("premium_start_price"),
            "discount_start": pd_detail.get("discount_start_price"),
        },
        "h1_last_event": {
            "direction": h1_last_event["direction"],
            "tag": h1_last_event["tag"],
            "level": h1_last_event["level"],
            "level_time": h1_last_event["level_time"],
            "break_time": h1_last_event["break_time"],
        } if h1_last_event else None,
        "internal_event": {
            "timeframe": internal_event_pack["timeframe"],
            "direction": internal_event_pack["event"]["direction"],
            "bias": bias_text(internal_event_pack["event"]["bias"]),
            "tag": internal_event_pack["event"]["tag"],
            "level": internal_event_pack["event"]["level"],
            "level_time": internal_event_pack["event"]["level_time"],
            "break_time": internal_event_pack["event"]["break_time"],
            "break_close": internal_event_pack["event"]["break_close"],
        } if internal_event_pack else None,
        "trade_mode": trade_mode,
        "trade_direction": "buy" if trade_direction == BULLISH else "sell" if trade_direction == BEARISH else None,
        "decision": decision,
        "entry_status": entry_status,
        "zone_name": zone_name,
        "dashboard_zone_name": dashboard_zone_name,
        "dashboard_h1_context_text": dashboard_h1_context_text,
        "h1_supply_dashboard_context": h1_supply_dashboard_context,
        "h1_demand_dashboard_context": h1_demand_dashboard_context,
        "dashboard_flip_displayed": dashboard_flip,
        "dashboard_flip_suppressed": dashboard_suppressed_flip,
        "h1_source_ob": h1_source_ob,
        "h1_supply_ob": h1_supply_ob,
        "h1_demand_ob": h1_demand_ob,
        "h1_supply_valid": h1_supply_valid,
        "h1_demand_valid": h1_demand_valid,
        "inside_h1_supply": inside_h1_supply,
        "inside_h1_demand": inside_h1_demand,
        "h1_ob_context": h1_ob_context,
        "h1_context_ob": selected_ob.get("h1_context_ob") if selected_ob else None,
        "inside_h1_ob": bool(selected_ob and selected_ob.get("inside_h1_ob")),
        "h1_sl_reference": stop_source,
        "retrace_reference_ob": retrace_ref_ob,
        "selected_ob": selected_ob,
        "selected_ob_source": selected_ob_source,
        "selected_ob_locked": selected_ob_locked,
        "fib_confirmed_ob_enabled": env_bool("SMC_FIB_CONFIRMED_OB_ENABLED", True),
        "fib_confirmed_ob_required": env_bool("SMC_FIB_CONFIRMED_OB_REQUIRE_FOR_EXECUTION", True),
        "m15_fib_confirmed_ob": locals().get("m15_fib_ob"),
        "m5_fib_confirmed_ob": locals().get("m5_fib_ob"),
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "internal_m5_structure": internal_m5_structure,
        "internal_m15_structure": internal_m15_structure,
        "ob_flip_candidates": ob_flip_candidates,
        "diagnostic_decision": diagnostic_decision,
        "flip_reference_ob": flip_reference_ob,
        "flip_reference_ob_source": flip_reference_ob_source,
        "flip_reference_ob_drawn": flip_reference_ob_drawn,
        "flip_reference_ob_missing_reason": flip_reference_ob_missing_reason,
        "visual_flags": {
            "SMC_VISUAL_MODE": visual_mode,
            "SMC_SHOW_REFERENCE_ZONES": show_reference_zones,
            "SMC_SHOW_H1_STRUCTURE": show_h1_structure,
            "SMC_SHOW_H1_STRONG_WEAK": show_h1_strong_weak,
            "SMC_SHOW_H1_OBS": show_h1_obs,
            "SMC_SHOW_INTERNAL_STRUCTURE": show_internal_structure,
            "SMC_SHOW_M5_STRUCTURE": env_bool("SMC_SHOW_M5_STRUCTURE", default=True),
            "SMC_SHOW_INTERNAL_SWINGS": show_internal_swings,
            "SMC_SHOW_INTERNAL_STRONG_WEAK": show_internal_strong_weak,
            "SMC_SHOW_PREVIOUS_STRUCTURE": show_previous_structure,
            "SMC_SHOW_FLIP_ZONES_ON_CHART": show_chart_flip_zones,
        },
        "overlay_file": str(output_path),
        "strategy_version": os.getenv("STRATEGY_VERSION", "fib_flip_v8_ai_zone_priority"),
        "selected_zone_timeframe": selected_ob.get("timeframe") if selected_ob else None,
        "m15_priority_applied": bool(selected_ob and selected_ob.get("timeframe") == "M15"),
        "m5_used_as_confirmation": bool(selected_ob and selected_ob.get("timeframe") == "M5" and m15_ob),
        "flip_entries_enabled": not flip_visual_only,
    }

    return summary


def main():
    symbol = os.getenv("TRADING_SYMBOL", "GBPUSDm")

    connect_mt5()
    summary = build_overlay(symbol)
    mt5.shutdown()

    print("\n===== AI SMC OVERLAY SUMMARY =====")
    print(json.dumps(summary, default=str, indent=2))


if __name__ == "__main__":
    main()