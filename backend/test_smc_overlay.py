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


def find_order_block(df: pd.DataFrame, start_index: int, end_index: int, bias: int):
    """
    Bullish demand OB:
    last bearish candle / bearish cluster before bullish displacement.

    Bearish supply OB:
    last bullish candle / bullish cluster before bearish displacement.
    """
    start_index = max(0, int(start_index))
    end_index = min(len(df) - 1, int(end_index))

    if end_index <= start_index:
        return None

    displacement_index = find_displacement_index(df, start_index, end_index, bias)

    ob_lookback = int(os.getenv("SMC_OB_LOOKBACK", "20"))
    max_cluster = int(os.getenv("SMC_OB_MAX_CLUSTER", "3"))

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

    while cluster_count < max_cluster:
        previous_idx = cluster_start - 1

        if previous_idx < search_start:
            break

        prev = df.loc[previous_idx]

        if bias == BULLISH:
            is_opposite = float(prev["close"]) < float(prev["open"])
        else:
            is_opposite = float(prev["close"]) > float(prev["open"])

        if not is_opposite:
            break

        cluster_start = previous_idx
        cluster_count += 1

    cluster = df.loc[cluster_start:chosen_idx]

    if cluster.empty:
        return None

    return {
        "type": ob_type,
        "bias": bias,
        "index": int(cluster_start),
        "end_index": int(chosen_idx),
        "displacement_index": int(displacement_index),
        "time": cluster.iloc[0]["time"],
        "end_time": cluster.iloc[-1]["time"],
        "high": float(cluster["high"].max()),
        "low": float(cluster["low"].min()),
        "open": float(cluster.iloc[0]["open"]),
        "close": float(cluster.iloc[-1]["close"]),
    }


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


