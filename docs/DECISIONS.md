# Experimental decision gateway

`backend/app/llm/decisions.py` calls OpenRouter's dedicated
[`POST /api/alpha/decisions`](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request).
It validates choice membership, complete probability distributions and finite confidence values.
It is a shared workflow component, not another agent or a generative model.

Configuration defaults:

| Purpose | Model |
| --- | --- |
| Experimental primary | `perplexity/pplx-decider-v1.1-27b` |
| Bounded availability fallback | `typesafe/jev-1.13` |
| Comparison / future selective escalation | `openai/gpt-6-luna-decisions` |
| Normal Chat and existing classifier | `anthropic/claude-haiku-5.5` |
| Research and Post generation/verification | `openai/gpt-6-luna` |

All five exact IDs were checked in the appropriate live OpenRouter catalogs on 2026-10-11.
The Models API defaults to text; decision listings require `output_modalities=decisions`.
Listed capability support establishes configuration compatibility, not successful inference.

`DECISION_MODE=off` by default. In `shadow`, Chat evaluates a prediction alongside its
authoritative router and records agreement/confidence, usage, latency and cost. Explicit user
modes and deterministic freshness rules remain authoritative at every confidence level. A failed
prediction never changes the selected route. Discovery also records shadow topic predictions
against its existing classifier; predictions never change clustering or publish authority.

Fallback is bounded to one configured candidate for availability errors. Auth, credits and rate
limit errors do not trigger fallback. Luna escalation is configured for evaluation; it is not
automatically enabled in the production workflow. Shadow has a separate small reservation budget;
autonomous research/draft publishing jobs do not add shadow calls to their publishing budget.

## Evaluation corpus and commands

`backend/evals/` contains 100 balanced Chat route examples, 100 actual source-attributed RSS
headlines collected on 2026-10-11, and 50 difficult route cases covering stale information,
misleading headlines, duplicates, mixed topics, overrides and inaccessible pages. RSS labels are
assistant-reviewed dominant-subject labels, not independently adjudicated ground truth. The
corpus includes older entries and is not a representative production traffic sample. Difficult
cases test routing policy; SQL tests separately test publication eligibility.

From `backend`, these commands audit the data without any provider or database calls:

```powershell
.venv/Scripts/python.exe -m scripts.eval_chat_routes --dataset chat
.venv/Scripts/python.exe -m scripts.eval_chat_routes --dataset discovery
.venv/Scripts/python.exe -m scripts.eval_chat_routes --dataset tricky
```

When credits are available, explicitly add `--live --max-reserved-usd 10`. Each candidate sees
the same case and criteria, with fallback disabled during comparison. The existing route or
discovery classifier supplies the baseline. The runner records accuracy, search mistakes where
applicable, confidence coverage, provider failures, latency, real reported cost and conservative
reservations under ignored `.local-state/`. Missing billing data remains unknown. An incomplete
or failed run must not justify enabling a candidate.

No live decision comparison was run after the OpenRouter HTTP 402 blocker. No candidate is
claimed more accurate, cheaper in the full workflow, or production ready.
