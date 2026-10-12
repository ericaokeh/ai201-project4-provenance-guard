"""Unit tests for labels.build_label(): all three variants and every uncertain reason.

Results come from the real scoring.score(), so these also check the two modules fit together.
"""

import unittest

from labels import LABELS, UNCERTAIN_REASONS, build_label
from scoring import score


def llm(value, reasons=("Polished, uniform corporate language",)):
    return {"score": value, "available": value is not None, "reasons": list(reasons)}


def heur(value, word_count=200, **sub_scores):
    subs = {"burstiness": 0.5, "vocabulary": 0.5, "em_dash": 0.0, "contrast_frames": 0.0, **sub_scores}
    return {"score": value, "features": {"word_count": word_count}, "sub_scores": subs}


def label_for(l, h):
    return build_label(score(l, h, h["features"]["word_count"]), l, h)


class VariantTests(unittest.TestCase):
    def test_high_ai(self):
        out = label_for(llm(0.9), heur(0.85, burstiness=0.9, vocabulary=0.8))
        self.assertEqual(out["label"], LABELS["high_ai"])
        self.assertIn("very evenly sized sentences and polished, highly varied word choice", out["why"])
        self.assertIn("The language model noted: polished, uniform corporate language.", out["why"])

    def test_high_human(self):
        out = label_for(llm(0.1, ["Informal slang and personal anecdote"]), heur(0.1, burstiness=0.0))
        self.assertEqual(out["label"], LABELS["high_human"])
        self.assertIn("a natural mix of short and long sentences", out["why"])

    def test_every_label_says_estimate_or_mixed(self):
        for text in LABELS.values():
            self.assertTrue("estimate" in text or "mixed or weak" in text)


class UncertainReasonTests(unittest.TestCase):
    def assert_reason(self, out, key):
        self.assertTrue(out["label"].startswith("Unclear origin."))
        self.assertIn(UNCERTAIN_REASONS[key], out["label"])
        self.assertNotIn("{reason}", out["label"])

    def test_too_short(self):
        out = label_for(llm(0.9), heur(0.9, word_count=30))
        self.assert_reason(out, "too_short")
        self.assertIn("At 30 words", out["why"])

    def test_llm_unavailable(self):
        self.assert_reason(label_for(llm(None, ()), heur(0.6)), "llm_unavailable")

    def test_signals_disagree_names_both_directions(self):
        out = label_for(llm(0.88), heur(0.40))
        self.assert_reason(out, "signals_disagree")
        self.assertIn("language model read this as more AI-like", out["why"])
        self.assertIn("writing-style measurements looked more human-like", out["why"])

    def test_lean_bands(self):
        self.assert_reason(label_for(llm(0.40), heur(0.40)), "leaning_human")
        self.assert_reason(label_for(llm(0.55), heur(0.55)), "mixed")
        self.assert_reason(label_for(llm(0.72), heur(0.72)), "leaning_ai")


if __name__ == "__main__":
    unittest.main()
