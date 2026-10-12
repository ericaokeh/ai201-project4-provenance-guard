import uuid
from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from audit_log import init_db, log_event, read_log
from detection.heuristic_signal import get_heuristic_signal
from detection.llm_signal import get_llm_signal
from scoring import score

app = Flask(__name__)
init_db()

limiter = Limiter(
get_remote_address,
app=app,
default_limits=[],
storage_uri="memory://",
)

@app.route("/")
def home():
    return "Provenance Guard is running."

MAX_TEXT_CHARS = 20_000
MAX_CREATOR_ID_CHARS = 100

# Placeholder labels until M5 adds the full transparency label text.
PLACEHOLDER_LABELS = {
    "high_ai": "Likely AI-generated.",
    "uncertain": "Unclear origin.",
    "high_human": "Likely human-written.",
}

@app.route("/submit", methods=["POST"])
@limiter.limit("5 per minute;50 per day")
def submit():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    text = data.get("text")
    creator_id = data.get("creator_id")
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "'text' is required and cannot be empty."}), 400
    if len(text) > MAX_TEXT_CHARS:
        return jsonify({"error": f"'text' must be at most {MAX_TEXT_CHARS:,} characters."}), 400
    if not isinstance(creator_id, str) or not creator_id.strip():
        return jsonify({"error": "'creator_id' is required and cannot be empty."}), 400
    if len(creator_id) > MAX_CREATOR_ID_CHARS:
        return jsonify({"error": f"'creator_id' must be at most {MAX_CREATOR_ID_CHARS} characters."}), 400

    llm = get_llm_signal(text)
    heuristic = get_heuristic_signal(text)
    result = score(llm, heuristic, heuristic["features"]["word_count"])

    content_id = str(uuid.uuid4())
    log_event({
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": result["attribution"],
        "confidence": result["confidence"],
        "llm_score": llm["score"],
        "heuristic_score": heuristic["score"],
        "decided_by": result["decided_by"],
        "scoring_version": result["scoring_version"],
        "status": "classified",
    })

    return jsonify({
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": result["attribution"],
        "confidence": result["confidence"],
        "label": PLACEHOLDER_LABELS[result["attribution"]],
        "decided_by": result["decided_by"],
        "lean": result["lean"],
        "scoring_version": result["scoring_version"],
        "status": "classified",
        "word_count": heuristic["features"]["word_count"],
        "signals": {"llm": llm, "heuristic": heuristic},
    }), 201

@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json()
    content_id = data.get("content_id")
    reasoning = data.get("creator_reasoning")

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "message": "Your appeal was received and is under review.",
    })

@app.route("/log", methods=["GET"])
def view_log():
    return jsonify({"entries": read_log()})



if __name__ == "__main__":
    app.run(port=5000, debug=True)
