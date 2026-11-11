# API contract (server :8602, draft 2026-10-26)

The UI is built against `ui/mock-server.mjs` first; the real server (Phase 2) implements the same contract.

## GET /api/samples
The 16 preloaded conversations for the left panel, in 3 groups (valid 4, invalid 7, malicious 5) (from data/scenarios.json), plus every product policy.
```json
{"groups": [
  {"key": "valid", "title": "Valid returns", "scenarios": [
     {"id": "v-phone", "title": "Cracked phone screen", "subtitle": "Clear photo, approved", "order": "4411",
      "product": "phone", "expected": "accept",
      "turns": [{"text": "Hi, order 4411. The phone screen was cracked when I opened the box.", "expect": "need_photo"},
                {"image": "samples/phone/phone_3_clear.jpg", "expect": "accept"}]}]},
  {"key": "invalid", "title": "Invalid returns", "scenarios": ["..."]},
  {"key": "malicious", "title": "Malicious intent", "scenarios": ["..."]}],
 "policies": {"phone": {"name": "Smartphone", "window_days": 10, "human_review_over": null,
                        "covered": "...", "not_covered": ["..."], "photo": "..."}}}
```
`expected` = the scenario's own `expected` if set, else the last turn's `expect` that is not `closed`. `order` and `product` may be null (jailbreak scenario).

## GET /api/customer
The one mock customer, logged in: `{"name": "Emily Carter", "email": "emily.c****@example.com", "greeting": "Hi Emily! ...",
"orders": [{"order": "4411", "product": "phone", "product_name": "Smartphone", "item": "...", "price": 499,
"delivered_days_ago": 2, "window_days": 10}, ...]}` (most recent first).
- Every new chat starts with `greeting` as the first assistant message (shown by the UI; the server's chat history
  starts with it too). No model call.
- Clicking an order card sends a normal visible user message `Order 4411` (so the Claim step runs as usual).
- Without an order number, the `claim` step runs D3 order matching: its detail then has `order_match`, a D3 detail
  like `eligibility` with one choice question `order` whose `probs` keys are order ids (or `unclear`) and a `labels`
  map id -> item name; `threshold` 0.75. Matched: summary "Order 6230: ... (matched by D3, 0.99)". Two close
  candidates: verdict `need_order`, summary "Which order? A (order x) or B (order y)" and the reply asks.

## GET /api/gallery
Images in the sample folder for the 📁 picker: `[{"path": "samples/phone/phone_1_clear.jpg", "product": "phone", "case": "damaged_clear"}]`.
Images are served at `/<path>`.

## POST /api/chat → `text/event-stream`
Body: `{"session": "uuid", "text": "...?", "image": "samples/... or data:image/jpeg;base64,...?"}` (text, image or both).

Events (each `data:` is JSON with a `type`):

| type | fields | meaning |
|---|---|---|
| `step` | `node`, `status`, `model?`, `ms?`, `summary?`, `detail?` | a flow node changed state |
| `token` | `text` | streamed reply text |
| `message` | `role: "assistant"`, `text`, `verdict`, `order`, `policy_ref` | final reply of this turn |
| `stats` | stats object (below) | top bar update |
| `done` | `turn` | end of turn; `turn` = turn id, for replaying its flow |
| `error` | `message` | |

`node` (in flow order): `guardrail`, `router`, `claim`, `policy`, `eligibility`, `image_check`, `decision`, `reply`.
`status`: `waiting | running | done | skipped | blocked | failed`. Nodes not used by this turn are sent as `skipped`
(e.g. a text-only turn skips `image_check`; a not-eligible claim skips `image_check` for the rest of the session).

