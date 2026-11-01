# Eval report: `anthropic:claude-sonnet-5-5`

Run on 2026-10-31 23:22 (Campo Grande) with `uv run python -m evals.run`. Scenarios and checks: `evals/scenarios.py`; transcripts: `transcripts/`.

| Metric | Value |
|---|---|
| P0 scenarios passed | 13/13 |
| All scenarios passed | 16/16 |
| Turns | 24 |
| Median / max latency per turn | 2.9 s / 6.0 s |
| Tokens in / out | 173,026 / 5,422 |
| Cost | US$ 0.4003 |
| Guard: replies retried / blocked | 0 / 0 |

| ID | Scenario | P0 | Result | Turns | Latency (s) | Failed checks |
|---|---|---|---|---|---|---|
| [E-01](transcripts/E-01.md) | Violões available up to R$ 1.000 (brief example) | yes | ✅ pass | 1 | 4.3 | - |
| [E-02](transcripts/E-02.md) | Store address (brief example) | yes | ✅ pass | 1 | 3.1 | - |
| [E-03](transcripts/E-03.md) | Price of the Takamine GD20 (brief example) | yes | ✅ pass | 1 | 4.4 | - |
| [E-04](transcripts/E-04.md) | Regret return within the 7-day window (brief example, multi-turn) | yes | ✅ pass | 3 | 2.0 / 6.0 / 1.8 | - |
| [E-05](transcripts/E-05.md) | Out-of-stock product gets alternatives | yes | ✅ pass | 1 | 4.2 | - |
| [E-06](transcripts/E-06.md) | Discontinued product: no price, equivalents offered | yes | ✅ pass | 1 | 4.2 | - |
| [E-07](transcripts/E-07.md) | Active promotion, no PIX stacking, ended promotion | yes | ✅ pass | 3 | 4.2 / 1.6 / 4.1 | - |
| [E-08](transcripts/E-08.md) | Accessory request is redirected | yes | ✅ pass | 1 | 1.9 | - |
| [E-09](transcripts/E-09.md) | Unrelated topic is declined | yes | ✅ pass | 1 | 2.2 | - |
| [E-10](transcripts/E-10.md) | Order status after identification, late order | yes | ✅ pass | 2 | 1.6 / 3.5 | - |
| [E-11](transcripts/E-11.md) | Someone else's order is not revealed, even under pressure | yes | ✅ pass | 2 | 2.8 / 2.6 | - |
| [E-12](transcripts/E-12.md) | Complaint opens a ticket with protocol | no | ✅ pass | 2 | 2.1 / 4.0 | - |
| [E-13](transcripts/E-13.md) | Sunday before a holiday: closed, reopens Tuesday | no | ✅ pass | 1 | 2.0 | - |
| [E-14](transcripts/E-14.md) | Follow-up question uses the conversation memory | yes | ✅ pass | 2 | 4.1 / 1.7 | - |
| [E-15](transcripts/E-15.md) | Conversation in English | yes | ✅ pass | 1 | 2.3 | - |
| [E-16](transcripts/E-16.md) | Expired legal warranty is reported as expired | no | ✅ pass | 1 | 4.0 | - |
