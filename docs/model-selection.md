# Model selection (Groq and NVIDIA free tiers)

Checked on 6 Nov 2026 with the project's own keys, using synthetic data only. Re-run `python scripts/model_eval.py` whenever models, limits or test sets change.

## What each provider gives us

| | Groq (free plan) | NVIDIA (free trial, build.nvidia.com) |
|---|---|---|
| Limits | Per model: **1,000 requests a day, 8,000 tokens a minute**, about 30 requests a minute. The tiny models differ (allam-2-7b: 7,000 a day, 6,000 a minute). Sent back in `x-ratelimit-*` headers | Not published. Forum posts say 30 to 40 requests a minute for the whole key. No headers sent, so we pace conservatively at 30 |
| Models that answer for our key | gpt-oss-120b, gpt-oss-20b, qwen3.8-27b (was "over capacity" once), allam-2-7b, gpt-oss-safeguard-20b | About 9 of 84 listed: nemotron-3-ultra-550b, nemotron-3.5-super-vl-120b, nemotron-3-nano-omni-30b, nemotron-3.5-lightning-30b, nemotron-3.5-bolt-e4b, laguna-xs-2.1, step-5-preview, mistral-large-4, muse-glimmer-30b. Many others return 404 "not found for account" or time out |
| Allowed use | Development. Production on the free plan is not realistic because of the limits | **Prototyping and testing only.** Production needs a paid NVIDIA AI Enterprise licence. Prompts may be used by NVIDIA, so synthetic data only |

Consequence: both free tiers are for building and testing. Before any real client data or pilot, choose a paid plan (Groq Developer tier, or another provider) and check its data terms. No route may depend only on the NVIDIA trial (a test enforces this).

## How the gateway stays inside the limits

- It paces each model to **90% of its limit** for requests a minute, tokens a minute and requests a day. Groq limits are per model; NVIDIA models share one bucket.
- It counts input plus the reserved answer length, then replaces the estimate with the real count.
- It trusts Groq's own remaining-token headers, and waits as long as `Retry-After` says after a 429.
- A person at a screen waits at most 45 seconds for room; a background job can pass `max_wait` and wait longer. If the wait would be longer, or the day's quota is gone, it moves to the next model.
- In practice one Groq model handles about 10 small reads a minute, so a batch of documents is spread across gpt-oss-20b and gpt-oss-120b, which have separate budgets.

## Results on our own jobs

Score is the share of checked fields that are exactly right. Time is per call without waiting for limits. The test sets are small (3 quotes, 2 invoices, 7 replies, 6 item mappings, 5 agent turns, 6 blocked invoices), so most good models reach the ceiling. The Phase 1 test sets (30 or more per job) will separate them.

| Model | Quote | Invoice | Reply | Mapping | Agent | Reason | Speed | Notes |
|---|---|---|---|---|---|---|---|---|
| Groq gpt-oss-120b | 0.94 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.4 to 1.2 s | Best all-round |
| Groq gpt-oss-20b | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.3 to 0.8 s | One agent turn of five failed (it tried a native tool call); prompt changed, to be re-checked |
| Groq qwen3.8-27b | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.1 to 4 s | Perfect, but a preview model and was over capacity once |
| NVIDIA laguna-xs-2.1 | 0.97 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.3 to 2.7 s | Fast and accurate; best NVIDIA model |
| NVIDIA nemotron-3-ultra-550b | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 2 to 14 s | Strongest, slow (long thinking) |
| NVIDIA nemotron-3-nano-omni-30b | 1.00 | 1.00 | 1.00 | 1.00 | 0.80 | 1.00 | 1 to 11 s | Slow for its size |
| NVIDIA nemotron-3.5-super-vl-120b | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1 to 26 s | 1 of 3 quotes and 1 of 2 invoices gave no valid answer |
| NVIDIA nemotron-3.5-bolt-e4b | 1.00 | 1.00 | 0.96 | 1.00 | 0.20 | 0.67 | 0.3 to 2.6 s | Reads well but cannot choose tools or reason |
| NVIDIA nemotron-3.5-lightning-30b | 0.00 | 0.00 | mixed | mixed | 0.80 | mixed | 3 to 36 s | Often returns empty text for JSON requests: not usable |
| NVIDIA step-5-preview | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | slow | Empty answers every time: not usable |
| NVIDIA mistral-large-4 | | | | | | | | Did not finish: calls were very slow under test. Retest later |

