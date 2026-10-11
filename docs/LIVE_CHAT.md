# Live Chat smoke tests

Current Chat v4 implementation passes **245 local tests, zero skipped**. Cached navigation,
passage offsets, structured server citations and a separate answer-coverage verdict are ready
for the next explicitly authorized live case. See [implementation details](CHAT_WEB_READING.md).
The single authorized v4 `auto_web` run is recorded below; grounded-answer acceptance remains pending.
The later [coverage investigation](CHAT_COVERAGE_INVESTIGATION.md) is offline only and does not
replace this live failure with a claimed successful answer.

## Latest single authorized test — 2026-10-11 10:57 UTC

Artifact: `backend/.local-state/live-chat-20261011T105731.json`.
Ran exactly one `auto_web` case after explicit authorization, with unchanged limits:
two searches, four page attempts, six model calls, $1.50 per-run conservative reservation
cap and 1,800 output tokens per web call. No automatic retry or further case ran.

One Serper search returned ten candidates and used one credit. Four pages extracted text;
the Mastodon extraction was only a JavaScript-enablement notice, so readable-page count
does not imply four useful evidence sources. Both GPT-6 Luna calls returned HTTP 200 and
`finish_reason=stop`. Server sentence/citation mapping passed for all five claims. Independent
verification judged all five claims supported and reported zero contradictions, but rejected
answer coverage with `temporal_mismatch` at sentence 0, claim 0, passage S2-P1.
The final answer was withheld with no released citations. Acceptance **failed**.

The draft wording was not retained, so the exact unsupported temporal assertion cannot be
reconstructed from the diagnostic alone. S2-P1 says Python 3.15 is officially released;
the document publication date alone does not prove the release occurred in the requested week.
This is a potential explanation, not a demonstrated reconstruction of sentence 0.

Provider-reported input/output: 3,450 / 1,254 tokens (4,704 total, including 631 reported
reasoning tokens). Model cost: $0.0010581; Serper: one separate credit.
Reserved capacity: 20,321 tokens / approximately $1.01605, not billed usage.
Compared with the preceding attempt, input decreased by 76 and output by 98 tokens;
different collected evidence and generated text prevent attributing that reduction to compaction.
No hosted database writes or published/drafted posts occurred; the local database was discarded.
No code, model or budget changes were made during this acceptance run.

## Authorized v4 web run — 2026-10-11 10:26 UTC

### Subsequent single authorized test — 10:38 UTC

Artifact: `backend/.local-state/live-chat-20261011T103826.json`.
Harness limits were checked before launch: two searches, four page attempts, six model calls,
$1.50 conservative reservation cap, 1,800 output tokens per web model call. Publishing,
discovery, rechecks, fallback and experimental routing remained disabled.

One search returned ten candidates (one Serper credit); four attempts yielded three readable
sources. Both GPT-6 Luna calls returned HTTP 200 and finish_reason=stop. Generation used 801
completion tokens ($0.0005753); verification used 551 ($0.0005413). Actual model cost was
**$0.0011166**, distinct from the $1.0419 conservative reservation.

Six claims passed passage/sentence-mapping checks. Independent support was
`[true,false,true,true,true,true]`; coverage was false with one failure. Zero contradictions
were reported. Indexed diagnostics identify category `missing_citation`, answer sentence 0,
claim 1 and passage `S2-P1` (source `S2`). These are verifier judgments, not a deterministic
proof of the precise semantic error. The rejected draft and claim text are not retained;
their exact wording cannot be reconstructed from the indexed record alone.

The result was withheld with verified=false and no returned citations. Exit 1 reflects failed
acceptance, not a worker crash. No retry/full suite ran; hosted writes remained zero and the
local database was discarded. The offline baseline remains 231 passed, zero skipped.

Artifact: `backend/.local-state/live-chat-20261011T102616.json`.
One Serper call returned 10 candidates and reported one credit. Four page attempts produced
three readable sources; one page request failed with HTTPStatusError. `find_in_page` and
`read_more` ran successfully for all three captured pages.

Both GPT-6 Luna calls returned HTTP 200 and `finish_reason=stop`. Generation consumed 720
completion tokens ($0.00053305); independent verification consumed 559 ($0.0004653).
Actual model cost totals **$0.00099835**, distinct from the $0.85005 conservative reservation.

All five submitted claims passed structural passage validation and received supported=true
from the independent verifier. However, the verifier returned two contradictions,
answer_coverage_supported=false and two coverage failures. Sanitized categories include
temporal_relevance and coverage_or_support. Exact missing-fact and contradiction prose was
not retained, so these diagnostics do not establish the specific problematic answer sentences.
The report was withheld: verified=false, zero claims and zero citations returned.

The process reports the job completed because it produced an explicit limitation, but the
smoke command exits 1 because factual acceptance failed. No retries or other live cases ran.
Hosted database writes and published/drafted posts remained zero; the disposable database
was discarded. The offline evidence audit reports no_evidence_claims for the withheld result,
not factual success. Local test status remains 216 passed, zero skipped.