def select_ob_by_preference(m15_ob, m5_ob):
    """Pick OB based on SMC_OB_TIMEFRAME_PREFERENCE env.

    Modes:
      - m15_then_m5 (default): always prefer M15 OB when available.
      - m5_then_m15: tight M5 entry first, fall back to M15.
      - most_recent: legacy behaviour, picks whichever OB formed last
        (almost always M5 because M5 candles are newer than M15).
    """
    pref = (os.getenv("SMC_OB_TIMEFRAME_PREFERENCE", "m15_then_m5") or "m15_then_m5").strip().lower()
    if pref == "most_recent":
        return most_recent_ob(m15_ob, m5_ob)
    if pref == "m5_then_m15":
        return m5_ob if m5_ob else m15_ob
    return m15_ob if m15_ob else m5_ob


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
    """Classify price within the active swing range.

    SMC_LOCATION_MODE controls how strict 'premium' / 'discount' are.

      - strict (default): only the deep ends count.
        premium  = price >= swing_low + SMC_PREMIUM_FIB * range  (default 0.618)
        discount = price <= swing_low + SMC_DISCOUNT_FIB * range (default 0.382)
        anything between is 'equilibrium', so retracement trades only fire
        from real OTE depth, not just past the 50% line.
      - classic: legacy 50% split with SMC_EQ_NEUTRAL_BAND neutral zone.
    """
    rng = swing_high - swing_low

    if rng <= 0:
        return "equilibrium"

    mode = (os.getenv("SMC_LOCATION_MODE", "strict") or "strict").strip().lower()

    if mode == "strict":
        premium_min_fib = float(os.getenv("SMC_PREMIUM_FIB", "0.618"))
        discount_max_fib = float(os.getenv("SMC_DISCOUNT_FIB", "0.382"))
        premium_threshold = swing_low + rng * premium_min_fib
        discount_threshold = swing_low + rng * discount_max_fib

        if price >= premium_threshold:
            return "premium"

        if price <= discount_threshold:
            return "discount"

        return "equilibrium"

    equilibrium = (swing_high + swing_low) / 2.0
    neutral_band = rng * float(os.getenv("SMC_EQ_NEUTRAL_BAND", "0.03"))

    if abs(price - equilibrium) <= neutral_band:
        return "equilibrium"

    if price > equilibrium:
        return "premium"

    return "discount"


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

    if trade_mode == "continuation":
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

    # Prefer the source OB that created the selected internal break event.
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

    m15_ob = last_valid_ob(
        m15_result["events"], m15, trade_direction, "M15",
        zone_low=zone_low, zone_high=zone_high
    )
    m5_ob = last_valid_ob(
        m5_result["events"], m5, trade_direction, "M5",
        zone_low=zone_low, zone_high=zone_high
    )

    selected_ob = source_selected_ob if source_selected_ob else select_ob_by_preference(m15_ob, m5_ob)
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

    show_ob_invalidations = os.getenv("SMC_SHOW_OB_INVALIDATIONS", "true").lower() == "true"
    flip_visual_only = os.getenv("SMC_FLIP_CANDIDATE_VISUAL_ONLY", "true").lower() == "true"
    diagnostic_decision = None
    if show_ob_invalidations and flip_visual_only:
        has_bull_flip = bool(ob_flip_candidates["m5_bullish_flip"] or ob_flip_candidates["m15_bullish_flip"])
        has_bear_flip = bool(ob_flip_candidates["m5_bearish_flip"] or ob_flip_candidates["m15_bearish_flip"])
        if external_bias == BULLISH and current_location == "premium" and has_bull_flip:
            diagnostic_decision = "WAIT_BUY_PULLBACK_AFTER_SUPPLY_INVALIDATION"
            decision = diagnostic_decision
            trade_mode = "diagnostic"
            trade_direction = None
            selected_ob = None
            zone_name = "bullish_flip_reference"
        elif external_bias == BEARISH and current_location == "discount" and has_bear_flip:
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
    show_h1_structure = env_bool("SMC_SHOW_H1_STRUCTURE", default=not is_clean_visual)
    show_h1_strong_weak = env_bool("SMC_SHOW_H1_STRONG_WEAK", default=not is_clean_visual)
    show_chart_flip_zones = env_bool("SMC_SHOW_FLIP_ZONES_ON_CHART", default=is_debug_visual)
    show_fib_labels_minimal = env_bool("SMC_SHOW_FIB_LABELS_MINIMAL", default=True)

    # DASHBOARD
    external_text = bias_text(external_bias)

    if internal_event_pack:
        internal_event = internal_event_pack["event"]
        internal_text = f"{internal_event_pack['timeframe']} {bias_text(internal_event['bias'])} {internal_event['tag']}"
    else:
        internal_text = "None"

    add_label(lines, "AI_SMC_DASHBOARD_1", 12, 22, f"AI SMC | {symbol}", "yellow")
    add_label(lines, "AI_SMC_DASHBOARD_2", 12, 42, f"External H1: {external_text} | Location: {current_location}", "yellow")
    add_label(lines, "AI_SMC_DASHBOARD_3", 12, 62, f"Internal: {internal_text}", "white")
    add_label(lines, "AI_SMC_DASHBOARD_4", 12, 82, f"Decision: {decision} | Mode: {trade_mode}", "white")

    dashboard_status_y = 102
    dashboard_active_y = 122
    if latest_flip:
        add_label(lines, "AI_SMC_DASHBOARD_FLIP", 12, 102, f"Flip: {latest_flip['timeframe']} {latest_flip['message']}", "orange")
        dashboard_status_y = 122
        dashboard_active_y = 142

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
    show_internal_structure = env_bool("SMC_SHOW_INTERNAL_STRUCTURE", default=not is_clean_visual)
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

    # INTERNAL SWINGS
    # Respect .env directly. Earlier versions blocked this in clean mode.
    show_internal_swings = env_bool("SMC_SHOW_INTERNAL_SWINGS", default=False)

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
        default=env_bool("SMC_CLEAN_SHOW_INTERNAL_STRONG_WEAK", default=not is_clean_visual),
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

        if is_debug_visual:
            _draw_internal_levels(internal_m5_structure, "cyan", "cyan")
            _draw_internal_levels(internal_m15_structure, "magenta", "magenta")
        else:
            active_tf = internal_event_pack["timeframe"] if internal_event_pack else "M5"
            active_levels = internal_m15_structure if active_tf == "M15" else internal_m5_structure
            _draw_internal_levels(active_levels, "cyan", "cyan")

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
        ob_label = f"{selected_ob['timeframe']} ACTIVE {'DEMAND' if selected_ob['bias'] == BULLISH else 'SUPPLY'} OB"

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

    if selected_ob and trade_direction:
        if trade_direction == BULLISH:
            entry = selected_ob["high"]
            stop_loss = selected_ob["low"]
            risk = entry - stop_loss
            if risk > 0:
                take_profit = entry + risk * rr
        elif trade_direction == BEARISH:
            entry = selected_ob["low"]
            stop_loss = selected_ob["high"]
            risk = stop_loss - entry
            if risk > 0:
                take_profit = entry - risk * rr

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
    add_label(lines, "AI_SMC_DASHBOARD_6", 12, dashboard_active_y, f"Active zone: {zone_name}", "white")

    output_path = mt5_common_files_dir() / "AI_SMC_OVERLAY.csv"
    output_path.write_text("\n".join(lines), encoding="utf-8")

    summary = {
        "symbol": symbol,
        "current_price": current_price,
        "external_h1_bias": external_text,
        "current_location": current_location,
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
        "h1_source_ob": h1_source_ob,
        "retrace_reference_ob": retrace_ref_ob,
        "selected_ob": selected_ob,
        "selected_ob_source": selected_ob_source,
        "selected_ob_locked": selected_ob_locked,
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
            "SMC_SHOW_INTERNAL_STRUCTURE": show_internal_structure,
            "SMC_SHOW_INTERNAL_SWINGS": show_internal_swings,
            "SMC_SHOW_INTERNAL_STRONG_WEAK": show_internal_strong_weak,
            "SMC_SHOW_PREVIOUS_STRUCTURE": show_previous_structure,
            "SMC_SHOW_FLIP_ZONES_ON_CHART": show_chart_flip_zones,
        },
        "overlay_file": str(output_path),
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