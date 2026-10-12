import hashlib
import hmac
import secrets
import uuid
from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from audit_log import (
    appeal_queue, get_content, init_db, now, read_log, record_appeal, save_submission,
)
from detection.heuristic_signal import get_heuristic_signal
from detection.llm_signal import get_llm_signal
from labels import UNDER_REVIEW_SUFFIX, build_label
from scoring import score

app = Flask(__name__)
init_db()

limiter = Limiter(
get_remote_address,
app=app,
default_limits=[],
storage_uri="memory://",
)

MAX_TEXT_CHARS = 20_000
MAX_CREATOR_ID_CHARS = 100
MIN_REASONING_CHARS = 10
MAX_REASONING_CHARS = 2_000
MAX_EVIDENCE_CHARS = 2_000
APPEALABLE = {"high_ai", "uncertain"}
APPEAL_STATUSES = {"classified", "under_review"}


def error(message, status):
    return jsonify({"error": message}), status


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


@app.errorhandler(429)
def rate_limited(e):
    return error(
        f"Rate limit exceeded ({e.description}). Each submission runs an AI model, "
        "so /submit allows 5 requests per minute and 50 per day per IP. Try again later.",
        429,
    )


@app.route("/")
def home():
    return "Provenance Guard is running."


@app.route("/submit", methods=["POST"])
@limiter.limit("5 per minute;50 per day")
def submit():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Request body must be a JSON object.", 400)

    text = data.get("text")
    creator_id = data.get("creator_id")
    if not isinstance(text, str) or not text.strip():
        return error("'text' is required and cannot be empty.", 400)
    if len(text) > MAX_TEXT_CHARS:
        return error(f"'text' must be at most {MAX_TEXT_CHARS:,} characters.", 400)
    if not isinstance(creator_id, str) or not creator_id.strip():
        return error("'creator_id' is required and cannot be empty.", 400)
    if len(creator_id) > MAX_CREATOR_ID_CHARS:
        return error(f"'creator_id' must be at most {MAX_CREATOR_ID_CHARS} characters.", 400)

    llm = get_llm_signal(text)
    heuristic = get_heuristic_signal(text)
    word_count = heuristic["features"]["word_count"]
    result = score(llm, heuristic, word_count)
    label = build_label(result, llm, heuristic)

    content_id = str(uuid.uuid4())
    # Shown to the creator once; only its hash is stored, so a leaked database can't be used to appeal.
    appeal_token = secrets.token_urlsafe(32)
    signals = {"llm": llm, "heuristic": heuristic}
    created_at = now()

    save_submission(
        content={
            "content_id": content_id,
            "creator_id": creator_id,
            "text": text,
            "word_count": word_count,
            **result,
            **label,
            "signals": signals,
            "status": "classified",
            "appeal_token_hash": hash_token(appeal_token),
            "created_at": created_at,
        },
        audit_entry={
            "content_id": content_id,
            "creator_id": creator_id,
            "event_type": "classification",
            "attribution": result["attribution"],
            "confidence": result["confidence"],
            "llm_score": llm["score"],
            "heuristic_score": heuristic["score"],
            "decided_by": result["decided_by"],
            "scoring_version": result["scoring_version"],
            "status": "classified",
            "signals": signals,
        },
    )

    return jsonify({
        "content_id": content_id,
        "creator_id": creator_id,
        "appeal_token": appeal_token,
        "attribution": result["attribution"],
        "confidence": result["confidence"],
        "label": label["label"],
        "why": label["why"],
        "decided_by": result["decided_by"],
        "lean": result["lean"],
        "scoring_version": result["scoring_version"],
        "status": "classified",
        "word_count": word_count,
        "signals": signals,
        "created_at": created_at,
    }), 201


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Request body must be a JSON object.", 400)

    fields = {name: data.get(name) for name in ("content_id", "creator_id", "appeal_token", "creator_reasoning")}
    for name, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            return error(f"'{name}' is required and cannot be empty.", 400)
    reasoning = fields["creator_reasoning"].strip()
    if not MIN_REASONING_CHARS <= len(reasoning) <= MAX_REASONING_CHARS:
        return error(
            f"'creator_reasoning' must be {MIN_REASONING_CHARS}-{MAX_REASONING_CHARS:,} characters.", 400
        )
    evidence = data.get("evidence")
    if evidence is not None and (not isinstance(evidence, str) or len(evidence) > MAX_EVIDENCE_CHARS):
        return error(f"'evidence' must be text of at most {MAX_EVIDENCE_CHARS:,} characters.", 400)

    content = get_content(fields["content_id"])
    if content is None:
        return error("No submission found with that content_id.", 404)

    # Identity is checked before anything else about the content is revealed. A wrong
    # creator_id and a wrong token get the same message, so neither can be guessed separately.
    same_creator = hmac.compare_digest(content["creator_id"], fields["creator_id"])
    same_token = hmac.compare_digest(content["appeal_token_hash"] or "", hash_token(fields["appeal_token"]))
    if not (same_creator and same_token):
        return error("The creator_id and appeal_token don't match this submission.", 403)

    if content["attribution"] not in APPEALABLE:
        return error("This submission was labeled likely human-written, so there is nothing to appeal.", 400)
    if content["status"] == "under_review":
        return error("This submission already has an appeal under review.", 409)

    appeal_id = str(uuid.uuid4())
    recorded = record_appeal(
        appeal={
            "appeal_id": appeal_id,
            "content_id": content["content_id"],
            "creator_id": content["creator_id"],
            "creator_reasoning": reasoning,
            "evidence": evidence,
            "created_at": now(),
        },
        audit_entry={
            "content_id": content["content_id"],
            "creator_id": content["creator_id"],
            "event_type": "appeal",
            "appeal_id": appeal_id,
            "creator_reasoning": reasoning,
            # The decision being appealed, unchanged: the appeal doesn't re-classify.
            "attribution": content["attribution"],
            "confidence": content["confidence"],
            "decided_by": content["decided_by"],
            "scoring_version": content["scoring_version"],
            "status": "under_review",
        },
    )
    if not recorded:
        return error("This submission already has an appeal under review.", 409)

    return jsonify({
        "appeal_id": appeal_id,
        "content_id": content["content_id"],
        "status": "under_review",
        "original_attribution": content["attribution"],
        "original_confidence": content["confidence"],
        "message": (
            "Your appeal was recorded. The original label stays visible, marked as under review, "
            "until a person reviews it."
        ),
    })


