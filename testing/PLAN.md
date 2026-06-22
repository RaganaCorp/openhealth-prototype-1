# Testing & evaluation — plan

Goal: be able to answer "is the AI actually accurate and safe?" with evidence, not
vibes. Today the app's quality is essentially unmeasured. This plan builds a
defensible evaluation we can show a technical reviewer and use to drive
improvements.

Guiding constraints (same as the product):
- **Runs on-device.** The shipped app never calls an external LLM. The eval harness
  *may* (judge/reference models), but only on de-identified or synthetic data.
- **Correctness first, performance second.**
- **Cheap to maintain.** Prefer deterministic scoring over LLM-judge wherever a
  ground-truth label exists.

---

## Current state (what exists)

- `testing/model_eval_harness.py` — exercises the **real** pipeline code
  (`backend/extraction.py`, `prompts.py`, `documents.py`), scores quality via an
  external judge LLM and speed, writes CSVs to `data-test/`. Good bones.
- It reads inputs from `data-test/` (gitignored) and writes
  `model_eval_matrix.csv`, `model_eval_details.csv`, `model_eval_errors.jsonl`,
  plus `data-test/expected/` gold files and `data-test/outputs/` raw dumps.

### Known gaps (why current results aren't trustworthy)
1. **Committed results are stale** — the scenarios in the old CSVs
   (`summary_generation`, `extraction_pass1`, `json_document_extraction`) no longer
   match the harness's current scenarios (`extract_problems/medications/...`,
   `vision_transcription`, `verification`). Numbers don't reflect today's pipeline.
2. **Gold standard is LLM-generated** by a reference model and judged by another
   LLM. No human-verified ground truth.
3. **No retrieval metrics** (recall@k / MRR) and **no end-to-end QA accuracy** — the
   RAG core is unmeasured.
4. **No safety/hallucination measurement** on the chat path.
5. **Tiny test set** — a handful of files; n far too small to claim anything.
6. **Scoring mixes quality and speed** (60/40), muddying "is it good?".

---

## Privacy: the load-bearing rule

The harness may send eval content to external judge/reference LLMs. Therefore the
data is split across two locations:

| Location            | Committed?       | Tier | External judge ok? |
|---------------------|------------------|------|--------------------|
| `testing/data/`     | **Yes**          | 1 (de-identified / synthetic) | Yes — treat as public |
| `data-test/` (root) | No (gitignored)  | 2 (private real-doc holdout)   | **No** — local/deterministic only |

- **`testing/data/` must never contain real PHI** — de-identify before adding (see
  `testing/data/README.md` for the Safe-Harbor checklist).
- The private real-doc tier in `data-test/` is scored **only** with
  local/deterministic methods and never leaves the machine.

---

## Dataset design (the human work — do this carefully, once)

### Tier 1 — de-identified / synthetic gold set (judge-safe, shareable) → `testing/data/`
- Start from our real documents; strip the 18 HIPAA Safe-Harbor identifiers.
- **Date-shift by a random per-patient offset** so intervals survive (needed for
  trend testing) while real dates are removed.
- Tooling: Microsoft Presidio (runs locally) or a scripted Safe-Harbor pass.
- Optionally use de-identified docs as templates to generate synthetic variants and
  grow the set.

### Tier 2 — private real-doc holdout (never leaves the machine) → `data-test/`
- Real documents, hand-labeled ground truth, scored **deterministically only** (no
  external judge). This is where we measure true performance on messy real formats.

### Coverage matrix (breadth > depth; 1–2 per cell beats 30 of one kind)
- **Format:** CCDA/HTML · native-text PDF · scanned PDF · phone photo (JPEG) · TIFF ·
  JSON portal export.
- **Type:** labs · imaging · discharge summary · medication list · clinical note.
- **Difficulty:** clean · faint/low-contrast scan · handwriting.

### Ground-truth labels (make labeling cheap)
- Per document, a `*.truth.json` mirroring the `DocumentFacts` schema with the
  correct problems / meds / labs / vitals / allergies / procedures + document date.
- **Correct the model's output instead of authoring from scratch** (~5× faster):
  run the current extractor, then fix it by hand.
- **Both founders label, then reconcile.** Report inter-annotator agreement — it's a
  credibility signal in itself.

### Two more small sets beyond extraction truth
- **Retrieval / QA set:** 30–50 natural patient questions, each tagged with the gold
  source document and a short gold answer → enables recall@k + end-to-end accuracy.
- **Safety / adversarial set:** questions whose answers are *not* in the record
  (should decline), dose/interaction framings, and traps designed to elicit
  hallucination. Gold label = `answer` vs `refuse/defer`.

---

## Metrics to report

Offline (no impact on app latency):
- **Extraction:** precision / recall / F1 per category, scored **deterministically**
  against `truth.json` (set overlap for names; numeric/string match for lab values).
  No LLM judge needed.
- **Retrieval:** recall@k, MRR against the QA set's gold source.
- **End-to-end QA:** factual accuracy on the QA set.
- **Safety:** hallucination rate, dangerous-omission rate, refusal correctness on the
  adversarial set.
- **Calibration:** do the answer's "uncertainty notes" correlate with actual errors?
- **Vision:** transcription fidelity (human spot-check; no external judge can see the
  image).

Keep the **LLM-judge for free-text answer quality only**, and only on Tier 1.
Separate quality from speed in reporting (don't fold latency into the headline score).

---

## Harness changes needed
- Re-run and publish a matrix whose scenarios match the current pipeline; delete or
  archive the stale CSVs so code and numbers agree.
- Add a deterministic extraction scorer (`truth.json` ⇄ extractor output).
- Add retrieval + end-to-end QA scenarios driven by the QA set.
- Add the adversarial/safety scenarios.
- Report quality and speed as separate columns.

---

## Phases
- [x] **T1 — Dataset scaffolding:** `testing/data/` committed tier scaffolded
      (README + `truth.template.json`); `.gitignore` anchored so `testing/data/` is
      tracked while root `data/` and `data-test/` stay ignored.
- [ ] **T2 — De-identify + label** a first coverage pass (target ~20–30 docs across
      the matrix). Record inter-annotator agreement.
- [ ] **T3 — Deterministic extraction scorer** + refreshed extraction matrix.
- [ ] **T4 — Retrieval + QA set** and metrics (recall@k, end-to-end accuracy).
- [ ] **T5 — Safety/adversarial set** and metrics (hallucination, refusal, omission).
- [ ] **T6 — Calibration + verifier-independence study** (does a different-family
      verifier catch more than same-family?).

## Open questions
- Verifier independence: is `verification_model` worth making a different family than
  `clinical_model`? (Measure in T6.)
- Embedding model: does a medical-tuned embedder beat `nomic-embed-text` enough to
  justify its size? (Measure once retrieval metrics exist — T4.)
- Chunk size/overlap: tune empirically against recall@k, not by feel.
