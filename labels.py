"""Transparency labels: turn a scoring result into plain-language text (planning.md §3).

build_label() returns {label, why}. label is the fixed text for the attribution;
why explains this particular result from the signals' evidence.
"""

from typing import Any

LABELS = {
    "high_ai": (
        "Likely AI-generated. Our automated checks found strong signs that this text "
        "was written with an AI tool. This is an estimate, not proof. If you wrote this "
        "yourself, you can appeal and a person will review it."
    ),
    "uncertain": (
        "Unclear origin. Our automated checks found mixed or weak signals, so we're not "
        "calling this AI-written or human-written. {reason} If you wrote this and want a "
        "person to review it, you can appeal."
    ),
    "high_human": (
        "Likely human-written. Our automated checks found few signs of AI writing. "
        "This is an estimate, not a guarantee."
    ),
}

UNCERTAIN_REASONS = {
    "too_short": "The text is too short for our checks to work reliably.",
    "llm_unavailable": "One of our two checks was unavailable, so we didn't make a call.",
    "signals_disagree": "Our two checks disagreed with each other.",
    "leaning_human": "The signals lean slightly toward human writing.",
    "mixed": "The signals are evenly mixed.",
    "leaning_ai": "The signals lean toward AI writing, but not strongly enough to say so.",
}

UNDER_REVIEW_SUFFIX = "The creator has disputed this label. It is waiting for review by a person."

# Plain-language phrases for heuristic sub-scores that point clearly one way.
AI_SIDE_PHRASES = {
    "burstiness": "very evenly sized sentences",
    "vocabulary": "polished, highly varied word choice",
    "em_dash": "frequent em dashes",
    "contrast_frames": "\"it's not X, it's Y\" phrasing",
}
HUMAN_SIDE_PHRASES = {
    "burstiness": "a natural mix of short and long sentences",
    "vocabulary": "everyday, repeated word choice",
}
AI_SIDE_AT_OR_ABOVE = 0.7
HUMAN_SIDE_AT_OR_BELOW = 0.2


def _join(phrases: list[str]) -> str:
    if len(phrases) <= 1:
        return "".join(phrases)
    return ", ".join(phrases[:-1]) + " and " + phrases[-1]


def _leans(score: float | None) -> str:
    return "AI" if score is not None and score >= 0.5 else "human"


def _why(result: dict[str, Any], llm: dict[str, Any], heuristic: dict[str, Any]) -> str:
    decided_by = result["decided_by"]
    word_count = heuristic["features"]["word_count"]

    if decided_by == "too_short":
        return f"At {word_count} words, there isn't enough text to measure writing style reliably (we need at least 50)."
    if decided_by == "llm_unavailable":
        return "Only the writing-style check ran, and one check alone isn't enough to decide."
    if decided_by == "signals_disagree":
        return (
            f"The language model read this as more {_leans(llm['score'])}-like, but the "
            f"writing-style measurements looked more {_leans(heuristic['score'])}-like."
        )

    sub_scores = heuristic["sub_scores"]
    if result["confidence"] >= 0.5:
        phrases = [AI_SIDE_PHRASES[name] for name, value in sub_scores.items()
                   if name in AI_SIDE_PHRASES and value >= AI_SIDE_AT_OR_ABOVE]
    else:
        phrases = [HUMAN_SIDE_PHRASES[name] for name, value in sub_scores.items()
                   if name in HUMAN_SIDE_PHRASES and value <= HUMAN_SIDE_AT_OR_BELOW]

    parts = []
    if phrases:
        parts.append(f"Writing style: {_join(phrases)}.")
    if llm.get("reasons"):
        reason = llm["reasons"][0].rstrip(".")
        parts.append(f"The language model noted: {reason[0].lower() + reason[1:]}.")
    return " ".join(parts) or "No single feature stood out; the result comes from the overall balance of signals."


def build_label(result: dict[str, Any], llm: dict[str, Any], heuristic: dict[str, Any]) -> dict[str, str]:
    """Return {label, why} for a scoring.score() result and the two signal outputs."""
    attribution = result["attribution"]
    label = LABELS[attribution]
    if attribution == "uncertain":
        reason_key = result["lean"] or result["decided_by"]
        label = label.format(reason=UNCERTAIN_REASONS[reason_key])
    return {"label": label, "why": _why(result, llm, heuristic)}
