---
title: Breach Analytics at Scale
emoji: 🔎
colorFrom: gray
colorTo: red
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Exposure analysis over 759 synthetic breach documents
---

# Breach Analytics at Scale

Ingests 759 heterogeneous synthetic breach documents, extracts PII,
resolves them into people, and serves a defensible exposure table where
every claim opens to the page of the document it came from.

**All data here is synthetic.** The names, SSNs, card numbers, addresses
and clinical notes were generated for this project. They are not real
people and not a real breach. They are deliberately realistic, because a
corpus of obviously-fake data would not have exercised the pipeline —
see the design doc's §1.2a on why the first, tidier corpus flattered the
results.

## What is running here

This Space serves a **pre-computed** database. Extraction, entity
resolution and the four agents all ran offline before deployment, and
their output is baked into the image.

Nothing here calls a language model at request time. There is no API key
in this Space and no per-visitor inference cost — the LLM tier was a
batch job whose results are stored, not a live dependency. The same is
true of the agents: you can read every run trace and merge proposal they
produced, but you cannot trigger a new agent run from the web UI.

The documents in the evidence viewer are re-encoded to 150 dpi greyscale
to fit a free host (191 MB → 46 MB). Accuracy figures were measured
against the originals, never these — compressing a scan changes what OCR
reads off it, which is the whole premise of §1.2b.

## Measured results

| Metric | Value |
|---|---:|
| Person recall | 1.000 |
| Person precision | 0.792 (0.808 after approved merges) |
| Merge errors | 0 |
| Value-level precision | 0.975 |
| Page-citation accuracy | 1.000 |
| Third-party contamination | 0 of 24 planted |

Full methodology, the defects these numbers exposed, and the cases where
the *measurement* turned out to be wrong rather than the code, are in
`docs/02_Solution_Design.md`.

## Running it locally

```bash
# backend
cd backend && pip install -r requirements.txt
python run_pipeline.py --reset            # ingest, extract, resolve
uvicorn app.main:app --reload

# frontend
cd frontend && npm install && npm run dev
```

Built for the DataFactZ AI Engineering Internship, Use Case 3.
