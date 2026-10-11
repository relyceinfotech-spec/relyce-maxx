# Chat webpage reading and evidence contracts

Implemented locally on Python 3.13.7. The full disposable PostgreSQL/pgvector suite passes
**216 tests, zero skipped**; Ruff lint and formatting pass. This is simulated-provider and
local database acceptance. No paid provider calls, hosted writes or migrations ran for this
implementation. Real factual quality and model compatibility with the expanded schemas remain
live acceptance gates. Existing models, output ceilings and spending limits are unchanged.

## Failure investigation

The first web run (`live-chat-20261011T094909.json`) discarded its rejected draft. Its exact
failed claim/check cannot be reconstructed. The later authorized retest
(`live-chat-20261011T100159.json`) read four pages and passed strict quote/passage checks for
three claims; independent verification returned `[false, true, true]` and three contradictions.
The answer was withheld. That artifact does not retain the unsupported claim's text or raw
contradiction reasons, so no single semantic root cause is established.

Code inspection previously established a separate date-propagation gap: document publication
dates were absent from the independent verifier payload. They are now included. Historical
release-note filtering and explicit unknown dates prevent fresh crawl labels from becoming
publication dates. None of these changes proves that the live rejection is resolved.

## Tool ownership and navigation

`backend/app/tools/reader.py` owns the job-scoped `PageReader`:

- `web_search(query)`: existing Serper, normalized candidate URL/title/snippet/search date.
  Candidate snippets never enter the extracted evidence registry.
- `open_page(url_or_source_id)`: existing secure Fetcher and bounded HTML/PDF extraction.
  Known source IDs, final URLs and captured redirect aliases reuse the job cache.
- `find_in_page(source_id, query)`: lexical ranking of captured passages, without network calls.
- `read_more(source_id, passage_id)`: up to three neighboring captured passages; no caller offsets.

Passages contain stable source/passage IDs, exact text and character offsets into the bounded
6,000-character extraction. Each passage is at most 700 characters. Unicode offsets address
Python strings, not bytes. `Page` rejects wrong pairings, duplicate passage IDs and forged
offsets. A source's `published_at` comes from document metadata; `search_date` remains separate.
`date_confidence=document` means document-provided metadata, not independently confirmed truth.

The reader inherits the existing public DNS/IP pinning, per-redirect validation, robots rules,
timeouts, size limits and content-type restrictions. It never executes page scripts or bypasses
access restrictions. Bounded extracted text is retained in existing private job checkpoints and
the existing owner-scoped evidence store; this change adds no full-page archival service.

## Orchestration, recovery and limits

Chat uses prepare → discover → read → assess → compose → verify. Each search and page operation
is a separate graph node. The source registry, aliases, candidate queue, attempted URLs,
completed queries, selected passages, plan, verification outcome and budget survive checkpoints.
Completed checkpointed operations are reused after restart. A crash after a provider responds
but before its checkpoint commits can still replay that in-flight operation; exactly-once external
execution is not claimed. Explicit failed-job retries keep the existing new-generation semantics.

Candidate ranking uses topic overlap, primary Python documentation where relevant, extractability,
deduplication and uncertain date hints. Sources remain discoverable without a domain whitelist.
Inaccessible/old pages yield to other candidates. Weak evidence can trigger a bounded query
refinement. Synthesis uses `find_in_page` and `read_more` with the same passage IDs.

New configurable collection limits (also in `backend/.env.example`):

| Mode | Captured page cap | Search iterations | Page/search attempts |
| --- | ---: | ---: | --- |
| Web | 4 | 2 | Existing MAX_PAGES / MAX_SEARCHES |
| Direct URL | 2 | 0 | At most two supplied URLs |
| Deep | 8 | 2 | Existing DEEP_MAX_PAGES / DEEP_MAX_SEARCHES |

Captured pages include extracted documents excluded for freshness; failed extraction attempts
consume the existing attempt budget. The absolute job deadline survives recovery. Existing
model-call/token/cost reservations, cancellation fences and provider error handling still apply.
Deep research gets bounded additional collection when verification fails and collection capacity
remains; exhausting collection capacity does not justify repeated synthesis of unchanged evidence.

