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
- `confidence` is an AI-likelihood score, not a probability. 0.62 does not mean "62% chance of AI", which is why the label a reader sees uses words, not the number.

## Transparency labels

Every result gets one of three labels (`labels.py`), written for a non-technical reader. Each says what was found, that it's an estimate, and what to do next:

| Attribution | Label text |
|---|---|
| `high_ai` | Likely AI-generated. Our automated checks found strong signs that this text was written with an AI tool. This is an estimate, not proof. If you wrote this yourself, you can appeal and a person will review it. |
| `uncertain` | Unclear origin. Our automated checks found mixed or weak signals, so we're not calling this AI-written or human-written. *{reason}* If you wrote this and want a person to review it, you can appeal. |
| `high_human` | Likely human-written. Our automated checks found few signs of AI writing. This is an estimate, not a guarantee. |

The Uncertain *{reason}* explains which rule applied: too short, a check was unavailable, the two checks disagreed, or the signals lean human / are evenly mixed / lean AI.

Every response also has a **`why`** line built from this result's actual evidence, e.g. *"Writing style: very evenly sized sentences and polished, highly varied word choice. The language model noted: highly polished, uniform marketing language."*

While an appeal is pending, `GET /content/<id>` adds: *"The creator has disputed this label. It is waiting for review by a person."*

## Appeals

```json
POST /appeal
{
  "content_id": "…", "creator_id": "…", "appeal_token": "… (from the /submit response)",
  "creator_reasoning": "I wrote this myself; my drafts are in Google Docs history.",
  "evidence": "optional: links or a description of the writing process"
}
```

- **Who:** only the original submitter. `/submit` returns a one-time `appeal_token`, and only its SHA-256 hash is stored. The appeal must match both the `creator_id` and the token. A mismatch on either returns the same `403`, so neither can be guessed separately. This is checked before anything else about the content is revealed.
- **What can be appealed:** `high_ai` and `uncertain` results. Appealing `high_human` returns `400`. Each piece of content can be appealed once (`409` after that).
- **What happens:** in one database transaction, the appeal is stored, the content's status changes from `classified` to `under_review`, and an `appeal` entry is appended to the audit log with the creator's reasoning and the original decision. **The original label is not changed or hidden**, and nothing is automatically re-classified.
- **Reviewer queue:** `GET /appeals` lists pending appeals oldest first. Each item has the full submitted text, the original label and score, both signals' full output, and the creator's reasoning and evidence: everything a person needs to decide without digging through the log.

## Rate limiting

`POST /submit` is limited to **5 requests per minute and 50 per day per IP**.
- **5/min:** enough for a real writer re-checking a few drafts, but it blocks scripted bulk submissions.
- **50/day:** every submission costs one Groq API call. This cap keeps a single client from exhausting the free-tier quota for everyone else.
- The limit is checked **before** validation, so malformed requests count too. That's deliberate: a script spamming bad requests is throttled just the same.
- Over the limit, the API returns `429` with a JSON explanation: *"Rate limit exceeded (5 per 1 minute). Each submission runs an AI model, so /submit allows 5 requests per minute and 50 per day per IP. Try again later."*
- Appeals and GET endpoints are not rate limited. They don't call Groq and are cheap.

## Testing

**Automated tests:** `python -m unittest discover tests` runs 28 tests, with no Groq calls and no changes to the real database:
- **13 scoring tests:** every rule and threshold boundary (49 vs 50 words, a signal gap of exactly 0.40 vs 0.41, confidence of exactly 0.80 / 0.79 / 0.35 / 0.36).
- **7 label tests:** all three variants and every Uncertain reason. The label text was also checked character-for-character against planning.md §3.
- **8 appeal tests** (temp database, stubbed LLM):
  - a successful appeal sets `under_review`, keeps the original label, and appends an `appeal` log entry while the original `classification` entry stays unchanged
  - a second appeal gets 409
  - a wrong token and a wrong `creator_id` get identical 403s
  - `high_human` can't be appealed, while `uncertain` can
  - an unknown id gets 404
  - input validation works
  - the token never appears in any GET response

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

Every classification and every appeal appends one entry to the `audit_log` table in SQLite. Entries are never updated or deleted, so the log is a complete history. A submission's current status lives in a separate `content` table. Read the log with `GET /log` (newest first; `?content_id=` filters to one submission, `?limit=` up to 200).

Below are four real entries from a live run (`GET /log`, shown oldest first). Each classification's `signals` field holds both signals' full output. The heuristic's raw feature values are trimmed here for length.

