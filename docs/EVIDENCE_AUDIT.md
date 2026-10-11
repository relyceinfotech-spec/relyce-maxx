# Chat evidence audit

Current passage-aware audit and structured citation contracts are described in
[Chat webpage reading](CHAT_WEB_READING.md). Audit v2 uses captured offsets and server citation
mappings for ChatReport; legacy reports retain their historical audit format. Chat runtime quote
checks normalize whitespace only, while historical audit previews remain casefolded.

The live Chat smoke harness now includes `evidence_audit` in each case. The audit is an
evaluation artifact; production verification and publication permissions are unchanged.
It reads the captured extracted-source corpus and maps every recorded claim to its passage
and rendered source citation. Model-provided source lists and search snippets cannot replace
captured extraction.

Audit existing local runs without loading environment credentials or making network calls:

```powershell
cd "D:\reserch maxxx\backend"
.venv\Scripts\python.exe -m scripts.audit_chat_evidence .local-state\live-chat-20261011T085305.json
```

Output is a sibling `*-evidence-audit.json`, with per-claim text, quote, source URL, bounded
surrounding passage and deterministic checks. Passages are casefolded and whitespace-normalized
to match quote checks. Duplicate source IDs, missing passages, unknown citation IDs and citations
pointing at a different URL fail the structural audit. Invalid schemas are reported explicitly.
The script does not overwrite the original input or re-fetch links.

`structural_checks_passed` and `structural_match_rate` measure passage/citation matches.
They do not measure factual entailment. The audit leaves `entailment`, `answer_claim_coverage`
and `factual_acceptance` pending independent review, regardless of the upstream `verified` flag.
An extracted quote may be unrelated to its associated claim, and an answer may omit an important
claim from the claims array. Every answer paragraph is therefore included for coverage review.

For live evaluation, review each claim against the displayed passage in context, including
negation, attribution, dates and contradictions. Review all answer paragraphs for material claims
missing from the mappings. Check source authority, relevance to the requested period and current
link accessibility before accepting the answer. Existing model-verifier success is recorded as
`upstream_report_verified`; it is not an independent adjudication by this offline audit.

Historical checks performed without external calls:

- `live-chat-20261011T085305`: `no_generated_report`; the credit-blocked web run had no answer to audit.
- `live-chat-20261011T092418`: `no_evidence_claims`; concise stable Chat was not a sourced research answer.

No live web answer has passed this audit yet. Publishing, hosted writes, migrations and
experimental decision routing remain disabled for Chat acceptance.

Chat synthesis now uses `ChatReport`/`PassageClaim`, requiring an existing passage ID as
well as source ID. Exact quotation matching against both selected passage and captured
source precedes independent entailment validation. Production errors remain fail-closed.

The disposable live harness injects a diagnostic callback; ordinary worker contexts do not.
Rejections are saved only under ignored local `.local-state` artifacts, never public timeline
events or hosted tables. Records contain indexes, bounded source/passage IDs, reason codes,
quote lengths/hashes and URL-scheme validity. No rejected quote, private prompt, credentials
or raw verifier contradiction prose is copied into diagnostics. Successful passage checks
and sanitized entailment results identify which stage fails. Future entailment failures map
unsupported claims to their source/passages. Historical retests cannot recover fields that
were not retained at the time; inspect the artifact version before assuming detail exists.