## Verification and response

Structural checks reject unknown/duplicate IDs, wrong source/passages, quotes absent from exact
captured passages, mismatched canonical URLs and forged offsets. Chat quote matching normalizes
whitespace only, retaining letter case. The legacy non-passage workflow keeps its prior matching.

Independent Chat verification reviews negation, values, dates, scope, attribution, qualifiers and
contradictions. `ChatVerification` additionally requires `answer_coverage_supported` and
`coverage_failures`: supported submitted claims alone cannot accept uncited extra answer facts.
These are model judgments, not deterministic proof of factual truth. Local doubles test their
enforcement, not the judgment's live accuracy.

Accepted `ChatReport` responses add `citations`, each containing `source_id`, `passage_id`,
server-resolved `url`/`title` and `claim_indices`. Model-provided citation metadata is discarded.
All claimed source IDs must appear in the answer. The server renders existing Markdown citations
and creates structured targets only after passage, support, coverage and citation checks pass.
Failure clears claims/citations and returns an explicit limitation with `verified=false`.

The live harness's development-only diagnostics retain bounded IDs, claim indexes, stage/reason,
quote lengths/hashes, lookup/matching results and coverage/support counts, never rejected quotes,
private prompts, credentials or raw verifier prose. Ordinary workers have no diagnostic callback.
Evidence audit v2 independently checks captured passage offsets and structured citation mappings;
it continues to leave factual acceptance pending independent review.

## Files changed

- Runtime: `agents/chat/graph.py`, `core/config.py`, `domain/models.py`, `tools/reader.py`,
  `tools/serper.py`, `research/evidence.py`, `research/selection.py`, `research/verification.py`,
  `research/audit.py`, `workers/runner.py` under `backend/app/`.
- Configuration/scripts: `backend/.env.example`, `backend/scripts/live_chat_smoke.py`,
  `backend/scripts/audit_chat_evidence.py`.
- Tests: `test_web_reader.py`, `test_research_graph.py`, `test_recovery_and_scheduling.py`,
  `test_freshness.py`, `test_chat_context.py`, `test_chat_reliability.py`,
  `test_local_workflows.py` under `backend/tests/`.
- Documentation: this file, `PROJECT_MAP.md`, `LIVE_CHAT.md`, `EVIDENCE_AUDIT.md`,
  `VALIDATION.md`, `LOCAL_TESTING.md` under `docs/`.

## Optional OpenRouter search investigation

[OpenRouter's current server-tool documentation](https://openrouter.ai/docs/guides/features/server-tools/web-search)
documents beta `openrouter:web_search`, per-endpoint `native_tools` capability checks and
server-tool call caps. Native support varies; some engines ignore `max_uses`. Exact configured
endpoint behavior was not established with a live inference call. No optional adapter/flag was
added on unverified support. Serper remains the discovery engine; hosted annotations would still
need independent MAXXX page extraction and passage verification.

## Next authorized live step

After explicit approval for one bounded run:

```powershell
cd "D:\reserch maxxx"
node tools/local-db/test.mjs --live --case auto_web
```

The existing harness caps the case at two Serper searches, four page attempts, six model calls
and $1.50 conservative model-cost reservation. This deterministic fresh-question path normally
uses one synthesis and one independent verification call. Actual costs are recorded separately.
Stop and inspect the artifact on failure. Do not launch the full suite or retry automatically.
Normal and standard Chat previously passed; URL/follow-up/deep live acceptance remains pending.

The user subsequently authorized exactly one auto_web case. Artifact
`live-chat-20261011T102616.json` records three readable sources from four attempts, five
structurally valid and model-supported claims, but two contradictions and two answer-coverage
failures. The result was withheld. Actual model cost was $0.00099835 plus one Serper credit.
Both model calls stopped normally. No retry/full suite ran; any further paid run needs fresh
authorization. Exact rejected-answer coverage details were not retained in sanitized diagnostics.
