from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Optional

from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings, uses_openai_compatible_client
from app.gemini_env import gemini_api_key as gemini_api_key_from_env
from app.gemini_env import gemini_model_id, gemini_vision_model_id

logger = logging.getLogger(__name__)
settings = get_settings()


def _llm_no_retry_exception_types() -> tuple[type[BaseException], ...]:
    """Exceptions that must not be retried (auth, bad request, setup errors)."""
    types_list: list[type[BaseException]] = [
        ImportError,
        ModuleNotFoundError,
        ValueError,
        FileNotFoundError,
    ]
    try:
        import openai

        types_list.append(openai.AuthenticationError)
        types_list.append(openai.BadRequestError)
    except ImportError:
        pass
    try:
        from google.api_core import exceptions as gx

        types_list.extend(
            (
                gx.Unauthenticated,
                gx.PermissionDenied,
                gx.InvalidArgument,
            )
        )
    except ImportError:
        pass
    return tuple(types_list)


_LLM_NO_RETRY_EXCEPTIONS = _llm_no_retry_exception_types()


def _extract_gemini_text(response: Any) -> str:
    try:
        t = response.text
        if t:
            return t
    except (ValueError, AttributeError):
        pass
    try:
        cands = response.candidates
        if not cands:
            return ""
        parts = cands[0].content.parts
        return "".join(getattr(p, "text", "") or "" for p in parts)
    except (AttributeError, IndexError, KeyError, TypeError):
        return ""


def _prompt_section(prompt: str, start_marker: str, end_marker: str) -> str:
    if start_marker not in prompt:
        return ""
    tail = prompt.split(start_marker, 1)[1]
    if end_marker in tail:
        return tail.split(end_marker, 1)[0].strip()
    return tail.strip()

MOCK_RESPONSES = {
    "pine_analysis": """
## Pine Script Analysis

**Strategy Type:** Smart Money Concepts (SMC) breakout & reversal system

**Detected SMC Concepts:**
- Break of Structure (BOS) — used for trend confirmation
- Change of Character (CHOCH) — reversal signal
- Order Blocks (OB) — entry zone identification
- Fair Value Gaps (FVG) — liquidity imbalance zones
- Liquidity Sweep — stop-hunt detection before reversal
- Premium/Discount Zones — 50% Fibonacci-based entry filtering
- Session Filters — London and New York session awareness

**Entry Conditions:**
1. Bias determined by most recent BOS/CHOCH direction
2. Price returns to a valid, unmitigated Order Block
3. Order Block sits in discount (buy) or premium (sell) zone
4. FVG present inside or near Order Block
5. Liquidity sweep detected prior to entry candle
6. Displacement candle confirms entry direction

**Exit Conditions:**
- Take Profit: Previous swing high/low or 2R target
- Stop Loss: Below/above Order Block wick

**Key Parameters:**
- OB lookback: 10 bars
- Displacement threshold: 15 pips
- Session: London + NY only
- Risk per trade: 1%
""",
    "mql5_analysis": """
## MQL5 EA Analysis

**EA Structure:** Event-driven via OnTick() with state machine logic

**Entry Logic:**
- Mirrors Pine Script BOS/CHOCH detection using custom ZigZag
- Order Block detection via swing high/low identification
- Discount/Premium filter via iMA Fibonacci midpoint approximation

**Differences from Pine Script:**
1. Pine uses bar-close logic; EA uses tick-level evaluation (potential premature triggers)
2. Pine FVG detection is cleaner — EA uses simplified gap detection
3. Session filter in EA uses server time (may differ from chart time)
4. EA displacement threshold is fixed; Pine uses ATR-relative threshold

**Input Parameters:**
- OBLookback = 10
- DisplacementPips = 15
- RiskPercent = 1.0
- SessionFilter = true
- MaxSpreadPips = 3.0
""",
    "failure_analysis": """
## Failure Analysis Report

**Primary Failure Zones:**
1. **Late Asian Session (02:00–04:00 UTC)** — 68% loss rate, low liquidity noise
2. **News Events** — No filter causes false BOS signals
3. **Ranging Markets** — OB invalidation not detected, multiple re-entries on same OB
4. **Very Wide OBs** — SL placed too far, skews R:R below 1:1

**Missed Opportunities:**
1. After original bias exhaustion, valid counter-structure trades are skipped
2. Soft reversal setups (CHOCH after 3+ BOS in same direction) not traded
3. Second/third OB in same direction after first TP hit — missed continuation trades

**Improvement Priorities:**
1. Add soft reversal logic for counter-trades after bias exhaustion
2. Add OB quality filter (size, displacement, volume)
3. Add Asian session exclusion or reduced-size trading
4. Implement dynamic OB invalidation
""",
    "improvement_ideas": json.dumps([
        {
            "name": "Soft Reversal Counter-Trade",
            "category": "reversal",
            "logic_explanation": "After 3+ consecutive BOS in same direction, detect CHOCH and allow controlled counter-direction trade from premium/discount OB",
            "affected_component": "entry",
            "smc_reasoning": "In SMC, after displacement exhaustion, a CHOCH signals institutional reversal. The original bias is exhausted and a new structure is forming.",
            "expected_benefit": "Capture 15-25% more valid setups per month during trend exhaustion phases",
            "expected_risk": "May generate false entries in strong trending conditions",
            "parameters_changed": ["soft_reversal_bos_count", "soft_reversal_confirmation"],
            "overfit_risk": "medium",
        },
        {
            "name": "OB Quality Filter",
            "category": "filter",
            "logic_explanation": "Only trade OBs with displacement > 1.5x ATR14 and formed after a liquidity sweep",
            "affected_component": "entry",
            "smc_reasoning": "High-quality OBs are formed by institutional displacement. Small OBs without displacement are retail noise.",
            "expected_benefit": "Reduce low-quality entries, improve win rate by 5-10%",
            "expected_risk": "May reduce trade count by 20-30%",
            "parameters_changed": ["ob_displacement_factor", "require_liquidity_sweep"],
            "overfit_risk": "low",
        },
        {
            "name": "Asian Session Exclusion",
            "category": "session",
            "logic_explanation": "Disable trading during 22:00-06:00 UTC (Asian session) to avoid low-liquidity false signals",
            "affected_component": "filter",
            "smc_reasoning": "Asian session has lower institutional participation, leading to weaker OBs and more frequent stop hunts without follow-through",
            "expected_benefit": "Eliminate worst-performing session, improve profit factor",
            "expected_risk": "Miss occasional valid Asian session setups",
            "parameters_changed": ["session_start_hour", "session_end_hour"],
            "overfit_risk": "low",
        },
    ]),
}


