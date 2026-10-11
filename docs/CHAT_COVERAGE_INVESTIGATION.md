# Chat answer coverage investigation

Offline follow-up to `backend/.local-state/live-chat-20261011T102616.json`.
**231 local tests pass, zero skipped; Ruff lint and formatting pass.** No paid calls,
new model choices, spending changes, hosted writes, migrations or publishing occurred.

## Demonstrated findings

- The artifact retains the withheld report, source corpus, selected passages and verdict counts.
  It does not retain the original final-answer draft, its five claim texts or raw verifier issues.
  Therefore comparing the historical factual sentences with their claim mappings is impossible.
- The prior Chat verifier prompt explicitly instructed missing coverage to be listed as
  contradictions. This conflated separate checks. Its document/time instruction also had a
  malformed sentence ordering. Both prompt defects are corrected; neither proves the model's
  two historical contradictions were false.
- The original structured report had no explicit answer-sentence-to-claim mapping. A complete
  claim list and supported=true flags alone could not establish complete answer coverage.
- Captured S1-P1 describes JIT improvements; S1-P29 qualifies the conditions for fully supported
  status. These propositions can coexist. A captured model-written limitation also calls this
  a qualification. That limitation is not an independent adjudication of the verifier.
- S1/S2 document dates were 2026-10-09; S3 had an unknown publication date. Publication timing
  alone does not establish a feature's introduction/event date. Exact historical temporal
  mistakes remain unknown because the rejected sentences/reasons were discarded.

## Changes

`backend/app/research/coverage.py` owns deterministic sentence indexing, mapping checks,
indexed verifier input and reference validation. Indexes preserve version numbers, attach
source citations to their sentences and stay contiguous across blank lines.

New Chat drafts include `sentence_claims`: sentence index plus claim indexes. Every factual
sentence needs a mapping and exactly matching source citations. Unknown/duplicate/missing
mappings fail before another model call. New synthesis requests one factual sentence per line,
every proposition in mapped claims, timing precision and scope disclaimers in limitations.
Older checkpoints retain mandatory independent full-answer verification; they do not gain an
automatic verification exemption because the new mapping field was absent.

`ChatVerification.issues` separates contradiction, qualification, missing claim/citation,
temporal mismatch, incomplete coverage and unsupported scope. Server validation rejects
unknown sentence/claim/passage references. Any blocking issue fails acceptance even if the
model's claim booleans and coverage flag are true. A qualification alone is informational only
when all passage, claim support, full-answer coverage and citation checks pass. Any legacy
contradiction string still blocks; none is discarded or reinterpreted to force acceptance.

The verifier receives indexed draft sentences, mappings and limitations. Development callbacks
record only bounded issue categories, sentence/claim indexes and known passage IDs. Mapping
failures identify affected indexes without copying sentence text. Invalid verifier references
are withheld without echoing their strings. Ordinary workers still have no diagnostic callback.

## Offline reproduction and its limits

`backend/tests/fixtures/chat_coverage_20261011.json` captures three bounded public passages,
source dates and historical verdict counts. It contains no private prompt or reconstructed
historical draft. Tests create explicitly synthetic candidates from these passages.

Regression cases demonstrate missing sentence mappings/citations are withheld, typed temporal
or genuine contradiction issues cannot pass behind true booleans, and an independently checked
limited JIT answer can pass when the qualification is compatible and every included sentence
is covered. The same limited candidate is withheld if a contradiction remains. Fake verifier
judgments test controller enforcement; they do not reproduce GPT-6 Luna's historical reasoning
or prove its current classification quality.

A partial answer is a separately checked candidate, not a subset silently released from a
failed report. This change adds no automatic repair call, no automatic paid retry and no
fallback that ignores failed coverage. Live behavior of the expanded schema remains unverified.

## Changed files

- `backend/app/domain/models.py`: sentence mapping and indexed issue contracts.
- `backend/app/research/coverage.py`: focused coverage/reference validation.
- `backend/app/agents/chat/graph.py`: synthesis instructions, indexed verifier input and gates.
- `backend/tests/test_answer_coverage.py` and its captured fixture: 15 new regression cases.
- Existing doubles updated in `test_research_graph.py`, `test_chat_context.py`,
  `test_local_workflows.py`, `test_web_reader.py` under `backend/tests/`.
- Documentation: this report, `PROJECT_MAP.md`, `VALIDATION.md`, `LOCAL_TESTING.md`, `LIVE_CHAT.md`.

The specific historical missing facts and whether the verifier falsely labeled a qualification
remain suspected, not demonstrated. A future explicitly authorized run can retain safe indexed
issue diagnostics; no additional live test is authorized by this offline task.
