from __future__ import annotations

import os
from typing import Any

import pandas as pd

BULLISH = 1
BEARISH = -1


def find_pivots(df: pd.DataFrame, length: int):
    highs = []
    lows = []
    if len(df) < length * 2 + 5:
        return highs, lows
    for i in range(length, len(df) - length):
        window = df.iloc[i - length : i + length + 1]
        current_high = float(df.iloc[i]["high"])
        current_low = float(df.iloc[i]["low"])
        if current_high >= float(window["high"].max()):
            highs.append(
                {
                    "index": i,
                    "confirm_index": i + length,
                    "time": df.iloc[i]["time"],
                    "price": current_high,
                    "crossed": False,
                }
            )
        if current_low <= float(window["low"].min()):
            lows.append(
                {
                    "index": i,
                    "confirm_index": i + length,
                    "time": df.iloc[i]["time"],
                    "price": current_low,
                    "crossed": False,
                }
            )
    return highs, lows


def find_displacement_index(df: pd.DataFrame, start_index: int, end_index: int, bias: int):
    start_index = max(0, int(start_index))
    end_index = min(len(df) - 1, int(end_index))
    lookback = int(os.getenv("SMC_DISPLACEMENT_LOOKBACK", "30"))
    body_mult = float(os.getenv("SMC_DISPLACEMENT_BODY_MULT", "1.5"))
    range_mult = float(os.getenv("SMC_DISPLACEMENT_RANGE_MULT", "1.2"))
    min_body_ratio = float(os.getenv("SMC_MIN_BODY_TO_RANGE", "0.45"))
    focus_start = max(start_index, end_index - lookback)
    segment = df.iloc[focus_start : end_index + 1].copy()
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
        same_direction = (
            float(row["close"]) > float(row["open"])
            if bias == BULLISH
            else float(row["close"]) < float(row["open"])
        )
        strong_body = avg_body > 0 and body >= avg_body * body_mult
        strong_range = avg_range > 0 and candle_range >= avg_range * range_mult
        clean_body = body_ratio >= min_body_ratio
        if same_direction and clean_body and (strong_body or strong_range):
            candidates.append(idx)
    return int(candidates[-1]) if candidates else end_index


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
    highs_by_confirm: dict[int, list[dict[str, Any]]] = {}
    lows_by_confirm: dict[int, list[dict[str, Any]]] = {}
    for p in pivot_highs:
        highs_by_confirm.setdefault(p["confirm_index"], []).append(p)
    for p in pivot_lows:
        lows_by_confirm.setdefault(p["confirm_index"], []).append(p)
    current_high_pivot = None
    current_low_pivot = None
    trend = 0
    events = []
    for i in range(len(df)):
        if i in highs_by_confirm:
            current_high_pivot = highs_by_confirm[i][-1]
        if i in lows_by_confirm:
            current_low_pivot = lows_by_confirm[i][-1]
        close_price = float(df.iloc[i]["close"])
        bar_time = df.iloc[i]["time"]
        if current_high_pivot and not current_high_pivot["crossed"] and close_price > current_high_pivot["price"]:
            tag = "CHoCH" if trend == BEARISH else "BOS"
            ob = find_order_block(df, current_high_pivot["index"], i, BULLISH)
            events.append(
                {
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
                }
            )
            current_high_pivot["crossed"] = True
            trend = BULLISH
        if current_low_pivot and not current_low_pivot["crossed"] and close_price < current_low_pivot["price"]:
            tag = "CHoCH" if trend == BULLISH else "BOS"
            ob = find_order_block(df, current_low_pivot["index"], i, BEARISH)
            events.append(
                {
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
                }
            )
            current_low_pivot["crossed"] = True
            trend = BEARISH
    return {"trend": trend, "events": events, "pivot_highs": pivot_highs, "pivot_lows": pivot_lows}


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
            candidates.append({"timeframe": "M15", "event": m15_last, "age": m15_age})
    if m5_last:
        m5_age = len(m5) - 1 - int(m5_last["break_index"])
        if m5_age <= max_m5_age:
            candidates.append({"timeframe": "M5", "event": m5_last, "age": m5_age})
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
        return bool((after["close"] < ob["low"]).any()) if use_close else bool((after["low"] < ob["low"]).any())
    if ob["type"] == "supply":
        return bool((after["close"] > ob["high"]).any()) if use_close else bool((after["high"] > ob["high"]).any())
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




