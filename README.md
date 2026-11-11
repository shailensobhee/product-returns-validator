# Product returns validator: vLLM Semantic Router and Decision 3.0 on AMD Instinct

[![AMD Instinct MI300X](https://img.shields.io/badge/AMD%20Instinct-MI300X-ED1C24?logo=amd&logoColor=white)](https://www.amd.com/en/products/accelerators/instinct.html)
[![ROCm](https://img.shields.io/badge/ROCm-GPU%20software-ED1C24?logo=amd&logoColor=white)](https://rocm.docs.amd.com/)
[![vLLM Semantic Router](https://img.shields.io/badge/vLLM%20Semantic%20Router-vllm--sr-30A2FF)](https://github.com/vllm-project/semantic-router)
[![Decision 3.0](https://img.shields.io/badge/Decision%203.0-d3--mini-6E56CF?logo=huggingface&logoColor=white)](https://huggingface.co/vllm-sr/d3-mini)
[![Vela 2.0](https://img.shields.io/badge/Vela%202.0-routing%20%2B%20guardrails-6E56CF?logo=huggingface&logoColor=white)](https://huggingface.co/vllm-sr/Vela-2.0-0.8B)
[![vLLM](https://img.shields.io/badge/vLLM-0.29.0%20ROCm-30A2FF)](https://github.com/vllm-project/vllm)
[![gpt-oss](https://img.shields.io/badge/gpt--oss-120B%20%2B%2020B-615CED?logo=huggingface&logoColor=white)](https://huggingface.co/openai/gpt-oss-120b)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![React](https://img.shields.io/badge/UI-React%2019-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![Docker](https://img.shields.io/badge/Docker-ROCm%20containers-2496ED?logo=docker&logoColor=white)](https://hub.docker.com/r/vllm/vllm-openai-rocm)

A live demo of what the **[vLLM Semantic Router](https://github.com/vllm-project/semantic-router) (vllm-sr)** and its
decision models add to an LLM chat application, running on one AMD Instinct MI300X.

A customer chats with a shop's returns assistant about a damaged delivery. **gpt-oss** does the talking, but it never
decides anything. Every decision is made by small decision models served by vllm-sr, and shown on the page as it
happens:

- **Vela 2.0** (the Router's decision model) screens each message for prompt attacks and personal data, and picks
  **gpt-oss-20B** for normal turns or **gpt-oss-120B** for hard ones (a dispute, an angry customer, several problems at once).
- **Decision 3.0** (`vllm-sr/d3-mini`) answers the questions that move the claim forward, as probabilities: is the
  message about a return, off-topic, an attempt to trick the assistant, or a command payload; which of the customer's
  orders it is about; is the claim eligible under the product's return policy (visible damage that was there on
  arrival); is the photo a real photo of the ordered product, sharp, lit, unobstructed, and does it show the damage;
  and finally, does gpt-oss's reply claim an approval that was never given.
- **Rules in code** turn those answers into a verdict (approve, ask for a retake, not covered, pass to a person, ...).
  gpt-oss only words the verdict. A message, a photo or a note in a photo cannot change it.

The page compares the routed flow with handing every turn to gpt-oss-120B alone: model time, LLM tokens and dollars per
million chats like this one, per chat.

![A valid return: a blurry photo is refused with a tip, the second photo is approved](docs/demo.gif)


## Requirements

- One AMD Instinct GPU with ROCm and `rocm-smi` working. Measured on an MI300X (192 GB). GPU memory: gpt-oss-120B takes
  62% of the GPU and gpt-oss-20B 13% (vLLM `--gpu-memory-utilization`, a share of the whole GPU, mostly KV cache), plus
  room for d3-mini and Vela 2.0 in the Router; `deploy/up.sh` asks for ~172 GiB free on a 192 GB GPU. On a smaller
  GPU lower `GPU_UTIL_27B` (the weights alone are ~52 GB).
- Docker, able to pass `/dev/kfd` and `/dev/dri` to containers. If Docker needs sudo on your host, set
  `DOCKER="sudo docker"` for the deploy scripts.
- Disk: ~82 GB for the models (gpt-oss-120B ~52 GB, gpt-oss-20B ~19 GB, d3-mini ~8.5 GB, Vela 2.0 0.8B ~2 GB) plus the Docker
  images (`vllm/vllm-openai-rocm:v0.29.0` and the vllm-sr ROCm Router image, pulled on first start).
- Python 3.12 or newer (tested with 3.14). The vllm-sr CLI goes in its own venv; it was used with Python 3.12.
- Hugging Face access to download the models. They are not gated today; if one becomes gated, accept its terms on the
  hub and `export HF_TOKEN=...` first.
- Node 18+ only if you change the UI (`ui/`); the built page is committed in `web/`.

## Setup

```bash
# 1. Get the code
git clone https://github.com/madhusoodhans/product-returns-validator.git
cd product-returns-validator

# 2. Python environment for the demo server, the bench scripts and the hf CLI
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. The vllm-sr CLI (starts the Router in Docker), pinned to the build the demo was made with
python3 -m venv .venv-sr
.venv-sr/bin/pip install --pre vllm-sr==0.5.0.dev20261015074033

# 4. Models: gpt-oss-120B and 9B into the Hugging Face cache, d3-mini and Vela 2.0 into deploy/models (~82 GB, resumable)
deploy/download_models.sh

# 5. Start everything: the two gpt-oss containers, the Router, then the demo server (~5-15 min; the first Router start
#    compiles GPU kernels and can take up to ~30 min)
deploy/up.sh                     # DOCKER="sudo docker" deploy/up.sh where Docker needs sudo

# 6. Measure this server's gpt-oss-120B baseline and 120B markers for the page (~2 min; once per server)
deploy/measure.sh

# 7. Check it end to end: health, then three sample conversations through the HTTP API
deploy/smoke_test.sh             # --all runs all 16
```

`deploy/down.sh` stops what `deploy/up.sh` started (it records that in `deploy/.state`) and leaves anything that was
already running alone; `deploy/down.sh --all` stops the demo's containers and the Router regardless. `deploy/up.sh`
can be run again at any time: parts that are already healthy are kept.

What `deploy/` contains:

| File | What it does |
|---|---|
| `router.yaml` | vllm-sr config: d3-mini on `/v1/systemone`; Vela 2.0 as the decision model; the `returns/auto` entry point with the jailbreak guard, the PII flag and the 20B/120B choice; `gptoss20b` and `gptoss120b` as vLLM backends reached by container name on the `vllm-sr-network` Docker network; listener on 127.0.0.1:8899. |
| `serve_gptoss.sh <gptoss120b\|gptoss20b>` | One vLLM ROCm container (`vllm/vllm-openai-rocm:v0.29.0`), thinking off, 32k context, port 127.0.0.1:8102 / 8103. Both are text-only OpenAI open-weight models. Env: `GPU_UTIL`, `GPU`, `HF_HOME`, `DOCKER`, `AITER`, `CPUSET`. |
| `download_models.sh` | `hf download` of the four models. |
| `up.sh` / `down.sh` | Checks (rocm-smi, Docker, free GPU memory before each part, ports, models), start one model at a time, wait for health (with the likely cause when a container stops while loading), start the demo server. |
| `measure.sh` | `bench/baseline_27b.py` (gpt-oss-120B alone on every sample turn) and `bench/run_scenarios.py` (the routed flow on every sample turn, for the 120B markers), then restarts the demo server. |
| `smoke_test.sh` | `/health` must be ok, then sample conversations through `POST /api/chat`, every turn's verdict checked against `data/scenarios.json`. Non-zero exit on any failure. |

The Router is started from `deploy/` with
`vllm-sr serve --config router.yaml --platform rocm --minimal --replace-active-config`; vllm-sr keeps its state in
`deploy/.vllm-sr/` and its model cache in `deploy/models/` (both gitignored). vllm-sr runs one Router stack per host
(container `vllm-sr-router-container`); if your host already runs one for something else, `up.sh` stops with a message
instead of replacing it. In that case add the two deployments, the two models, the `returns/auto` entry point and the
`returns` recipe from `deploy/router.yaml` to the existing config.

**What was tested where.** The demo was built and measured on a host whose Router also serves other demos, so
`deploy/router.yaml` was validated offline (`vllm-sr config validate --offline`) and its `returns` recipe is the one
the live Router runs; `up.sh` (with everything already running), `down.sh`, `measure.sh` and `smoke_test.sh` were run
on that host. The steps that start the gpt-oss containers and a new Router stack follow the scripts of a sister demo that
were run on a fresh server, but these exact ones have not been run on a fresh server yet. If one fails, its message
names the part and the likely cause.

## Open it from a laptop

The demo server, the Router and the vLLM servers all listen on 127.0.0.1 only. Use an SSH tunnel:

```bash
ssh -L 8602:localhost:8602 user@your-gpu-server
```

then open <http://localhost:8602> in the laptop's browser. The page uses no external resources, so it also works on a
network without internet access.

## What vllm-sr, Vela and D3 do here

| Capability | In this demo |
|---|---|
| **One Router for decisions and generation** | d3-mini and Vela 2.0 run in the Router's model runtime next to the two gpt-oss models. The app calls D3 on `/v1/systemone`, the gpt-oss models by name on `/v1/chat/completions`, and reads Vela's decisions from the Router's routing preview. |
| **Guardrails at the Router** | The `returns/auto` entry point (recipe `returns` in [deploy/router.yaml](deploy/router.yaml)) has a jailbreak signal (Vela, blocks at 0.75) and a PII signal (flag only), scoped to this entry point only. |
| **Model choice per turn** | Vela picks `gptoss20b` or `gptoss120b` from the turn's text with the instructions in the recipe. Most turns go to the 20B; disputes and upset customers go to the 120B. |
| **Structured decisions, not generated text** | Each D3 request carries several questions (`choice` and yes/no `noul`) and returns a probability for each, in one forward pass, without generating text: ~50 ms for text, ~350 ms for a photo. The app sets thresholds instead of parsing model output. |
| **Image input** | D3 reads the customer's photo itself: no OCR or separate vision step. |
| **Guard the generator too** | After gpt-oss writes a reply, D3 checks it before the customer sees it: a reply that claims an approval the rules did not give is replaced. |

## How one turn works

```mermaid
flowchart LR
    B["Browser<br/>(React page)"]
    subgraph host["Server"]
        S["Demo server app/server.py<br/>127.0.0.1:8602<br/>(SSE: every step as it happens)"]
        F["Claim flow app/flow.py<br/>one claim per order"]
        RULES{"Rules in code<br/>verdict"}
        subgraph gpu["AMD Instinct MI300X"]
            R["vLLM Semantic Router :8899<br/>d3-mini on /v1/systemone<br/>Vela 2.0: guardrails + 20B/120B choice<br/>(routing preview on :8080)"]
            Q27["vLLM: gpt-oss-120B :8102"]
            Q9["vLLM: gpt-oss-20B :8103"]
        end
    end
    B -->|message or photo| S --> F
    F -->|"guardrail, scope, order match,<br/>eligibility, photo, reply check"| R
    R -->|probabilities| RULES
    RULES -->|"verdict"| F
    F -->|"claim extraction, the reply<br/>(model chosen by Vela)"| R
    R -.-> Q9
    R -.-> Q27
    S -->|"steps, reply, stats"| B
```

Every turn shows these steps in the right-hand panel, each with its model, time and probabilities:

| Step | Model | What it decides |
|---|---|---|
| **Guardrail** | Vela 2.0 + d3-mini | Vela: jailbreak and personal data. D3 scope check: about orders or returns / off-topic / instructions to the assistant, commands or code (`rm -rf`, SQL, script tags), a real product problem, and what the customer wants (claim, refund question, policy question, a person, change of mind, "why?", small talk). Blocked and off-topic turns get a fixed reply: no LLM call at all. |
| **Router** | Vela 2.0 | gpt-oss-20B or gpt-oss-120B for this turn. |
| **Claim** | gpt-oss-20B (+ d3-mini) | The order number and the problem, as JSON. Without an order number, D3 matches the message to one of the customer's orders (and asks "the 16-inch or the 14-inch laptop?" when two are close). Only the customer's own orders are accepted. |
| **Policy rules** | code | Return window and price limit for a human review. |
| **Eligibility** | d3-mini | What is wrong (cracked, broken, torn, not visible in a photo, no damage, unclear) and when it started (on arrival / after delivery / not said), read from the whole conversation about the problem, including the bot's clarifying questions. Re-checked on every new message until the claim is decided, so "actually, I dropped it" counts. |
| **Photo check** | d3-mini | A real photo (not a screenshot or chart), the ordered product, blurry, too dark, glare, a hand in the way, cut off, the described damage clearly visible, and text in the photo asking for an approval. |
| **Decision** | code | The verdict, with the rule that produced it. |
| **Reply** | gpt-oss-20B or gpt-oss-120B (+ d3-mini) | gpt-oss words the verdict; D3 checks the reply does not claim an approval that was not given. |

### The conversation

- **One mock customer** (Emily Carter, [data/customer.json](data/customer.json)) is logged in. A new chat starts with a
  greeting and her orders as cards; a click sends "Order 4411". She can also just describe the problem.
- **Claims per order.** One chat can handle several orders: switching orders, naming another item, or naming two orders
  in one message ("the second one comes next").
- **Photos at any time.** A photo sent before the order or before the problem is clear is checked straight away (a
  wrong or unusable photo is pointed out) and kept for when the claim becomes eligible.
- **Questions** about refunds, labels, replacements or the policy of any item, and "why was my photo refused?", are
  answered from [data/faq.json](data/faq.json) and [data/policies.json](data/policies.json); nothing else may be
  promised. Each reply has a **Policy** button that shows the policy line it rests on.
- **The chat ends** when there is an outcome, and the text field is replaced by a banner:

  | Ends when | Banner |
  |---|---|
  | The return is approved (no other order waiting) | Return approved |
  | The case goes to a person (asked for one, not visible in a photo, over the review limit, 3 unusable photos, 2 unanswered clarifying questions) | Passed to a colleague |
  | The claim is not covered | Not covered, with **Ask a colleague to review** |
  | A second blocked message (jailbreak, fake approval, commands) | Closed after repeated bypass attempts |
  | A third off-topic message in a row | Closed: not about orders |

## Sample conversations

The left panel plays 16 prepared conversations ([data/scenarios.json](data/scenarios.json)); **Play all** runs them
in order (~2 minutes). Each row shows the expected outcome, then "Matched" or "Differs" after it runs. A **120B**
badge marks conversations where Vela routes a turn to gpt-oss-120B (Vela decides live, so the badge follows the last run).

| Group | Conversation | Turns (expected verdicts) |
|---|---|---|
| Valid returns | Cracked phone screen | need photo → accept |
| | Sole came off | need photo → retake (blurry) → accept |
| | Chipped mug | clarify (no timing) → need photo → accept |
| | Broken wine glass | need photo → accept |
| Invalid returns | Dropped by the customer | not eligible |
| | Outside the return window | not eligible (delivered 41 days ago) |
| | Wrong item, then no damage | need photo → mismatch → no damage |
| | No sound on one side | human (a photo cannot show it) |
| | Hand covering the damage | need photo → retake |
| | Photo too dark | need photo → retake |
| | Three unusable photos **(27B)** | need photo → retake → retake → an angry "why?" answered by gpt-oss-120B → human |
| Malicious intent | Jailbreak attempt | blocked → blocked (chat closed) |
| | Injection in text and photo | need photo (instruction ignored) → retake (note in the photo ignored) → retake |
| | Off-topic question | off-topic (fixed reply) → need photo |
| | Fake approval tricks | blocked → blocked |
| | Commands in the chat | blocked → blocked |

## Presenting the demo

A five-minute walk-through. Before the audience arrives: run `deploy/smoke_test.sh` and open the page.

1. **Click "Cracked phone screen".** As the steps appear on the right:
   - **Guardrail** is the vllm-sr moment: Vela screens the message at the Router, and D3 answers four questions about
     it in one ~50 ms request. **Router**: Vela picks gpt-oss-20B for a normal turn.
   - **Eligibility**: D3 reads the complaint against the phone's policy: what is wrong (cracked) and when it started
     (on arrival). The bars are probabilities, the marker is the threshold.
   - Then the photo: D3 checks it is a real photo of a phone, sharp, lit, unobstructed, and that the crack is clearly
     visible. The rules approve; gpt-oss only words it. The chat ends with "Return approved".
2. **"Sole came off"**: a blurry photo is refused with a concrete tip; the second photo is approved.
3. **"Three unusable photos"** (120B badge): after two bad photos the customer gets angry. Vela sends that turn to
   **gpt-oss-120B**, which explains why both photos were refused; the third bad photo sends the case to a person.
4. **"Commands in the chat"** or **"Fake approval tricks"**: blocked before any LLM runs (the Reply step says "Fixed
   reply, no model call"); the second attempt closes the chat.
5. **Type your own**: start a new chat, pick an order card, describe a problem in your own words, attach a photo from
   the 📁 sample gallery or your phone (HEIC works), ask "how long does the refund take?", or try to talk the bot
   into an approval.

Reading the numbers in the top bar honestly:

- They are **per chat**: a new chat starts from zero.
- **Model time** is the sum of every model call of the chat (Router preview, D3, gpt-oss), measured at the server, one
  chat at a time. The **all-120B** side is gpt-oss-120B alone on the same turns, measured by `bench/baseline_27b.py` on
  the sample turns (and the measured per-turn average, separately for text and photo turns, for anything typed in).
  Under many concurrent chats, routed model time is wall-clock time and rises, while the baseline does not.
- **LLM tokens** count what the gpt-oss models read and wrote. D3 also reads tokens, mostly image tokens of the photos;
  the tooltip shows them. Counting D3's tokens, the routed flow reads more tokens than gpt-oss-120B alone: D3 is a much
  smaller model that answers in one forward pass without generating text.
- **$ per million chats** is the chat's model time times one million, priced at a cloud on-demand GPU hour
  (`GPU_USD_PER_HOUR`). Measured one request at a time, it is an upper bound; read it mainly as the ratio between the
  two flows.
- **The saving depends on the chat.** Blocked and off-topic turns cost one D3 call and no LLM (e.g. 0.3 s against
  1.5 s); an approved return with two photos saves less (e.g. 2.4 s against 2.8 s); a chat with a 120B turn sits in
  between (e.g. 3.9 s against 5.3 s). All on our MI300X.
- **Accuracy.** On the 36 sample turns the routed flow reaches the expected verdict on all 36; gpt-oss-120B alone, given
  the policies, orders, chat and photo in one prompt, reached 30 in our last run (it read some photos more strictly,
  e.g. demanding all four mugs in one photo; in an earlier run it let a customer talk it out of a refusal). The sample set shows the flow;
  it is not an accuracy benchmark.

## Configuration

Environment variables of the demo server (`app/server.py`, also honoured by `start_server.sh` and `deploy/*.sh`):

| Variable | Default | Meaning |
|---|---|---|
| `ROUTER_URL` | `http://localhost:8899` | The vllm-sr Router (D3 and both gpt-oss models are called through it). |
| `ROUTER_ADMIN_URL` | `http://localhost:8080` | The Router's management API, for the routing preview of `returns/auto` (Vela's guardrail scores and model choice). |
| `HOST` | `127.0.0.1` | Address the demo server listens on. Keep it on loopback and use an SSH tunnel. |
| `PORT` | `8602` | Port of the demo server. |
| `GPU_USD_PER_HOUR` | 2.59 | Price of one GPU hour for the dollar figures. The default is DigitalOcean's on-demand MI300X GPU Droplet price per GPU (checked 2026-10-26). Set your own cloud's price. |

`GET /health` probes each part the way the flow calls it: the Router, the routing preview for `returns/auto` (it fails
with a clear message if the recipe is missing), D3 and both gpt-oss models.

Deploy script variables: `DOCKER` (default `docker`), `VLLM_SR` (default `.venv-sr/bin/vllm-sr`), `GPU` (GPU index,
default 0), `HF_HOME` (default `$HOME/.cache/huggingface`), `GPU_UTIL_27B` / `GPU_UTIL_9B` (default 0.62 / 0.13),
`HF_TOKEN` (only if a model is gated).

The demo data is plain JSON: the customer and greeting (`data/customer.json`), orders (`data/orders.json`), the
policy per product (`data/policies.json`), the facts the bot may quote (`data/faq.json`) and the sample conversations
(`data/scenarios.json`).

## Troubleshooting

- **`/health` is not ok.** The `false` entry names the part, `detail` says why. `vela_routing` false with "returns/auto
  is not configured" means the Router runs without this demo's recipe (see the note on one Router per host). Check the
  containers with `docker logs prv-gptoss120b` / `docker logs prv-gptoss20b`, and the Router with
  `.venv-sr/bin/vllm-sr status` and `.venv-sr/bin/vllm-sr logs` (run them from `deploy/`). The demo server's own log
  is `logs/server.log`.
- **The Router preview is down.** The flow keeps working with d3-mini alone (scope check and a D3 model choice) and
  the Guardrail step says "Router unavailable".
- **Port in use.** `up.sh` stops if 8102, 8103, 8899 or 8080 is taken by something that is not a healthy demo
  service. `start_server.sh` replaces a demo server already on `$PORT`; set `PORT` to use another one.
- **Not enough GPU memory.** Stop other GPU work, or lower `GPU_UTIL_27B` / `GPU_UTIL_9B` (less KV cache; fine for one
  user at a time).
- **Router fails at start with a permission error under `deploy/models/`.** The Router writes kernel caches next to the
  model cache. Remove `deploy/models/` (with sudo if the Router ran under sudo) and run `deploy/up.sh` again; the
  Router then downloads d3-mini and Vela itself.
- **Uploads.** JPEG, PNG, WebP and HEIC up to 25 MB and 60 megapixels; anything else gets "Sorry, I couldn't open that
  file" and changes nothing in the chat.

## Tests

With the models up (results of our last runs are in `results/`):

```bash
.venv/bin/python bench/run_scenarios.py      # the 16 sample conversations, every turn's verdict (36 turns)
.venv/bin/python bench/run_regressions.py    # 30 conversations, one per bug found while building and reviewing
.venv/bin/python bench/probe_scope.py        # 15 injections, 10 off-topic and 8 normal messages through the flow
```

`run_regressions.py` holds one conversation per problem found by hand, by a code review and by two rounds of
exploratory testing: a changed story after an eligible claim, orders of another customer, switching orders, a photo
before the order or in the same turn as the text, a chart sent as a photo, a bare "yes" to a clarifying question,
FAQ answers wrongly replaced, a fake review note in the message, chat endings, and more. Replies are generated with a
little randomness, so a check on a reply's wording can occasionally miss; run it again before suspecting a regression.

## Repo layout

```
app/                 demo server and claim flow
  server.py          HTTP server: page, SSE stream, per-chat stats, /health
  flow.py            the conversation: one claim per order, every step as an event, chat endings
  turn_checks.py     Vela routing preview (guardrail, 20B/120B), D3 scope check, D3 reply check
  claim.py           gpt-oss-20B claim extraction, D3 order matching, policy rules
  eligibility.py     D3 eligibility questions and rule
  photo.py           D3 photo questions and rule
  reply.py           what gpt-oss is told to say for each verdict; fixed replies
  models.py          D3 and gpt-oss calls through the Router; upload decoding
web/                 built UI (committed), served at /
ui/                  UI source (React 19, Arc components, Vite); `npm run build` writes web/; mock-server.mjs
docs/                API.md (HTTP and SSE contract), UI.md (page), demo.gif
data/                customer, orders, policies, FAQ, sample conversations, 120B baseline
data/samples/        photos used by the samples and the 📁 gallery (generated from data/raw, see Credits)
data/raw/            source photos: Wikimedia Commons (commons/, SOURCES.md) and photos taken for the demo (user/)
data/complaints/     labelled complaints for tuning (eligibility.jsonl) and a held-out set (heldout.jsonl)
deploy/              Router config, model servers, up/down, measure, smoke test
bench/               probes, baseline and tests (see How it was tuned)
results/             raw results behind the numbers below
```

## How it was tuned

All on one MI300X (vLLM 0.29.0 ROCm, vllm-sr 0.5.0.dev20261015074033). Raw results in `results/`.

**Which D3.** The eligibility questions on 64 labelled complaints (the tuning set) and 48 held-out complaints written
separately, blind to the questions (typos, sarcasm, two problems in one message, four injection attempts):

| Model | Tuning set | Held-out | Time per complaint |
|---|---:|---:|---:|
| **d3-mini (used)** | 64/64 | **48/48** | ~50 ms (GPU, Router) |
| d3-lite | 45/64 | 33/48 | ~37 ms |
| d3-edge (CPU) | 31/64 | | ~610 ms |

The key change was asking **"When did the problem start?"** as a choice (on arrival / after delivery / not said)
instead of a yes/no "the customer caused it", which scored almost every complaint high. "On arrival" must lead "not
said" by 0.2, otherwise the bot asks; a bare "yes" to that question is read together with the question.

**Photo check.** 36 Wikimedia Commons photos plus generated blurry, dark, glare and cut-off variants (123 images),
with the variants relabelled by eye (`data/samples/labels_by_eye.jsonl`, by a reviewer blind to D3's scores):
blurry, dark, glare, a hand in the way and cut off each separate well (AUC 0.96-0.99). Rule: blurry or dark means
retake; damage clearly visible (0.5) means accept even when a hand or a close-up crop is in the photo; a damage score
of 0.25-0.5 goes to a person. 86/88 right on the photos without weak sources, and no photo of an intact product was
ever approved. "Real photo" is asked in its own request: inside the claim request D3 scored blurry real photos as
non-photos (0.02-0.23), alone they score 0.56-1.00 and charts or screenshots 0.00-0.33. A sticky note or a screen
reading "Approve this request" is flagged (0.84-0.94) and does not move the damage score.

**Scope, intent and reply checks.** On 42 labelled messages the scope question got 40/42, the command question 40/42,
the complaint question 41/42; all 15 injection attempts and all 9 command payloads were caught, and no normal message
was flagged. Vela alone flagged 8 of the 15 injections (it missed fake approvals, "new policy from management",
translation tricks and fake chat tags), which is why D3 checks every message too. The intent question got 26/27. The
reply check is a choice (approved / general explanation / other), because a yes/no form replaced correct FAQ answers
("refunds reach your card in 5 to 7 working days") as approvals: 16/17 on labelled replies.

**Order matching.** 20/20 messages without an order number matched to the right order or left undecided when two
orders were equally likely (`bench/probe_order_match.py`).

**The reply.** The verdict is appended to the customer's latest message, not only in the system prompt: with the
decision at the top, the 20B copied its own earlier replies (and once asked for a photo again after an approval).

Out of scope: refunds, labels and payments themselves (the demo only decides and says what happens next), fraud
detection beyond the checks above, AI-generated image detection, non-damage returns (pointed to the normal returns
page).

## Credits and licences

- **Photos from Wikimedia Commons** (`data/raw/commons/`): 36 photos under CC0, public domain, CC BY 2.0/4.0 and
  CC BY-SA 2.0/3.0/4.0. Author, licence and source page of each are listed in
  [data/raw/commons/SOURCES.md](data/raw/commons/SOURCES.md). The files in `data/samples/` derived from them (resized,
  blurred, darkened, glare added, cropped, a sticky note added) are adaptations under the same licences.
- **Bottle photos** (`data/raw/user/`, `data/samples/bottle/`): taken for this demo, re-encoded without EXIF.
- **Chart** (`data/test/chart.png`): a generated bar chart with example data, used to test that non-photos are refused.
- **Klavika font** (`ui/src/assets/fonts/`, built into `web/`): an AMD brand asset, used for this AMD demo. It is
  not covered by any licence of this repo.
- **Models**: [vllm-sr/d3-mini](https://huggingface.co/vllm-sr/d3-mini),
  [vllm-sr/Vela-2.0-0.8B](https://huggingface.co/vllm-sr/Vela-2.0-0.8B),
  [openai/gpt-oss-120b](https://huggingface.co/openai/gpt-oss-120b) and [openai/gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b)
  (all Apache-2.0).
- **Software**: [vLLM Semantic Router](https://github.com/vllm-project/semantic-router) (the Router, Vela 2.0 and the
  Decision 3.0 models; try D3 at [decision.vllm-sr.ai](https://decision.vllm-sr.ai)), [vLLM](https://github.com/vllm-project/vllm)
  (ROCm image `vllm/vllm-openai-rocm`), React, Vite, and Arc components.