class LLMService:
    def __init__(self) -> None:
        self._client = None
        self._provider = settings.llm_provider

    def _get_client(self):
        if self._client is not None:
            return self._client
        if settings.mock_llm or settings.mock_mode:
            return None
        if self._provider == "openai" or uses_openai_compatible_client(self._provider):
            try:
                from openai import OpenAI
            except ModuleNotFoundError as e:
                raise ModuleNotFoundError(
                    "The 'openai' package is not installed. On Python 3.14, "
                    "`pip install -r requirements.txt` may fail on pandas; run: "
                    "`pip install openai` (from the backend folder), then restart the server."
                ) from e
            kwargs: dict[str, Any] = {"api_key": settings.openai_api_key}
            if uses_openai_compatible_client(self._provider):
                kwargs["base_url"] = settings.local_llm_base_url
                kwargs["api_key"] = settings.local_llm_api_key
            self._client = OpenAI(**kwargs)
        elif self._provider == "anthropic":
            from anthropic import Anthropic
            self._client = Anthropic(api_key=settings.anthropic_api_key)
        return self._client

    def _model_name(self) -> str:
        if self._provider == "openai":
            return settings.openai_model
        if self._provider == "anthropic":
            return settings.anthropic_model
        if self._provider == "gemini":
            return gemini_model_id()
        return settings.local_llm_model

    def _vision_chat_model(self) -> str:
        """Model id for OpenAI-style multimodal chat (screenshots)."""
        if uses_openai_compatible_client(self._provider):
            v = (settings.local_llm_vision_model or "").strip()
            if v:
                return v
            return settings.local_llm_model
        return settings.vision_model

    def _complete_gemini(self, prompt: str, system: str, temperature: float) -> str:
        try:
            import google.generativeai as genai
        except ModuleNotFoundError as e:
            raise ModuleNotFoundError(
                "The 'google-generativeai' package is not installed. Run: pip install google-generativeai"
            ) from e
        key = gemini_api_key_from_env()
        if not key:
            raise ValueError(
                "GEMINI_API_KEY must be set in the environment (or backend/.env) when LLM_PROVIDER=gemini. "
                "Create a key in Google AI Studio; never commit API keys."
            )
        genai.configure(api_key=key)
        gen_cfg: dict[str, Any] = {
            "max_output_tokens": settings.gemini_max_output_tokens,
            "temperature": temperature,
        }
        mid = gemini_model_id()
        sys_inst = (system or "").strip() or None
        try:
            model = genai.GenerativeModel(mid, system_instruction=sys_inst)
        except TypeError:
            model = genai.GenerativeModel(mid)
            prompt = f"{system}\n\n{prompt}" if system else prompt
        resp = model.generate_content(prompt, generation_config=gen_cfg)
        return _extract_gemini_text(resp)

    def _analyze_image_gemini(self, image_path: Path, prompt: str, system: str) -> str:
        try:
            import google.generativeai as genai
            from PIL import Image
        except ModuleNotFoundError as e:
            raise ModuleNotFoundError(
                "Gemini vision needs 'google-generativeai' and 'Pillow'. "
                "Run: pip install google-generativeai Pillow"
            ) from e
        key = gemini_api_key_from_env()
        if not key:
            raise ValueError(
                "GEMINI_API_KEY must be set in the environment when LLM_PROVIDER=gemini."
            )
        genai.configure(api_key=key)
        model_name = gemini_vision_model_id()
        sys_inst = (system or "").strip() or None
        try:
            model = genai.GenerativeModel(model_name, system_instruction=sys_inst)
        except TypeError:
            model = genai.GenerativeModel(model_name)
            prompt = f"{system}\n\n{prompt}" if system else prompt
        img = Image.open(image_path)
        gen_cfg: dict[str, Any] = {
            "max_output_tokens": settings.gemini_max_output_tokens,
            "temperature": 0.2,
        }
        resp = model.generate_content([prompt, img], generation_config=gen_cfg)
        return _extract_gemini_text(resp)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_not_exception_type(_LLM_NO_RETRY_EXCEPTIONS),
        reraise=True,
    )
    def complete(self, prompt: str, system: str = "", temperature: float = 0.3) -> str:
        if settings.mock_llm or settings.mock_mode:
            return self._mock_response(prompt)
        if self._provider == "gemini":
            return self._complete_gemini(prompt, system, temperature)
        client = self._get_client()
        if self._provider == "anthropic":
            msg = client.messages.create(
                model=self._model_name(),
                max_tokens=settings.anthropic_max_tokens,
                system=system or "You are an expert quantitative trading analyst and SMC strategist.",
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            return msg.content[0].text
        else:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = client.chat.completions.create(
                model=self._model_name(),
                messages=messages,
                max_tokens=settings.openai_max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_not_exception_type(_LLM_NO_RETRY_EXCEPTIONS),
        reraise=True,
    )
    def analyze_image(self, image_path: str, prompt: str, system: str = "") -> str:
        if settings.mock_llm or settings.mock_mode:
            symbol = "Unknown"
            timeframe = "Unknown"
            for line in prompt.splitlines():
                if line.lower().startswith("symbol:"):
                    symbol = line.split(":", 1)[1].strip() or symbol
                if line.lower().startswith("timeframe:"):
                    timeframe = line.split(":", 1)[1].strip() or timeframe
            ref = _prompt_section(
                prompt,
                "--- REFERENCE KNOWLEDGE (from Word document) ---",
                "--- END REFERENCE KNOWLEDGE ---",
            )
            mq = _prompt_section(
                prompt,
                "--- MQL5 EA CODE EXCERPT (latest uploaded for project) ---",
                "--- END MQL5 EA CODE EXCERPT ---",
            )
            tv = _prompt_section(
                prompt,
                "--- TradingView chart context ---",
                "--- End TradingView context ---",
            )
            ref_note = ""
            if ref:
                snippet = ref[:900].strip()
                if len(ref) > 900:
                    snippet += "…"
                ref_note = (
                    "\n### Tie-in to your Word reference\n"
                    f"The following excerpt from your knowledge document frames how to read structure "
                    f"(mock mode cannot see the image pixels — cross-check on the chart yourself):\n\n"
                    f"> {snippet}\n"
                )
            mq_note = ""
            if mq:
                head = mq[:500].replace("\n", " ").strip()
                mq_note = (
                    "\n### EA code context (excerpt)\n"
                    f"Latest uploaded MQ5 begins with: «{head}…». "
                    "In mock mode, compare OB/BOS/session rules here against what you see on the screenshot.\n"
                )
            tv_note = ""
            if tv:
                tv_note = f"\n### TradingView URL context (fetched or parsed)\n{tv}\n"
            return (
                "## Chart Analysis (Mock)\n\n"
                f"Symbol (form hint): {symbol}\n"
                f"Timeframe (form hint): {timeframe}\n\n"
                "MOCK_MODE / MOCK_LLM is enabled: this is **not** a computer-vision read of your image. "
                "Always **verify the pair on the chart title** (e.g. GBPUSD vs EURUSD) before trusting form hints.\n"
                "When MOCK_LLM is off, the model must read the ticker from pixels per the screenshot prompt.\n"
                f"{tv_note}{ref_note}{mq_note}"
                "## Preliminary checklist (apply to your screenshot manually)\n"
                "- Market structure: mark last clear BOS / CHOCH per reference definitions\n"
                "- Liquidity: note recent sweep vs equal highs/lows before any OB reaction\n"
                "- OB / FVG: only unmitigated zones aligned with premium/discount of the active range\n"
                "- Session: confirm whether the move matches your EA session filter rules in MQ5\n\n"
                "## 8. Trade Recommendation and concrete plan (mock template)\n"
                "- EA action: **WAIT** (mock — enable real LLM for chart vision).\n"
                "- Hypothetical sizing used in live prompts: **USD 5,000** account, **1% = USD 50** risk, "
                "**minimum 1:3 R:R** (e.g. SL 25 pips → TP 75 pips on the same pair).\n"
                "- Example layout you should expect from a real run: **Entry OB** (bearish OB 1.3580–1.3600 "
                "for sells / bullish OB 1.3455–1.3485 for buys), **entry price**, **stop** beyond invalidation, "
                "**take profit** at ≥3× stop distance.\n\n"
                "## 9. EA Decision Assessment (mock)\n"
                "- Compare your stated EA decision to the checklist above; adjust if the chart contradicts MQ5 filters.\n"
                "- Setup quality: **D** (mock).\n"
            )
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")
        if self._provider == "gemini":
            return self._analyze_image_gemini(path, prompt, system)

        with open(path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")
        suffix = path.suffix.lower().lstrip(".")
        mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}
        mime_type = mime_map.get(suffix, "image/png")

        client = self._get_client()
        if self._provider == "anthropic":
            messages = [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": image_data}},
                {"type": "text", "text": prompt},
            ]}]
            msg = client.messages.create(
                model=settings.vision_model if "claude" in settings.vision_model else settings.anthropic_model,
                max_tokens=settings.anthropic_max_tokens,
                system=system or "You are an expert SMC chart analyst.",
                messages=messages,
            )
            return msg.content[0].text
        else:
            messages_list = []
            if system:
                messages_list.append({"role": "system", "content": system})
            messages_list.append({"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_data}"}},
                {"type": "text", "text": prompt},
            ]})
            resp = client.chat.completions.create(
                model=self._vision_chat_model(),
                messages=messages_list,
                max_tokens=settings.openai_max_tokens,
            )
            return resp.choices[0].message.content or ""

    def complete_json(self, prompt: str, system: str = "") -> Any:
        raw = self.complete(prompt, system=system, temperature=0.1)
        try:
            start = raw.find("{")
            if start == -1:
                start = raw.find("[")
            if start != -1:
                return json.loads(raw[start:])
        except json.JSONDecodeError:
            pass
        return {"raw": raw}

    def _mock_response(self, prompt: str) -> str:
        p = prompt.lower()
        if "pine" in p or "pinescript" in p:
            return MOCK_RESPONSES["pine_analysis"]
        if "mql5" in p or "expert advisor" in p or "ea code" in p:
            return MOCK_RESPONSES["mql5_analysis"]
        if "failure" in p or "where does" in p or "losing" in p:
            return MOCK_RESPONSES["failure_analysis"]
        if "improvement" in p or "suggest" in p or "improve" in p:
            return MOCK_RESPONSES["improvement_ideas"]
        return "Analysis complete. [Mock response — enable LLM provider in .env for real analysis]"


_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
