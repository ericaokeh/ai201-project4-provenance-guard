"""Unit tests for scoring.score(): every rule and every threshold boundary.

Run from the project root: python -m unittest discover tests
"""

import unittest

from scoring import score


def llm(value, available=True):
    return {"score": value if available else None, "available": available}


def heur(value):
    return {"score": value}


class RuleOrderTests(unittest.TestCase):
    def test_too_short_wins_over_everything(self):
        r = score(llm(0.95), heur(0.95), word_count=49)
        self.assertEqual((r["attribution"], r["decided_by"]), ("uncertain", "too_short"))
        self.assertEqual(r["confidence"], 0.95)  # still computed and logged

    def test_50_words_is_long_enough(self):
        self.assertEqual(score(llm(0.95), heur(0.95), 50)["decided_by"], "threshold_high_ai")

    def test_llm_unavailable(self):
        r = score(llm(None, available=False), heur(0.9), 200)
        self.assertEqual((r["attribution"], r["decided_by"], r["confidence"]),
                         ("uncertain", "llm_unavailable", None))

    def test_gap_over_040_disagrees(self):
        r = score(llm(0.90), heur(0.49), 200)  # gap 0.41
        self.assertEqual(r["decided_by"], "signals_disagree")

    def test_gap_of_exactly_040_does_not_disagree(self):
        r = score(llm(0.85), heur(0.45), 200)  # 0.85 - 0.45 is 0.3999... in floats
        self.assertNotEqual(r["decided_by"], "signals_disagree")

    def test_disagreement_overrides_high_average(self):
        r = score(llm(1.0), heur(0.55), 200)  # combined 0.82, but gap 0.45
        self.assertEqual((r["attribution"], r["decided_by"]), ("uncertain", "signals_disagree"))


class ThresholdTests(unittest.TestCase):
    def test_exactly_080_is_high_ai(self):
        r = score(llm(0.80), heur(0.80), 200)
        self.assertEqual((r["confidence"], r["attribution"]), (0.80, "high_ai"))

    def test_079_is_uncertain_leaning_ai(self):
        r = score(llm(0.79), heur(0.79), 200)
        self.assertEqual((r["attribution"], r["lean"]), ("uncertain", "leaning_ai"))

    def test_exactly_035_is_high_human(self):
        r = score(llm(0.35), heur(0.35), 200)
        self.assertEqual((r["confidence"], r["attribution"]), (0.35, "high_human"))

    def test_036_is_uncertain_leaning_human(self):
        r = score(llm(0.36), heur(0.36), 200)
        self.assertEqual((r["attribution"], r["lean"]), ("uncertain", "leaning_human"))

    def test_mixed_band(self):
        self.assertEqual(score(llm(0.60), heur(0.60), 200)["lean"], "mixed")

    def test_weighting_is_60_40(self):
        self.assertEqual(score(llm(1.0), heur(0.7), 200)["confidence"], 0.88)

    def test_lean_only_set_for_threshold_uncertain(self):
        self.assertIsNone(score(llm(0.9), heur(0.9), 200)["lean"])
        self.assertIsNone(score(llm(0.6), heur(0.6), 10)["lean"])


if __name__ == "__main__":
    unittest.main()