@app.route("/content/<content_id>", methods=["GET"])
def view_content(content_id):
    content = get_content(content_id)
    if content is None:
        return error("No submission found with that content_id.", 404)

    label = content["label"]
    if content["status"] == "under_review":
        label = f"{label} {UNDER_REVIEW_SUFFIX}"
    appeal = content["appeal"]
    return jsonify({
        "content_id": content["content_id"],
        "creator_id": content["creator_id"],
        "status": content["status"],
        "attribution": content["attribution"],
        "confidence": content["confidence"],
        "label": label,
        "why": content["why"],
        "decided_by": content["decided_by"],
        "scoring_version": content["scoring_version"],
        "created_at": content["created_at"],
        "appeal": None if appeal is None else {
            "appeal_id": appeal["appeal_id"],
            "creator_reasoning": appeal["creator_reasoning"],
            "filed_at": appeal["created_at"],
        },
    })


@app.route("/appeals", methods=["GET"])
def view_appeals():
    # Reviewer queue. In a real system this would require reviewer auth.
    status = request.args.get("status", "under_review")
    if status not in APPEAL_STATUSES:
        return error(f"'status' must be one of: {', '.join(sorted(APPEAL_STATUSES))}.", 400)
    return jsonify({"status": status, "appeals": appeal_queue(status)})


@app.route("/log", methods=["GET"])
def view_log():
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 200)
    except ValueError:
        return error("'limit' must be a whole number.", 400)
    return jsonify({"entries": read_log(limit=limit, content_id=request.args.get("content_id"))})


if __name__ == "__main__":
    app.run(port=5000, debug=True)