`detail` by node:
- `guardrail`: `{"jailbreak": 0.02, "pii": false, "flagged": false}`
- `router`: `{"chosen": "gptoss20b", "candidates": {"gptoss20b": 0.81, "gptoss120b": 0.19}}`
- `claim`: `{"order": "4411", "product": "phone", "issue": "crack", "part": "screen"}`
- `policy`: `{"rules": [{"label": "Delivered 2 days ago, window 10 days", "ok": true}, {"label": "Price under review limit", "ok": true}]}`
- `eligibility` and `image_check`: D3 questions
  ```json
  {"model": "vllm-sr/d3-mini", "questions": [
     {"key": "when", "label": "When did it start", "type": "choice", "choice": "arrival",
      "probs": {"arrival": 0.97, "after": 0.02, "not_said": 0.01}},
     {"key": "blurry", "label": "Blurry", "type": "yesno", "prob": 0.86, "threshold": 0.6, "borderline_from": 0.45,
      "status": "bad"}],
   "escalated": [{"key": "damage_visible", "model": "vllm-sr/d3-flash", "prob": 0.39, "ms": 410}]}
  ```
  `status` per yes/no: `ok | borderline | bad`.
- `decision`: `{"verdict": "retake", "reasons": ["Blurry"], "rule": "any quality check bad -> retake"}`
- `reply`: `{"model": "gptoss20b", "tokens": 112}`

`message.order` (null until an order number is known): `{"order", "product", "item", "price", "delivered_days_ago"}`.
`message.policy_ref`: which part of that product's policy the reply rests on, for the policy popover:
`covered | not_covered | window | review_limit | photo | null`.

Verdicts: `accept | retake | mismatch | no_damage | not_eligible | human | clarify | blocked | need_photo | need_order |
need_problem | closed` (closed = the claim was decided in an earlier turn).

## GET /api/stats?session=<id>
Stats of ONE chat session (the top bar is per chat; a new chat or a newly played sample starts from zero). The
`stats` SSE event carries the same object for the session of that request.
```json
{"session": "s-123", "turns": 3, "d3_calls": 2, "d3_ms_median": 290, "share": {"gptoss20b": 1.0, "gptoss120b": 0.0},
 "blocked": 0, "image_checks_avoided": 0,
 "model_seconds": {"routed": 1.9, "baseline_27b": 3.1},
 "tokens": {"routed": 1300, "baseline_27b": 6800, "d3": 9100, "note": "..."},
 "usd_per_million_chats": {"routed": 1055, "baseline_27b": 1722}, "gpu_usd_per_hour": 2.0,
 "baseline": {"correct": 25, "turns": 30, "measured": "2026-10-26 09:10"}}
```
`baseline_27b` = what gpt-oss-120B alone took for the same turns (measured per sample turn by bench/baseline_27b.py;
the per-turn average for free-typed turns). `tokens.routed` counts gpt-oss tokens only; `tokens.d3` is D3's (mostly
image) tokens. `image_checks_avoided` is 0 or 1 per chat. There is no `chats` field any more.

## GET /api/turn/{turn}
The stored `step` events of a past turn, so clicking an earlier message replays its flow on the right:
`{"turn": "t3", "steps": [<step events>]}`.

## Scope and reply checks (2026-10-26)
- `guardrail` detail now also has `scope_check`: a D3 detail (like `eligibility`) with a choice `scope`
  (`returns | off_topic | instructions`, `labels` map for display names) and yes/no `command` (commands or code) and
  `complaint` (real product problem). Blocked when the Router flags a jailbreak, scope = instructions or command >= 0.5,
  unless complaint >= 0.5 (then "Ignored ...; real complaint handled"). Off-topic -> verdict `off_topic`.
- New verdict `off_topic`. Blocked and off-topic turns get a FIXED reply: `reply` step is `skipped` with summary
  "Fixed reply, no model call", and `claim` .. `image_check` are skipped.
- `reply` detail may have `reply_check`: `{"model", "claims_approval", "threshold": 0.5, "replaced", "ms",
  "original"?}`; when the reply claimed an approval that was not given, it is replaced by a fixed fallback and the
  summary says "Replaced: the reply claimed an approval (0.83)". Replies are now checked before they are streamed.