def price_inside_ob(price: float, ob: dict | None) -> bool:
    if not ob:
        return False
    return float(ob["low"]) <= float(price) <= float(ob["high"])


def get_h1_context_ob(trade_direction, trade_mode: str, current_price: float, h1_supply_ob: dict | None, h1_demand_ob: dict | None):
    if str(os.getenv("SMC_H1_OB_REFINEMENT_ENABLED", "true")).strip().lower() not in {"1", "true", "yes", "y", "on"}:
        return None
    if trade_mode != "retracement":
        return None
    require_inside = str(os.getenv("SMC_H1_OB_REQUIRE_PRICE_INSIDE", "true")).strip().lower() in {"1", "true", "yes", "y", "on"}
    if trade_direction == BEARISH and h1_supply_ob:
        if not require_inside or price_inside_ob(current_price, h1_supply_ob):
            return h1_supply_ob
    if trade_direction == BULLISH and h1_demand_ob:
        if not require_inside or price_inside_ob(current_price, h1_demand_ob):
            return h1_demand_ob
    return None


def last_valid_ob(events, df: pd.DataFrame, bias: int, timeframe_label: str, zone_low=None, zone_high=None):
    for event in reversed(events):
        if event["bias"] != bias:
            continue
        ob = event.get("order_block")
        if not ob:
            continue
        if is_ob_invalidated(df, ob, use_close=True):
            continue
        if zone_low is not None and zone_high is not None and not ob_overlaps_zone(ob, zone_low, zone_high):
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
        low_index = int(candidate_lows[-1]["index"]) if candidate_lows else int(
            h1.iloc[max(0, break_index - 120) : break_index + 1]["low"].idxmin()
        )
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
    high_index = int(candidate_highs[-1]["index"]) if candidate_highs else int(
        h1.iloc[max(0, break_index - 120) : break_index + 1]["high"].idxmax()
    )
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
        result[level] = float(swing_low + rng * level if bias == BEARISH else swing_high - rng * level)
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
    internal_break_location = price_location(float(internal_event["break_close"]), swing_low, swing_high)
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
        return None, None, None, None
    poi_top = max(fibs[0.618], fibs[0.886])
    poi_bottom = min(fibs[0.618], fibs[0.886])

    h1_context_ob = get_h1_context_ob(
        trade_direction,
        trade_mode,
        float(current_price) if current_price is not None else float(equilibrium),
        h1_supply_ob,
        h1_demand_ob,
    )

    zone_name = "none"
    if h1_context_ob:
        zone_low = float(h1_context_ob["low"])
        zone_high = float(h1_context_ob["high"])
        zone_name = "h1_supply_refinement_zone" if trade_direction == BEARISH else "h1_demand_refinement_zone"
    elif trade_mode == "continuation":
        zone_low = poi_bottom
        zone_high = poi_top
        zone_name = "external_fib_poi"
    else:
        if trade_direction == BEARISH:
            zone_low = equilibrium
            zone_high = swing_high
            zone_name = "premium_retracement_zone"
        else:
            zone_low = swing_low
            zone_high = equilibrium
            zone_name = "discount_retracement_zone"

    m15_ob = last_valid_ob(m15_result["events"], m15, trade_direction, "M15", zone_low=zone_low, zone_high=zone_high)
    m5_ob = last_valid_ob(m5_result["events"], m5, trade_direction, "M5", zone_low=zone_low, zone_high=zone_high)

    if h1_context_ob and str(os.getenv("SMC_H1_OB_M15_FIRST", "true")).strip().lower() in {"1", "true", "yes", "y", "on"}:
        selected_ob = m15_ob if m15_ob else m5_ob
    else:
        selected_ob = most_recent_ob(m15_ob, m5_ob)

    if selected_ob and h1_context_ob:
        selected_ob = dict(selected_ob)
        selected_ob["h1_context_ob"] = h1_context_ob
        selected_ob["inside_h1_ob"] = True
        selected_ob["h1_refinement_priority"] = "M15_FIRST"

    return selected_ob, m15_ob, m5_ob, zone_name


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
