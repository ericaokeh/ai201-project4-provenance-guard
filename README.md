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

**Unit tests:** `python -m unittest discover tests` runs 13 tests covering every scoring rule and threshold boundary (49 vs 50 words, a signal gap of exactly 0.40 vs 0.41, confidence of exactly 0.80 / 0.79 / 0.35 / 0.36).

### Course test inputs (scoring v1)

| Input | Words | LLM | Heuristic | Confidence | Attribution | `decided_by` |
|---|---|---|---|---|---|---|
| Clear human ("ok so i finally tried that new ramen place…") | 55 | 0.15 | 0.20 | 0.17 | high_human | threshold_high_human |
| Lightly edited AI ("I've been thinking a lot about remote work…") | 39 | 0.45 | 0.67 | 0.54 | uncertain | too_short |
| Clear AI ("Artificial intelligence represents a transformative paradigm shift…") | 43 | 0.86 | 0.53 | 0.73 | uncertain | too_short |
| Formal human ("The relationship between monetary policy…") | 43 | **0.88** | 0.40 | 0.69 | uncertain | too_short |
| Long AI (LinkedIn-style post, 182 words) | 182 | 0.86 | 0.77 | 0.82 | high_ai | threshold_high_ai |

**What these show:**
- **Formal human writing fooled the LLM (0.88).** That's the academic-writing bias predicted in planning.md §5. The heuristic disagreed (0.40), and the gap of 0.48 would trigger the `signals_disagree` rule even without the length rule. The safeguard caught a real false positive.
- **The heuristic is weak on short text.** On the 43-word clear-AI paragraph (3 sentences) it scored only 0.53, and on the 2-sentence formal paragraph it couldn't measure sentence-length variation at all.
- **The 50-word minimum was kept.** Without it, all three short inputs would still be labeled Uncertain, only with a different reason. The heuristic is genuinely unreliable at 2–3 sentences, and "too short" is an honest explanation.

### Sample set (17 paragraphs, 120–300 words)
10 human paragraphs (*The Great Gatsby*, 1925, and a Mark Twain essay, 1895) and 7 AI paragraphs (generated with `openai/gpt-oss-120b`). Outcome:

| | high_human | uncertain | high_ai |
|---|---|---|---|
| Human (10) | 7 | 3 | **0** |
| AI (7) | 0 | 4 | 3 |

- **No human text was labeled AI**, which meets the false-positive bar.
- 3 of 7 AI texts reached high_ai, short of the "at least half" target. One scored 0.79. **The 0.80 threshold was kept:** 7 samples is too little evidence to move a threshold that protects human writers. It will be revisited when calibrating with modern human writing.

**Adjustments made during testing:** the heuristic's vocabulary-variety mapping was flipped (AI text turned out *more* varied), and em dashes and contrast frames were down-weighted. Before: heuristic means 0.30 human / 0.34 AI. After: 0.29 / 0.68. Details are in planning.md §1.

## Audit log examples

_TODO after building: paste at least 3 real entries (classifications and an appeal) from `GET /log`._

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "GROQ_API_KEY=your_key" > .env
```
