"""End-to-end tests for /submit -> /appeal -> /content, /appeals and /log.

Uses a throwaway SQLite file and a stubbed LLM signal, so no Groq calls are made
and the real audit_log.db is never touched.
"""

import os
import tempfile
import unittest
from unittest import mock

import audit_log

AI_TEXT = " ".join(["Organizations can unlock unprecedented value through collaboration."] * 12)
HUMAN_TEXT = " ".join(["ok so my cat sat in the plumber's toolbox again lol, he was not thrilled."] * 6)
# These tests are about appeals, not detection, so both signal scores are pinned.


def setUpModule():
    global app, client, db_path
    handle, db_path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    audit_log.DB_PATH = db_path
    import app as app_module  # imported after DB_PATH is redirected
    app = app_module
    app.limiter.enabled = False
    client = app.app.test_client()


def tearDownModule():
    os.remove(db_path)


def submit(text, creator_id="writer-1", llm_score=0.9):
    llm_stub = {"score": llm_score, "reasons": ["Generic tone"], "model": "stub", "available": True}
    real_heuristic = app.get_heuristic_signal

    def heuristic_stub(t):
        return {**real_heuristic(t), "score": llm_score}  # real features/word count, pinned score

    with mock.patch.object(app, "get_llm_signal", return_value=llm_stub), \
         mock.patch.object(app, "get_heuristic_signal", side_effect=heuristic_stub):
        response = client.post("/submit", json={"text": text, "creator_id": creator_id})
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def appeal(submission, **overrides):
    body = {
        "content_id": submission["content_id"],
        "creator_id": submission["creator_id"],
        "appeal_token": submission["appeal_token"],
        "creator_reasoning": "I wrote this myself; my drafts are in Google Docs history.",
        **overrides,
    }
    return client.post("/appeal", json=body)


class AppealTests(unittest.TestCase):
    def test_successful_appeal_updates_status_and_logs(self):
        sub = submit(AI_TEXT)
        self.assertEqual(sub["attribution"], "high_ai")

        response = appeal(sub, evidence="https://docs.example/drafts")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual((body["status"], body["original_attribution"]), ("under_review", "high_ai"))

        content = client.get(f"/content/{sub['content_id']}").get_json()
        self.assertEqual(content["status"], "under_review")
        self.assertEqual(content["attribution"], "high_ai")  # original label unchanged
        self.assertTrue(content["label"].endswith(app.UNDER_REVIEW_SUFFIX))
        self.assertEqual(content["appeal"]["appeal_id"], body["appeal_id"])

        entries = client.get(f"/log?content_id={sub['content_id']}").get_json()["entries"]
        self.assertEqual([e["event_type"] for e in entries], ["appeal", "classification"])
        appeal_entry, classification = entries
        self.assertEqual(appeal_entry["appeal_id"], body["appeal_id"])
        self.assertEqual(appeal_entry["status"], "under_review")
        self.assertIn("drafts are in Google Docs", appeal_entry["creator_reasoning"])
        self.assertEqual(classification["status"], "classified")  # log row never rewritten
        self.assertEqual(classification["signals"]["llm"]["score"], 0.9)

        queue = client.get("/appeals").get_json()["appeals"]
        item = next(a for a in queue if a["appeal_id"] == body["appeal_id"])
        self.assertEqual(item["text"], AI_TEXT)
        self.assertEqual(item["evidence"], "https://docs.example/drafts")
        self.assertIn("heuristic", item["signals"])

    def test_second_appeal_is_409(self):
        sub = submit(AI_TEXT)
        self.assertEqual(appeal(sub).status_code, 200)
        self.assertEqual(appeal(sub).status_code, 409)

    def test_wrong_token_and_wrong_creator_get_same_403(self):
        sub = submit(AI_TEXT)
        wrong_token = appeal(sub, appeal_token="not-the-token")
        wrong_creator = appeal(sub, creator_id="someone-else")
        self.assertEqual((wrong_token.status_code, wrong_creator.status_code), (403, 403))
        self.assertEqual(wrong_token.get_json(), wrong_creator.get_json())
        self.assertEqual(client.get(f"/content/{sub['content_id']}").get_json()["status"], "classified")

    def test_high_human_cannot_be_appealed(self):
        sub = submit(HUMAN_TEXT, llm_score=0.05)
        self.assertEqual(sub["attribution"], "high_human")
        self.assertEqual(appeal(sub).status_code, 400)

    def test_uncertain_can_be_appealed(self):
        sub = submit("Too short to judge, but I wrote it.", llm_score=0.9)
        self.assertEqual(sub["decided_by"], "too_short")
        self.assertEqual(appeal(sub).status_code, 200)

    def test_unknown_content_is_404(self):
        response = client.post("/appeal", json={
            "content_id": "nope", "creator_id": "x", "appeal_token": "y", "creator_reasoning": "long enough reason",
        })
        self.assertEqual(response.status_code, 404)

    def test_validation(self):
        sub = submit(AI_TEXT)
        self.assertEqual(appeal(sub, creator_reasoning="short").status_code, 400)
        self.assertEqual(appeal(sub, creator_reasoning=None).status_code, 400)
        self.assertEqual(client.post("/appeal", data="x").status_code, 400)

    def test_token_never_exposed_after_submit(self):
        sub = submit(AI_TEXT)
        appeal(sub)
        for path in (f"/content/{sub['content_id']}", "/appeals", f"/log?content_id={sub['content_id']}"):
            body = client.get(path).get_data(as_text=True)
            self.assertNotIn(sub["appeal_token"], body)
            self.assertNotIn("appeal_token_hash", body)


if __name__ == "__main__":
    unittest.main()