**1. Classification: AI product copy → `high_ai`**
```json
{
  "content_id": "29e18d15-fe75-40c9-8a1a-014dc266e8b1",
  "creator_id": "demo-writer-ai",
  "timestamp": "2026-10-12T14:21:17.038544+00:00",
  "event_type": "classification",
  "attribution": "high_ai",
  "confidence": 0.86,
  "llm_score": 0.92,
  "heuristic_score": 0.759,
  "decided_by": "threshold_high_ai",
  "scoring_version": "v1",
  "status": "classified",
  "signals": {
    "llm": {
      "score": 0.92,
      "reasons": ["Highly polished, uniform marketing language",
                  "Formulaic structure with generic feature list",
                  "No personal anecdotes or specific irregularities"]
    },
    "heuristic": {
      "score": 0.759,
      "sub_scores": {"em_dash": 0.518, "contrast_frames": 0.0, "burstiness": 0.821, "vocabulary": 0.964}
    }
  }
}
```

**2. Classification: Mark Twain essay paragraph → `high_human`**
```json
{
  "content_id": "6665a091-a143-4fdc-a827-bb40e5171597",
  "creator_id": "demo-writer-human",
  "timestamp": "2026-10-12T14:21:17.611705+00:00",
  "event_type": "classification",
  "attribution": "high_human",
  "confidence": 0.09,
  "llm_score": 0.15,
  "heuristic_score": 0.0,
  "decided_by": "threshold_high_human",
  "scoring_version": "v1",
  "status": "classified",
  "signals": {
    "llm": {
      "score": 0.15,
      "reasons": ["irregular rhythm and quirky narrative style",
                  "specific odd details and idiosyncratic voice",
                  "inconsistent logic typical of human creative writing"]
    },
    "heuristic": {
      "score": 0.0,
      "sub_scores": {"em_dash": 0.0, "contrast_frames": 0.0, "burstiness": 0.0, "vocabulary": 0.0}
    }
  }
}
```

**3. Classification: short text → `uncertain` (`too_short`)**
```json
{
  "content_id": "d0086b0e-5cd6-4c6e-ab94-946950ce8591",
  "creator_id": "demo-writer-short",
  "timestamp": "2026-10-12T14:21:18.511163+00:00",
  "event_type": "classification",
  "attribution": "uncertain",
  "confidence": 0.54,
  "llm_score": 0.45,
  "heuristic_score": 0.665,
  "decided_by": "too_short",
  "scoring_version": "v1",
  "status": "classified",
  "signals": {
    "llm": {
      "score": 0.45,
      "reasons": ["Uses personal pronoun but remains generic",
                  "Discusses common remote‑work tradeoffs without concrete data",
                  "Tone is polished yet lacks specific anecdotes"]
    },
    "heuristic": {
      "score": 0.665,
      "sub_scores": {"em_dash": 1.0, "contrast_frames": 0.0, "burstiness": 0.623, "vocabulary": 0.812}
    }
  }
}
```

**4. Appeal of entry 1**
```json
{
  "content_id": "29e18d15-fe75-40c9-8a1a-014dc266e8b1",
  "creator_id": "demo-writer-ai",
  "timestamp": "2026-10-12T14:21:18.515826+00:00",
  "event_type": "appeal",
  "attribution": "high_ai",
  "confidence": 0.86,
  "decided_by": "threshold_high_ai",
  "scoring_version": "v1",
  "status": "under_review",
  "appeal_id": "dd2fce37-962d-4969-beab-1adfa04df2ec",
  "creator_reasoning": "I wrote this product copy myself for my shop; earlier drafts are in my Google Docs history."
}
```

**How to read these:**
- Entry 4 shares entry 1's `content_id`, so the appeal sits alongside the decision it disputes, and it repeats that decision unchanged (`high_ai`, 0.86, `threshold_high_ai`).
- Entry 1 still says `"status": "classified"`. The log records what was true at each moment, while `GET /content/<id>` shows the current status (`under_review`).
- `decided_by` names the exact rule behind each label, and `scoring_version` records which weights and thresholds were in force, so old entries stay explainable after future tuning.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "GROQ_API_KEY=your_key" > .env
python app.py
```

Then, in another terminal:

```bash
curl -s -X POST http://127.0.0.1:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"text": "Your text here…", "creator_id": "your-name"}' | python -m json.tool
```

**macOS note:** use `127.0.0.1`, not `localhost`. On macOS, the AirPlay Receiver also listens on port 5000 and answers `localhost` requests with an empty `403`. Alternatively, turn it off in System Settings → General → AirDrop & Handoff.
