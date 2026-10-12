import uuid
from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from audit_log import init_db, log_event, read_log

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

@app.route("/submit", methods=["POST"])
@limiter.limit("5 per minute;50 per day")
def submit():
    data = request.get_json()
    text = data.get("text")
    creator_id = data.get("creator_id")

    # Placeholder response — wire in your detection signal next.
    return jsonify({
        "content_id": str(uuid.uuid4()),
        "attribution": "uncertain",
        "confidence": 0.5,
        "label": "We're not sure who wrote this.",
    })

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