Each smoke case includes a local claim-to-passage [evidence audit](EVIDENCE_AUDIT.md).
Existing artifacts can be audited offline without additional provider calls. Passage matches
and correct citation links are structural checks; independent factual/coverage review remains required.

Use Python 3.13.7 and the existing API/worker interfaces against a fresh local PostgreSQL/pgvector
database. The harness reads only configured model/search fields from the local environment and
substitutes local Auth and database transport. It does not contact Supabase or publish posts.

```powershell
node tools/local-db/test.mjs --live
node tools/local-db/test.mjs --live --case standard
node tools/local-db/test.mjs --live --case auto_web
node tools/local-db/test.mjs --live --suite
```

The first command tests normal chat with a request to explain decorators in approximately 150
words, include one small example and omit unnecessary sections. The suite then covers a standard explanation,
automatic/explicit search, URL reading, a follow-up in the same URL conversation, deep research
about Python 3.15 compatibility with AI frameworks, and simulated inaccessible pages.
Logs record provider calls, sanitized error messages, citations, token usage, reported model costs,
Serper credits and failure categories in `backend/.local-state/live-chat-*.json`. The suite has
a conservative reservation cap and stops on payment/auth/rate-limit errors without retries.
Source relevance and factual quality must still be read independently of structural checks.

## Current result — 2026-10-11

Focused evidence-debugging retest (`live-chat-20261011T100159.json`): automatic Serper
search returned candidates, all four fetch attempts succeeded, and three generated claims
passed strict source/quote/numbered-passage validation. GPT-6 Luna generation and independent
verification both returned HTTP 200 with `finish_reason=stop`. The verifier marked claim index
0 unsupported (`[false, true, true]`) and reported three contradiction/coverage issues. The final
answer was withheld (`verified=false`, zero final citations). Model cost: $0.0007959 total;
Serper reported one credit. No full-suite run or extra paid retest followed.

Captured sources were the official Python release announcement, InfoWorld (document date
2026-10-09), Python Morsels (2026-10-09), and LWN. No Facebook/Reddit fetches occurred.
The exact older quote mismatch cannot be replayed: its original draft was not retained.
The new retest identifies semantic/coverage validation as the remaining failing stage, not
a deterministic quote mismatch. Its local diagnostic retains booleans/counts; it does not retain
raw contradiction prose, so the three exact reasons cannot be reconstructed from this artifact.

Post-retest code correction (offline only): publication dates recovered during extraction now
reach the entailment verifier. Previously its payload omitted them. Future local diagnostics
map unsupported claim indexes to source/passage IDs and record bounded reason categories.
Neither correction was live-retested; acceptance remains pending. Models, web token ceiling,
verification standards, migrations, publication and decision settings were not changed.

Latest authorized live sequence:

| Case | Outcome | Model usage |
| --- | --- | --- |
| Standard (`live-chat-20261011T094841.json`) | Complete-response checks passed; no Serper calls | Router 64 tokens; answer 1,138/1,200, `stop`; $0.0006702 total |
| Automatic web (`live-chat-20261011T094909.json`) | Grounded-answer acceptance failed; answer withheld | GPT-6 Luna 748/1,800, `stop`; $0.0005363; Serper reported 1 credit |

The web router selected search automatically. Serper returned ten candidates; four page
attempts yielded two extracted sources and two `FetchRejected` failures. The page limit
then stopped additional reads. Four generated claims reached deterministic source/quote
validation, which rejected the report before the independent entailment-model call.
Only the generation call appears in model usage; the final report has `verified=false`,
zero claims and zero citations. The rejected claim payload was cleared and not retained in
this artifact, so the precise offending claim/source ID or quote cannot be identified afterward.

Retrieval quality also needs attention: the first captured page discusses Python 3.12's 2023
release despite Serper's “19 hours ago” label; the second is a Python 3.15 feature article
with only relative search publication metadata. These are observations from captured source
text, not proof that freshness caused the deterministic rejection.

Neither live case encountered 402 or truncation. The full suite was not run because the web
case failed acceptance. There were zero published/drafted posts and zero hosted database
writes; publishing, discovery, rechecks and decision routing stayed disabled. No code changes
or additional paid retries were made in this validation turn.

Chat output ceilings are independently configurable in `backend/.env`:

| Setting | Default ceiling |
| --- | ---: |
| `CHAT_ROUTER_OUTPUT_TOKENS` | 768 |
| `CHAT_SIMPLE_OUTPUT_TOKENS` | 768 |
| `CHAT_NORMAL_OUTPUT_TOKENS` | 1200 |
| `CHAT_CODING_OUTPUT_TOKENS` | 2000 |
| `CHAT_WEB_OUTPUT_TOKENS` | 1800 |
| `CHAT_DEEP_OUTPUT_TOKENS` | 6000 |

The gateway uses the same selected ceiling in both the provider request and cost/token
reservation. Deep selection follows the routed workflow, including recovered checkpoints.
`MAX_OUTPUT_TOKENS` remains the existing non-Chat ceiling. These are maximum allowances,
not expected usage or a guarantee that every answer fits. Billing errors never trigger automatic
ceiling reductions or fallback; empty and truncated answers remain withheld.

