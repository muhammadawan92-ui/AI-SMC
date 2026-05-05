from __future__ import annotations

import logging
from typing import Any

from app.services.llm_service import get_llm_service
from app.services.smc_logic_service import SMC_KNOWLEDGE_BASE, validate_trade_against_smc

logger = logging.getLogger(__name__)

AGENT_SYSTEM = """You are the EA Research Agent for an AI-assisted trading system.
Your role:
1. Understand Pine Script and MQL5 EA logic deeply
2. Apply Smart Money Concepts (SMC) principles to evaluate trades
3. Generate structured improvement hypotheses
4. Evaluate backtest results with statistical rigor
5. Never recommend changes without clear SMC reasoning
6. Always protect the profitable baseline — do not suggest breaking changes without validation
7. Think like a professional SMC trader, not a random optimizer

When analyzing, always:
- Reference specific SMC concepts (BOS, CHOCH, OB, FVG, liquidity)
- Explain WHY a pattern works or fails from an institutional perspective
- Consider drawdown impact, not just profit
- Flag potential overfitting risks
- Distinguish between statistical noise and genuine edge"""


class StrategyAgent:
    def __init__(self) -> None:
        self.llm = get_llm_service()
        self.conversation_history: list[dict[str, str]] = []

    def ask(self, question: str, context: dict[str, Any] | None = None) -> str:
        """Ask the agent a question about the strategy."""
        context_str = ""
        if context:
            context_str = "\n\nContext:\n" + "\n".join(f"{k}: {v}" for k, v in context.items())
        prompt = question + context_str
        response = self.llm.complete(prompt, system=AGENT_SYSTEM)
        self.conversation_history.append({"role": "user", "content": question})
        self.conversation_history.append({"role": "assistant", "content": response})
        return response

    def evaluate_trade_setup(self, setup: dict[str, Any]) -> dict:
        """Evaluate a specific trade setup against SMC rules."""
        smc_validation = validate_trade_against_smc(setup)
        ai_notes = self.llm.complete(
            f"Evaluate this trade setup:\n{setup}\n\nSMC validation result: {smc_validation}\n\n"
            "Provide your assessment: Should this trade be taken? What improvements to the setup would increase confidence?",
            system=AGENT_SYSTEM,
        )
        return {**smc_validation, "ai_notes": ai_notes}

    def explain_concept(self, concept: str) -> str:
        """Explain an SMC concept in the context of the EA."""
        knowledge = SMC_KNOWLEDGE_BASE.get(concept)
        if not knowledge:
            return self.llm.complete(
                f"Explain the SMC concept: {concept} and how it would apply to an automated EA.",
                system=AGENT_SYSTEM,
            )
        return self.llm.complete(
            f"Explain {knowledge['name']} in depth:\n{knowledge['description']}\n\n"
            "How should this be implemented in an automated EA? What are the key edge cases?",
            system=AGENT_SYSTEM,
        )

    def review_improvement(self, improvement: dict) -> dict:
        """Review an improvement idea for quality and overfitting risk."""
        prompt = f"""Review this EA improvement idea:

Name: {improvement.get('name')}
Logic: {improvement.get('logic_explanation')}
SMC Reasoning: {improvement.get('smc_reasoning')}
Expected Benefit: {improvement.get('expected_benefit')}
Expected Risk: {improvement.get('expected_risk')}
Parameters Changed: {improvement.get('parameters_changed')}
Overfit Risk: {improvement.get('overfit_risk')}

Evaluate:
1. Is the SMC reasoning sound? (1-10)
2. What is the actual overfitting risk? (low/medium/high with explanation)
3. What validation tests should be run?
4. What could go wrong?
5. Is this worth testing? (yes/no with explanation)

Be critical and objective."""
        review = self.llm.complete(prompt, system=AGENT_SYSTEM)
        return {
            "improvement_name": improvement.get("name"),
            "review": review,
        }

    def clear_history(self) -> None:
        self.conversation_history = []