- Stats: new `off_topic` count per chat (next to `blocked`).
- On blocked / off-topic turns the `router` step is `skipped` with summary "Vela picked gpt-oss-20B; not called (fixed
  reply)". Stats `share` counts only turns where an LLM wrote the reply (`llm_turns`); null when there were none.

## Review fixes (2026-10-26, after the code review and exploratory test)
- Claims are per order: a chat can hold several. Switching orders or naming another of the customer's orders after a
  decision starts a new claim; `message.order` follows the current claim. Only the customer's own orders are accepted;
  any other number gets "not on your account" (no details of other orders are revealed).
- New verdicts: `info` (a question answered from the FAQ/policy: refund timing, replacement, policy, change of mind,
  "why?", greetings; the claim is unchanged), `bad_file` (upload not a readable image; fixed reply, nothing changes),
  `error` (a model or Router failure; an `error` event is followed by a `message` with verdict `error` so the turn
  ends cleanly; the UI should mark running/waiting nodes as `failed`). `closed` replies now depend on how the claim
  ended (approved / with a person / not covered).
- `guardrail` scope check has a 4th question `intent` (choice: claim, refund_info, policy_info, human,
  change_of_mind, why, chitchat; `labels`). "Wants a person" hands over to a person straight away.
- `router` detail: `chosen` may be null ("Vela picked no model (guard decision)"); there is no `candidates` map any more.
- `reply` summary says "checked by D3" only when the reply check ran (it does not run on approvals). The reply step's
  `ms` includes the reply check.
- `image_check` "Checked in an earlier turn" only when the photo really came in an earlier turn; a photo in the same
  turn as the text is checked after eligibility and decides the claim directly. A photo sent before the order was
  known is kept and checked once the order is known.
- `order_match` and `scope_check` details carry their own `ms` (D3 time of that part).
- Stats: `image_checks_avoided` is now a count of claims closed before any photo check; the all-120B baseline for
  free-typed turns uses separate averages for text turns (0.88 s, 1,712 tokens) and photo turns (1.28 s, 3,404).

## Chat ending and 120B marker (2026-10-26)
- `message.ended` is null while the chat is open, else `{"reason", "title", "text", "order", "actions"}`:
  `approved` (accepted, no other order waiting), `approved_and_handed_over`, `handed_over` (any hand-over to a person),
  `not_covered` (actions `["review", "new_chat"]`), `blocked` (2nd blocked message in the chat), `off_topic` (3rd
  off-topic message in a row). Other reasons have actions `["new_chat"]`.
- After the end, POST /api/chat answers with verdict `ended` (all steps `skipped` "The chat has ended", no stats, no
  state change). The only exception: `{"session", "action": "review"}` after `not_covered` hands the case to a person
  (verdict `human`, then `ended.reason` = `handed_over`).
- /api/samples: each turn has `model` (the LLM that wrote that reply in the last bench/run_scenarios.py run:
  `gptoss20b`, `gptoss120b` or null for a fixed reply) and each scenario `uses_27b`.
- Samples changed: "Dropped by the customer" is one turn (the chat ends on not covered); "Three unusable photos" has
  an angry customer turn that Vela routes to gpt-oss-120B (verdict `info`).

## Agreed with the UI build (2026-10-26)
- Image-only turns send `guardrail`, `claim`, `policy`, `eligibility` as `skipped` with a summary such as
  "Eligible in an earlier turn".
- Yes/no questions where yes is good ("Shows a phone"): `borderline_from` above `threshold`; colour always comes from `status`.
- Each `escalated` entry carries its own `status` (ok | borderline | bad).
- `image_check` detail also carries `image` (the path or turn image id D3 judged).
- POST /api/reset clears sessions and stats (between demo runs).
- Stats `usd_per_million_chats` uses env GPU_USD_PER_HOUR (default 2.59, see README.md).
