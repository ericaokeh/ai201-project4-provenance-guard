"""Confidence scoring: combine both detection signals into one score and attribution.

Implements planning.md §2. Rules are checked in order and the first match decides
the attribution; its name is returned as decided_by and written to the audit log.
"""

from typing import Any

SCORING_CONFIG = {
    "scoring_version": "v1",
    "llm_weight": 0.6,
    "heuristic_weight": 0.4,
    "min_words": 50,
    "max_signal_gap": 0.40,
    "high_ai_at_or_above": 0.80,
    "high_human_at_or_below": 0.35,
    # Uncertain sub-bands only change the wording of the label, never the attribution.
    "lean_bands": [(0.50, "leaning_human"), (0.65, "mixed"), (0.80, "leaning_ai")],
}


def _lean(confidence: float) -> str:
    for upper, name in SCORING_CONFIG["lean_bands"]:
        if confidence < upper:
            return name
    return "leaning_ai"


def score(llm: dict[str, Any], heuristic: dict[str, Any], word_count: int) -> dict[str, Any]:
    """Return {confidence, attribution, decided_by, lean, scoring_version}.

    confidence is a 0-1 AI-likelihood (0 = human, 1 = AI), not a probability.
    It is None when the LLM signal is unavailable.
    """
    config = SCORING_CONFIG
    llm_score = llm.get("score") if llm.get("available") else None
    heuristic_score = heuristic["score"]

    confidence = None
    if llm_score is not None:
        confidence = round(
            config["llm_weight"] * llm_score + config["heuristic_weight"] * heuristic_score, 2
        )

    def result(attribution: str, decided_by: str, lean: str | None = None) -> dict[str, Any]:
        return {
            "confidence": confidence,
            "attribution": attribution,
            "decided_by": decided_by,
            "lean": lean,
            "scoring_version": config["scoring_version"],
        }

    if word_count < config["min_words"]:
        return result("uncertain", "too_short")
    if llm_score is None:
        return result("uncertain", "llm_unavailable")
    # Rounded so float noise (0.85 - 0.45 = 0.39999...) can't flip the rule.
    if round(abs(llm_score - heuristic_score), 3) > config["max_signal_gap"]:
        return result("uncertain", "signals_disagree")
    if confidence >= config["high_ai_at_or_above"]:
        return result("high_ai", "threshold_high_ai")
    if confidence <= config["high_human_at_or_below"]:
        return result("high_human", "threshold_high_human")
    return result("uncertain", "threshold_uncertain", _lean(confidence))
