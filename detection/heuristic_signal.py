"""Signal 2: stylometric heuristics for how AI-generated a piece of text is.

Local and deterministic: no API calls, same text always gives the same score.
"""

import re
import statistics
from typing import Any

# Dense features (every text has a value) carry most of the weight. Sparse
# features (em dashes, contrast frames) are absent from most text of either
# kind, so a zero there should only nudge the score, not decide it.
# v1 values, from an 18-sample check (Gatsby/Twain vs gpt-oss-120b); see planning.md §2.
WEIGHTS = {
    "burstiness": 0.45,
    "vocabulary": 0.35,
    "em_dash": 0.10,
    "contrast_frames": 0.10,
}

# (raw value that maps to 0.0, raw value that maps to 1.0); linear in between.
MAPPINGS = {
    "em_dash_per_100w": (0.0, 1.0),
    "contrast_frames_per_100w": (0.0, 0.5),
    "sentence_length_cv": (0.60, 0.25),   # bursty = human, uniform = AI
    # Polished AI text uses *more* varied vocabulary than human prose
    # (sample: AI 0.85-0.93, human 0.61-0.85), so high MATTR leans AI.
    "mattr": (0.80, 0.92),
}

MATTR_WINDOW = 50
MIN_SENTENCES_FOR_BURSTINESS = 3

WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
SENTENCE_SPLIT_RE = re.compile(r"[.!?]+[\"')\]]*\s+|\n+")
EM_DASH_RE = re.compile(r"—|\s--\s|\s–\s")
CONTRAST_RES = [
    # "not just X, but Y" / "not only X; it's Y"
    re.compile(r"\bnot (?:just|only|merely|simply)\b[^.!?]{0,80}?\b(?:but|it's|it is|they're|they are)\b"),
    # "it's not X, it's Y" / "this isn't X — it's Y"
    re.compile(r"\b(?:it's|it is|this is|that's|that is)\s+not\b[^.!?]{0,60}?[,;—]\s*(?:it's|it is|this is|that's|but)\b"),
    re.compile(r"\b(?:it|this|that) isn't\b[^.!?]{0,60}?[,;—]\s*(?:it's|it is|this is|that's|but)\b"),
    # "not a weakness, but a strength". Requires a determiner on both sides so
    # everyday hedges like "I'm not sure, but..." don't match.
    re.compile(r"\bnot\s+(?:a|an|the|about|just)\b[^,.!?]{0,40},\s*but\s+(?:a|an|the|about|rather)\b"),
]


def _scale(value: float, zero_at: float, one_at: float) -> float:
    """Linearly map value onto 0-1, clamped. Works for either direction."""
    fraction = (value - zero_at) / (one_at - zero_at)
    return round(min(1.0, max(0.0, fraction)), 3)


def _sentences(text: str) -> list[str]:
    """Split on sentence punctuation and line breaks (so poems and lists split per line)."""
    return [s for s in SENTENCE_SPLIT_RE.split(text) if WORD_RE.search(s.lower())]


def _contrast_frame_count(text: str) -> int:
    """Number of sentences containing a contrast frame (each sentence counts once)."""
    return sum(
        1 for sentence in _sentences(text.lower())
        if any(pattern.search(sentence) for pattern in CONTRAST_RES)
    )


def _sentence_length_cv(text: str) -> float | None:
    """Coefficient of variation (std / mean) of words per sentence."""
    lengths = [len(WORD_RE.findall(s.lower())) for s in _sentences(text)]
    if len(lengths) < MIN_SENTENCES_FOR_BURSTINESS:
        return None
    return statistics.pstdev(lengths) / statistics.mean(lengths)


def _mattr(words: list[str]) -> float | None:
    """Moving-average type-token ratio: vocabulary variety that doesn't drop
    just because a text is longer (plain type-token ratio does)."""
    if not words:
        return None
    if len(words) <= MATTR_WINDOW:
        return len(set(words)) / len(words)
    ratios = [
        len(set(words[i:i + MATTR_WINDOW])) / MATTR_WINDOW
        for i in range(len(words) - MATTR_WINDOW + 1)
    ]
    return statistics.mean(ratios)


def get_heuristic_signal(text: str) -> dict[str, Any]:
    """Return {score, features, sub_scores} for the text (score 0 = human, 1 = AI)."""
    normalized = text.replace("’", "'").replace("‘", "'")
    lowered = normalized.lower()
    words = WORD_RE.findall(lowered)
    per_100_words = 100 / len(words) if words else 0.0

    cv = _sentence_length_cv(normalized)
    mattr = _mattr(words)
    features = {
        "em_dash_per_100w": round(len(EM_DASH_RE.findall(normalized)) * per_100_words, 3),
        "contrast_frames_per_100w": round(_contrast_frame_count(normalized) * per_100_words, 3),
        "sentence_length_cv": None if cv is None else round(cv, 3),
        "mattr": None if mattr is None else round(mattr, 3),
        "word_count": len(words),
    }

    # A feature that can't be measured (too few sentences or words) scores a
    # neutral 0.5 rather than counting as evidence either way.
    sub_scores = {
        "em_dash": _scale(features["em_dash_per_100w"], *MAPPINGS["em_dash_per_100w"]),
        "contrast_frames": _scale(features["contrast_frames_per_100w"], *MAPPINGS["contrast_frames_per_100w"]),
        "burstiness": 0.5 if cv is None else _scale(cv, *MAPPINGS["sentence_length_cv"]),
        "vocabulary": 0.5 if mattr is None else _scale(mattr, *MAPPINGS["mattr"]),
    }
    score = round(sum(WEIGHTS[name] * sub_scores[name] for name in WEIGHTS), 3)
    return {"score": score, "features": features, "sub_scores": sub_scores}
