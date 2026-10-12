# ai201-project4-provenance-guard

Provenance Guard is a Flask API that estimates whether a piece of text was written by AI. It gives every result a plain-language transparency label, and creators can appeal decisions they disagree with. Every decision and appeal is recorded in an audit log.

Full design notes, signal analysis, and diagrams are in [planning.md](planning.md).

## How a submission flows through the system

1. `POST /submit` receives up to 20,000 characters of text. The **rate limiter** (5/min, 50/day per IP) runs first.
2. The **validator** rejects empty or oversized text and counts words.
3. **Signal 1 (LLM, Groq)** rates how AI-like the text reads and gives reasons.
4. **Signal 2 (stylometric heuristics)** measures sentence-length variation (burstiness), vocabulary variety (MATTR), the em-dash rate, and "it's not X, it's Y" contrast frames, weighted toward the features every text has a value for.
5. The **confidence scorer** combines the two (60% LLM, 40% heuristic) and applies the safety rules: under 50 words, LLM unavailable, or signals disagreeing by more than 0.4 → **Uncertain**.
6. The **label generator** turns the result into a plain-language label: *Likely AI-generated*, *Unclear origin*, or *Likely human-written*.
7. The result is saved to **SQLite** along with an **audit log** entry holding every score and reason, then returned as JSON with a private `appeal_token`.

**Appeals:** the original submitter calls `POST /appeal` with their `content_id`, `creator_id`, `appeal_token` and `creator_reasoning`. Both the `creator_id` and the token must match the submission. This is allowed for *Likely AI-generated* and *Unclear origin* labels. The content's status becomes `under_review`, and the appeal is logged next to the original decision. The original label stays visible, marked as disputed, until a person reviews it from the queue (`GET /appeals`). There is no automatic re-classification.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/submit` | Classify text → label, confidence score, signal breakdown, appeal token |
| POST | `/appeal` | Dispute a decision (submitter only) → status `under_review` |
| GET | `/content/<id>` | Current label and status of a submission |
| GET | `/appeals` | Reviewer queue of pending appeals (`?status=under_review`) |
| GET | `/log` | Read-only audit log entries (optionally `?content_id=`) |

## Confidence scoring

- `confidence = 0.6 × LLM + 0.4 × heuristic` (0 = human, 1 = AI)
- **High AI** ≥ 0.80 · **High Human** ≤ 0.35 · everything else **Uncertain**
- The thresholds are deliberately asymmetric. Falsely labeling a human writer as AI is worse than missing some AI text, so "AI" requires stronger evidence.

## Rate limiting

`POST /submit` is limited to **5 requests per minute and 50 per day per IP**.
- **5/min:** enough for a real writer re-checking a few drafts, but it blocks scripted bulk submissions.
- **50/day:** every submission costs one Groq API call. This cap keeps a single client from exhausting the free-tier quota for everyone else.
- Appeals and GET endpoints are not rate limited. They don't call Groq and are cheap.

## Testing

_TODO after building: the human and AI samples tested, the resulting scores, and any threshold adjustments made._

## Audit log examples

_TODO after building: paste at least 3 real entries (classifications and an appeal) from `GET /log`._

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "GROQ_API_KEY=your_key" > .env
```