## What we use for each job

| Job | First | Then | Second opinion (when a check fails) |
|---|---|---|---|
| S01 quote reading, S05 certificates | Groq gpt-oss-20b | gpt-oss-120b, NVIDIA laguna | gpt-oss-120b (medium thinking), then nemotron-3-ultra |
| S02 invoice reading | Groq gpt-oss-120b | gpt-oss-20b, NVIDIA laguna | same |
| S03 reply classification, S04 item mapping | Groq gpt-oss-20b | NVIDIA laguna, gpt-oss-120b | same |
| AG01, AG02, AG04 agent turns | Groq gpt-oss-120b | NVIDIA laguna, gpt-oss-20b | not used per turn |
| AG03 invoice exceptions | Groq gpt-oss-120b (medium thinking) | NVIDIA nemotron-3-ultra, laguna | not used |

These are the default routes in `backend/p2p/ai_steps/gateway.py`. Override any of them with `MODEL_ROUTES` in `.env`, and the limits with `MODEL_LIMITS`.

## Prices and the monthly budget (task 11a.6)

Prices are in `data/reference/model_prices.csv`: one row per provider, model and the date the price took effect, in US
dollars per million input and output tokens, with the plan, where the price was read and when. The free tiers above are
rows at 0. A paid row is added only with the provider's price page and the date it was read (no price is guessed);
`MODEL_PRICES` in `.env` adds rows. Each call's cost is worked out in Decimal (`ai_steps/costs.py`) and kept on
`model_call.cost_usd`.

A company can set a monthly AI budget in US dollars (policy `ai_monthly_cost_cap_usd`, two administrators). Before each
model call the gateway checks that the month's spend plus the call's worst case (its token estimate at the output price)
stays within it; a model with no known price is not used while a budget is set; the next model of the route is tried
(a free one passes), else the caller's usual fallback applies. The administrators are told once a month when the spend
reaches `ai_cost_warn_pct` (80 % by default). The Automation page shows the month's spend.

## What is stored about a model call (task 11a.4)

- Prompts and raw model answers are **not** stored. `model_call` keeps the request hash, the prompt version, tokens,
  cost, time, outcome, the count of masked values, and what the call belonged to: the agent run, the capability, the
  person, the workflow and the document an AI step read.
- `agent_step` keeps the agent's decisions and the tool results (the company's own data, in its own database).
- `model_cache` keeps answers to identical prompts at temperature 0, so the same reading is not paid twice. It is to be
  emptied after 30 days (a trimming job is not built yet).

## Open points

- Choose the paid plan or provider for the pilot, and check its data terms with the client.
- Build the 30-item test sets in Phase 1 and re-run the evaluation. Today's sets cannot rank models that are all near 100%.
- Re-check qwen3.8-27b and mistral-large-4 when they are stable.
- A Gemini key was not provided; it can be added as another fallback provider without code changes.

Sources: Groq [rate limits](https://console.groq.com/docs/rate-limits) and [models](https://console.groq.com/docs/models); NVIDIA [NIM FAQ](https://forums.developer.nvidia.com/t/nvidia-nim-faq/300317), [General FAQ](https://docs.api.nvidia.com/nim/docs/product) and forum posts on the [40 requests a minute limit](https://forums.developer.nvidia.com/t/request-for-nvidia-nim-api-rate-limit-increase-40-200-rpm/375340). Limits on our own key were read from the live response headers.