Answer complexity is independent of search mode. Explicit short/brief requests use the simple
profile; implementation/debugging requests use coding; ordinary explanations use the standard
profile. The normal-answer branch receives a separate desired-length instruction and uses plain
text, not a verbose structured answer schema. Routing rules and model routing are unchanged.
Owner-scoped `model_output` research events retain completion tokens, output ceiling, utilization
and finish reason, without retaining raw output in tracing metadata.

The concise live case passed (`live-chat-20261011T092418.json`). Routing used 53 tokens and
the answer used 323 of 768 tokens (42.1% utilization), both finishing with `stop`. Total reported
model cost was $0.0002582. The completed report persisted locally. Manual answer review found
a correct decorator explanation and one complete forwarding-wrapper example, with closed code
blocks and no unnecessary sections. Zero Serper calls and zero hosted database writes occurred.
This supports verbosity contributing to the earlier truncation; it is one representative example,
not a broad quality benchmark.

The standard case (`live-chat-20261011T092454.json`) routed successfully, then OpenRouter
returned 402 for the answer: 1,200 tokens allowed, but available credit covered only 841 for that
request. No answer was fabricated and no billing retry ran. Live web search was deferred to avoid
spending Serper credits while generation remains credit-blocked. Add sufficient OpenRouter
credit before standard/web/deep acceptance runs. Post development remains frozen.
All 185 local tests passed with zero skipped, including concise follow-up context, separate
complexity/search decisions, task ceilings, tracing metadata and truncation/billing safeguards.
Ruff checks passed.

Local reliability changes since that run reject unclosed code fences and obvious dangling
sentence/citation endings even with `finish_reason=stop`. These structural checks cannot
prove semantic completeness or factual correctness. Schema, exact-quote and citation gates
remain independent. Conflicting evidence is labeled and withheld; snippets alone never qualify.

Recent history is bounded by `CHAT_HISTORY_MAX_BYTES` (default 8,000 UTF-8 bytes), with
at most `CHAT_HISTORY_EXCERPT_BYTES` (2,000) of labeled overflow extracts. Recalled older
messages are bounded separately and owner-scoped. These extracts are not an LLM-generated
summary or complete long-term memory; source records remain intact for targeted retrieval.

`PROVIDER_CREDIT_PREFLIGHT_ENABLED=true` checks `GET /api/v1/key` once before a Chat
job's first Serper call. Exhausted known key/organization budgets, malformed data, authentication
errors and timeouts prevent search without retries. It emits sanitized preflight status, never
financial/account payloads. Positive key limits do not prove account balance or that a specific
model request fits its in-flight budget. Account balance is explicitly unknown when unavailable.
The account-wide credits endpoint requires a management key; none is requested or used.
See [OpenRouter limits](https://openrouter.ai/docs/api_reference/limits) and
[credits endpoint](https://openrouter.ai/docs/api/api-reference/credits/get-remaining-credits).
No new live inference, Serper calls or authenticated credit calls were made for these changes.

The 768-token normal-chat retest (`live-chat-20261011T091521.json`) passed the OpenRouter
credit check. Both Haiku requests returned HTTP 200. Routing used 53 completion tokens and
reported $0.000076; answer generation used 768 completion tokens and reported $0.0003938,
for $0.0004698 total. Neither call reported reasoning tokens. The router selected normal chat
with zero Serper calls, but the answer ended with `finish_reason=length`. The worker failed
with `ModelOutputTruncated`, withholding the incomplete answer. No automatic retries ran.
This earlier run established connectivity and usage capture but failed answer acceptance;
the subsequent concise run above passed.

Post development is frozen until Chat live acceptance passes. The harness explicitly disables
autonomous publishing, discovery, story rechecks and decision models. All database writes are local.

The normal-chat run (`live-chat-20261011T091044.json`) reached OpenRouter using the configured
Claude Haiku 5.5 model and failed with HTTP 402. The sanitized response said the request allowed
2,400 output tokens but available credit could cover only 1,133. This identifies insufficient credit
for the requested token ceiling; it does not establish a model-specific billing defect.
There were zero Serper calls, zero generated answers and zero hosted database writes.
No automatic billing retry or reduction of the output limit was performed. Ensure sufficient
OpenRouter credit before running the full suite; lowering the token ceiling can also cause
truncation, especially for structured research reports. The local simulated-402 workflow verifies
failure without an invented report and requires explicit retry.

After selecting GPT-6 Luna research and Claude Haiku 5.5 chat/classification, model-catalog
preflight passed with strict structured output support. The latest-Python test called real Serper
and extracted two readable sources, then GPT-6 Luna returned **HTTP 402**. No model answer,
verified report or cited answer passed acceptance. The process stopped, the database was discarded,
and hosted database writes remained zero. Artifact: `live-chat-20261011T085305.json`.

Earlier Qwen tests exposed unsupported strict-schema routing (404) and output exhausted entirely
on reasoning. JSON mode support, configurable reasoning effort, explicit truncated/empty-output
errors, date-anchored searches and URL-only fetch behavior now have offline checks. Those earlier
findings do not establish successful inference with the newly selected models. Full live Chat,
deep research, follow-up, and autonomous Post quality remain pending provider credits.
