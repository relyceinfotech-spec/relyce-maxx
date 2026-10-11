# Offline citation and evidence efficiency investigation

Investigated `backend/.local-state/live-chat-20261011T103826.json` without provider calls.
**245 local tests passed, zero skipped; Ruff lint and formatting passed.** The disposable
PostgreSQL/pgvector database was discarded. No hosted writes, migrations or publishing ran.

## What the artifact establishes

The deterministic passage and sentence-to-claim checks passed for six claims before the
independent verifier reported `missing_citation` at sentence 0, claim 1, passage S2-P1.
Five claims were supported, one was unsupported, and no contradictions were reported.
Consequently a missing rendered citation is not established by this diagnostic. An incorrect
proposition mapping or semantic support failure remains possible, but the rejected answer
and claim texts were not retained. Their exact failure cannot be reproduced or attributed.

The YouTube extraction demonstrably contains only navigation/legal footer labels. It
provided no selected evidence passage and should not have been considered a readable page.
The bounded public footer and recorded usage are preserved in
`backend/tests/fixtures/chat_citations_20261011.json`; no historical draft was invented.

## Changes and offline evidence

- `research/coverage.py` inserts source markers from validated sentence-to-claim mappings
  after exact claim/passage validation and before independent verification. It still rejects
  unknown source identifiers and model-created links. Missing/invalid mappings still fail.
  The server controls markers and final links; the verifier still judges factual support,
  temporal relevance and full-answer coverage.
- `research/content.py` conservatively detects extraction made entirely of known navigation
  or copyright labels. `tools/reader.py` rejects it before caching; Chat evidence selection
  also filters previously cached footer-only material. Substantive articles containing footer
  labels remain eligible. This is a targeted filter, not a universal content-quality classifier.
- `research/evidence.py` removes duplicate selected passages within a source and merges
  overlapping verifier context windows. Original text, source identity, dates and neighboring
  qualifications remain intact. Tests establish smaller serialized payloads on fixtures;
  reduced live token usage or billed cost has not yet been measured.
- `observability/usage.py` and the live harness now report provider input/output tokens and
  model cost separately from conservative reservations. Missing costs remain unknown, and
  search billing is explicitly excluded from model totals.

Fourteen additional offline cases cover deterministic marker insertion, idempotence,
unknown links, independent rejection of unsupported claims, the captured footer, substantive
content preservation, duplicate offsets, context qualifications and usage accounting.
Fake verifier decisions test enforcement; they do not establish real-model factual accuracy.
Existing Post behavior, models, limits and independent verification remain unchanged.

## Recorded usage, not new spending

| Metric from the saved run | Value |
| --- | ---: |
| Provider-reported input tokens | 3,526 |
| Provider-reported output tokens | 1,352 |
| Provider-reported model cost | $0.0011166 |
| Conservative reserved tokens | 20,838 |
| Conservative reserved model budget | $1.0419 |
| Separate Serper usage | 1 search credit |

Reservations are capacity accounting, not billed usage. Output counts are provider-reported
completion tokens and may include reasoning. No new paid test ran. Live grounded-answer
acceptance remains pending; any next run requires explicit authorization.
