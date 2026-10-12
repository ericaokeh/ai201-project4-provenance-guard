"""End-to-end tests for /submit -> /appeal -> /content, /appeals and /log.

Uses a throwaway SQLite file and a stubbed LLM signal, so no Groq calls are made
and the real audit_log.db is never touched.
"""

import os
import tempfile
import unittest
from unittest import mock

import audit_log
from labels import UNDER_REVIEW_SUFFIX

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
    assert response.status_code == 200, response.get_json()
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
        self.assertTrue(content["label"].endswith(UNDER_REVIEW_SUFFIX))
        self.assertEqual(content["appeal"]["appeal_id"], body["appeal_id"])

        entries = client.get(f"/log?content_id={sub['content_id']}").get_json()["entries"]
        self.assertEqual([e["event_type"] for e in entries], ["appeal", "classification"])
        appeal_entry, classification = entries
        self.assertEqual(appeal_entry["appeal_id"], body["appeal_id"])
        self.assertEqual(appeal_entry["status"], "under_review")
        self.assertIn("drafts are in Google Docs", appeal_entry["creator_reasoning"])
        self.assertEqual(classification["status"], "classified")  # log row never rewritten
        self.assertEqual(classification["signals"]["llm"]["score"], 0.9)
        self.assertTrue(classification["appeal_filed"])  # computed at read time
        self.assertTrue(appeal_entry["appeal_filed"])

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
        entries = client.get(f"/log?content_id={sub['content_id']}").get_json()["entries"]
        self.assertEqual([e["appeal_filed"] for e in entries], [False])

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


def resolve(appeal_id, **overrides):
    body = {"decision": "overturned", "reviewer_id": "reviewer-1",
            "reviewer_notes": "Drafts in version history confirm the creator wrote it.", **overrides}
    return client.post(f"/appeals/{appeal_id}/resolve", json=body)


class ResolveTests(unittest.TestCase):
    def appealed(self):
        sub = submit(AI_TEXT)
        return sub, appeal(sub).get_json()["appeal_id"]

    def test_overturned_relabels_and_logs(self):
        sub, appeal_id = self.appealed()
        response = resolve(appeal_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "appeal_overturned")

        content = client.get(f"/content/{sub['content_id']}").get_json()
        self.assertEqual(content["status"], "appeal_overturned")
        self.assertEqual(content["attribution"], "high_ai")  # original decision kept
        self.assertTrue(content["label"].startswith("Human-written (confirmed on review)."))
        self.assertIn('"Likely AI-generated"', content["label"])
        self.assertEqual(content["appeal"]["decision"], "overturned")

        entries = client.get(f"/log?content_id={sub['content_id']}").get_json()["entries"]
        self.assertEqual([e["event_type"] for e in entries], ["appeal_resolved", "appeal", "classification"])
        resolved = entries[0]
        self.assertEqual((resolved["decision"], resolved["reviewer_id"], resolved["status"]),
                         ("overturned", "reviewer-1", "appeal_overturned"))
        self.assertEqual(resolved["attribution"], "high_ai")
        self.assertEqual([e["status"] for e in entries[1:]], ["under_review", "classified"])  # never rewritten

    def test_upheld_keeps_label_with_suffix(self):
        sub, appeal_id = self.appealed()
        self.assertEqual(resolve(appeal_id, decision="upheld").status_code, 200)
        content = client.get(f"/content/{sub['content_id']}").get_json()
        self.assertEqual(content["status"], "appeal_upheld")
        self.assertTrue(content["label"].startswith("Likely AI-generated."))
        self.assertTrue(content["label"].endswith("A person reviewed the creator's appeal and kept this label."))

    def test_resolved_leaves_queue_and_appears_under_its_status(self):
        _, appeal_id = self.appealed()
        resolve(appeal_id, decision="upheld")
        pending = [a["appeal_id"] for a in client.get("/appeals").get_json()["appeals"]]
        upheld = client.get("/appeals?status=appeal_upheld").get_json()["appeals"]
        self.assertNotIn(appeal_id, pending)
        item = next(a for a in upheld if a["appeal_id"] == appeal_id)
        self.assertEqual((item["decision"], item["reviewer_id"]), ("upheld", "reviewer-1"))

    def test_second_resolution_is_409_and_reappeal_is_409(self):
        sub, appeal_id = self.appealed()
        self.assertEqual(resolve(appeal_id).status_code, 200)
        self.assertEqual(resolve(appeal_id, decision="upheld").status_code, 409)
        self.assertEqual(appeal(sub).status_code, 409)

    def test_resolve_errors(self):
        _, appeal_id = self.appealed()
        self.assertEqual(resolve("no-such-appeal").status_code, 404)
        self.assertEqual(resolve(appeal_id, decision="maybe").status_code, 400)
        self.assertEqual(resolve(appeal_id, reviewer_notes="ok").status_code, 400)
        self.assertEqual(resolve(appeal_id, reviewer_id="").status_code, 400)
        self.assertEqual(client.get("/appeals?status=classified").status_code, 400)


if __name__ == "__main__":
    unittest.main()
