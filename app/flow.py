"""The returns chat flow: one Session per chat, one generator per turn yielding the docs/API.md events.

The state machine decides what runs; models answer questions; reply.py words the decision.

A chat can hold several claims, one per order (Claim). Each claim moves through
    need_problem -> (eligibility) -> need_photo -> closed (accept | not_eligible | human)
                     \\-> clarify (ask; re-checked with every new customer message about the problem)
Any new message about the problem re-runs eligibility until the claim is closed, so "actually I dropped it" after an
eligible claim still counts. A photo is checked whenever it arrives: before the order is known it is kept for later,
before the claim is clear its result is kept, and it decides the claim once the claim is eligible.
"""
import re

import claim
import eligibility
import photo
import reply
import turn_checks
from models import BadImage, chat_stream, image_url

NODES = ["guardrail", "router", "claim", "policy", "eligibility", "image_check", "decision", "reply"]
MAX_PHOTOS = 3
MAX_CLARIFY = 2
MATCH_SWITCH = 0.9
# "let's do the phone now" was read as wanting a person: naming another item without these words is a claim step
PERSON_WORDS = re.compile(r"\b(human|person|people|agent|manager|someone|somebody|staff|representative)\b", re.I)   # D3 order match needed to move to ANOTHER order without an order number
RULES = {
    "blocked": "instructions to the assistant, a jailbreak or commands, without a real complaint -> blocked",
    "off_topic": "not about orders or returns -> fixed reply",
    "bad_file": "the upload is not a readable image -> ask for a photo",
    "need_order": "no order of this customer yet -> ask for it",
    "need_problem": "no problem described -> ask",
    "clarify": "visible damage without timing, or unclear problem -> ask",
    "need_photo": "eligible -> ask for a photo",
    "retake": "photo unusable or damage hidden -> retake",
    "mismatch": "photo is not of the ordered product -> ask for the ordered item",
    "no_damage": "damage not visible in a usable photo -> ask for a closer photo",
    "accept": "eligible and damage clearly visible -> approve",
    "not_eligible": "policy or eligibility check failed -> not covered",
    "human": "not visual, over the review limit, too many photos or the customer asked -> a person",
    "info": "a question, not a claim step -> answer from the FAQ and policy, claim unchanged",
    "closed": "claim already decided -> answer only",
}
FAQ = claim.FAQ


class Claim:
    """One claim for one order."""

    def __init__(self, order):
        self.order = order
        self.stage = "need_problem"
        self.complaint = []      # (question the bot asked or None, customer message) about the problem
        self.clarified = 0
        self.photos = 0          # photos checked for this claim
        self.photo = None        # (verdict, reasons) of a usable photo checked before the claim was eligible
        self.policy_ok = None
        self.final = None        # (verdict, reason) once closed
        self.photo_checked = False
        self.last = None         # (verdict, reasons, reason) of the last claim decision, for "why?"
        self.photo_log = []      # (verdict, reasons) of every photo checked for this claim, for "why?"


class Session:
    def __init__(self, sid):
        self.sid = sid
        self.history = [{"role": "assistant", "content": claim.CUSTOMER["greeting"]}]  # chat for the LLM
        self.unmatched = []      # customer messages before an order is known (for D3 order matching)
        self.claims = {}         # order id -> Claim
        self.claim = None        # the claim this chat is on
        self.pending_image = None  # a photo sent before any order was known (data URL), checked once one is
        self.queued = []         # other order ids named in one message, handled after the current claim
        self.turns = 0
        self.ended = None          # set when the chat is over (see END): no further turns except a review request
        self.blocked_n = 0         # blocked turns in this chat (2nd ends it)
        self.off_topic_run = 0     # off-topic turns in a row (3rd ends it)

    # ------------------------------------------------------------------ one turn
    def turn(self, text=None, image=None, action=None):
        if self.ended and not (action == "review" and self.ended["reason"] == "not_covered"):
            yield from self._ended_turn()
            return
        force_person = False
        if action == "review":
            # the only thing allowed after "not covered": ask a colleague to review the decision
            text, image, force_person = "Please have a colleague review this decision.", None, True
            self.ended = None
        self.turns += 1
        tid = f"{self.sid}-t{self.turns}"
        calls = []                 # (model, ms, tokens) for the stats
        steps = {}

        def step(node, status, summary=None, model=None, ms=None, detail=None):
            ev = {"type": "step", "node": node, "status": status, "summary": summary, "model": model, "ms": ms,
                  "detail": detail}
            steps[node] = ev
            return ev

        def skip_rest(after, why):
            for n in NODES[NODES.index(after) + 1:NODES.index("decision")]:
                if n not in steps or steps[n]["status"] in ("waiting", "running"):
                    yield step(n, "skipped", why)

        text = (text or "").strip() or None
        verdict, ctx = None, {"reasons": []}
        # the upload is decoded first: a file that is not an image changes nothing in the chat
        img = None
        if image:
            try:
                img = image_url(image)
            except BadImage:
                verdict, ctx["fixed"] = "bad_file", "bad_file"
        user_msg = (reply.scrub(text) if text else "") + ("\n[The customer sent a photo]" if image else "")
        self.history.append({"role": "user", "content": user_msg.strip()})
        for n in NODES:
            yield step(n, "waiting")

        # 1. guardrail + 2. router: one Router preview (Vela 2.0) + the D3 scope check
        intent = "claim"
        recent = [{"role": "user", "content": self.history[-1]["content"]}]   # history would keep a block forever
        yield step("guardrail", "running" if text else "skipped", None if text else "No text in this turn")
        yield step("router", "running", None, "Vela 2.0 (vllm-sr)")
        pv, pv_ms = turn_checks.router_preview(recent)
        # cached repeats answer in ~1 ms: count them at the uncached median; a failed call counts what it took
        calls.append(("router", max(pv_ms, turn_checks.PREVIEW_UNCACHED_MS) if pv else pv_ms, 0))
        router_block = bool(pv) and pv["decision"] == "returns-guard"
        if text:
            prev = next((m["content"] for m in reversed(self.history[:-1]) if m["role"] == "assistant"), None)
            sc, sdetail, sms, stok = turn_checks.scope_check(text, prev)
            calls.append(("d3", sms, stok))
            intent = sc["intent"]
            command = sc["command"] >= 0.5
            names_own = any(n in claim.CUSTOMER["orders"] for n in re.findall(r"\b\d{4,6}\b", text))
            sure_normal = sc["scope"] == "returns" and sc["command"] < 0.2 and (
                sc["probs"].get("returns", 0) >= 0.9 or names_own)
            vela_false_alarm = router_block and sure_normal
            injection = (router_block and not sure_normal) or sc["scope"] == "instructions" or command
            if intent == "human" and not command:
                injection = False      # "I want a person, not a bot" reads as instructions; it is a fair request
            sdetail["ms"] = sms
            gdetail = {"jailbreak": round(pv["jailbreak"], 4) if pv else None, "pii": bool(pv) and pv["pii"] >= 0.5,
                       "router_decision": pv and pv["decision"], "scope_check": sdetail,
                       "complaint": round(sc["complaint"], 4), "model": "Vela 2.0 (vllm-sr) + vllm-sr/d3-mini"}
            jb = f"jailbreak {pv['jailbreak']:.2f}" if pv else "D3 only"
            coded = command and re.search(r"[;|&$<>{}`]|rm -|drop table|curl |powershell", text, re.I)
            what = ("commands or code" if coded else jb if router_block else "instructions to the assistant")
            gstatus, summary = "done", f"Passed ({jb}, on topic)"
            if vela_false_alarm:
                summary = f"Passed: Vela flagged {jb}, D3 sure it is a normal returns message"
            if injection and sc["complaint"] >= 0.5:
                summary = f"Ignored {what}; real complaint handled"
            elif injection:
                gstatus, summary, verdict = "blocked", f"Blocked: {what}", "blocked"
                ctx["fixed"] = "blocked_command" if coded else "blocked"
            elif sc["scope"] == "off_topic" and intent not in ("human", "refund_info", "policy_info"):
                gstatus, summary, verdict = "blocked", "Off-topic: fixed reply, no model call", "off_topic"
                ctx["fixed"] = "off_topic"
            if pv and pv["pii"] >= 0.5:
                summary += ", personal data flagged"
            gdetail["flagged"] = gstatus == "blocked"
            yield step("guardrail", gstatus, summary + ("" if pv else "; Router unavailable"),
                       "Vela 2.0 + D3 mini" if pv else "D3 mini", (pv_ms if pv else 0) + sms, gdetail)

        fixed = verdict in ("blocked", "off_topic", "bad_file")
        if pv and pv["model"] in ("gptoss20b", "gptoss120b"):
            model = pv["model"]
            picked = f"{'Hard' if model == 'gptoss120b' else 'Normal'} turn, {model_name(model)}"
            rdetail = {"chosen": model, "router_decision": pv["decision"], "reason": pv["reason"]}
        elif pv:
            model = "gptoss20b"
            picked = "Vela picked no model (guard decision); gpt-oss-20B by default"
            rdetail = {"chosen": None, "router_decision": pv["decision"], "reason": pv["reason"]}
        elif fixed:  # Router down and nothing for a model to do
            model, rdetail = "gptoss20b", {"chosen": None}
        else:  # Router down: d3-mini picks
            model, rdetail, rms, rtok = turn_checks.route(text, self.claim.stage.replace("_", " ") if self.claim else "new chat")
            calls.append(("d3", rms, rtok))
            picked = f"{model_name(model)} (Router unavailable, D3 picked)"
        if fixed:
            picked = (f"Vela picked {model_name(pv['model'])}; " if pv and pv["model"] else "") + "not called (fixed reply)"
        yield step("router", "skipped" if fixed else "done", picked,
                   "Vela 2.0 (vllm-sr)" if pv else "vllm-sr/d3-mini", pv_ms, rdetail)

        # 3-6. the claim
        if verdict in ("blocked", "off_topic"):
            yield from skip_rest("router", "Off-topic" if verdict == "off_topic" else "Blocked by the guardrail")
        elif verdict == "bad_file":
            yield from skip_rest("router", "The upload was not a readable image")
        elif intent in ("refund_info", "policy_info", "change_of_mind", "why", "chitchat") and not image \
                and (intent == "change_of_mind" or not (text and re.search(r"\b\d{4,6}\b", text))) \
                and not (intent != "policy_info" and self._other_claim_named(text)):
            verdict = yield from self._answer(intent, text, step, skip_rest, calls, ctx)
        elif force_person:
            verdict = yield from self._to_person(step, skip_rest, ctx)
        elif intent == "human" and not (self._other_claim_named(text) and not PERSON_WORDS.search(text or "")):
            verdict = yield from self._to_person(step, skip_rest, ctx)
        else:
            verdict = yield from self._claim_flow(text, img, step, skip_rest, calls, ctx)

        # 7. decision
        c = self.claim
        if verdict in ("accept", "not_eligible", "human") and c and not c.final:
            c.stage, c.final = "closed", (verdict, ctx.get("reason") or ", ".join(ctx["reasons"]))
        if c and verdict not in ("info", "closed", "blocked", "off_topic", "bad_file"):
            c.last = (verdict, list(ctx["reasons"]), ctx.get("reason"))
        if verdict == "closed" and c and c.final:
            ctx["what"] = f"closed_{c.final[0]}"
            ctx["previous"] = c.final[1]
            ctx["facts"] = ctx.get("facts") or [FAQ["label"], FAQ["refund"], FAQ["replacement"]] if c.final[0] == "accept" else ctx.get("facts")
        if self.queued and c and c.final and verdict in ("accept", "not_eligible", "human"):
            nxt = self.queued[0][0]
            ctx["extra_note"] = (f"Then say that next you will look at their other order {nxt} "
                                 f"({claim.ORDERS[nxt]['item']}).")
        # the chat ends when the customer has an outcome, or after repeated abuse
        self.blocked_n += verdict == "blocked"
        self.off_topic_run = self.off_topic_run + 1 if verdict == "off_topic" else 0
        end = None
        if self.blocked_n >= 2 and verdict == "blocked":
            end = "blocked"
        elif self.off_topic_run >= 3:
            end = "off_topic"
        elif verdict in ("accept", "not_eligible", "human") and not self.queued:
            end = {"accept": "approved", "not_eligible": "not_covered", "human": "handed_over"}[verdict]
            if verdict == "human" and c and c.final and c.final[0] == "accept":
                end = "approved_and_handed_over"
        yield step("decision", "done", verdict.replace("_", " ").capitalize(), None, 0,
                   {"verdict": verdict, "reasons": ctx["reasons"] or ([ctx["reason"]] if ctx.get("reason") else []),
                    "rule": RULES.get(verdict, "")})

        # 8. reply
        ctx.update(product=c and c.order["product"], order=c and c.order["order"], item=c and c.order["item"])
        if end and not ctx.get("fixed"):
            # the last message of the chat: no question the customer can no longer answer
            ctx["extra_note"] = (ctx.get("extra_note", "") + " " + reply.LAST_MESSAGE).strip()
        if ctx.get("fixed"):
            # blocked, off-topic and unreadable uploads get a fixed reply: no model call, nothing to steer
            key = ctx["fixed"] + ("_end" if end and ctx["fixed"] + "_end" in reply.FIXED else "")
            out = {"text": reply.FIXED[key], "ms": 0, "tokens": 0}
            yield step("reply", "skipped", "Fixed reply, no model call")
        else:
            yield step("reply", "running", None, model)
            out = {}
            for kind, val in chat_stream(model, reply.messages(self.history, verdict, ctx)):
                if kind == "done":
                    out = val  # buffered: the reply is checked before the customer sees it
            calls.append((model, out["ms"], out["tokens"]))
            rdetail = {"model": model, "tokens": out["tokens"]}
            summary, ms = f"{out['tokens']} tokens", out["ms"]
            approved = verdict == "accept" or (c and c.final and c.final[0] == "accept"
                                               and verdict in ("closed", "info", "human"))
            if not approved:
                # D3 reply check: the words must not claim an approval that was not given
                p, cms, ctok = turn_checks.reply_claims_approval(out["text"])
                calls.append(("d3", cms, ctok))
                ms += cms
                rdetail["reply_check"] = {"model": "vllm-sr/d3-mini", "claims_approval": round(p, 4), "threshold": 0.5,
                                          "replaced": p >= 0.5, "ms": cms}
                summary += ", checked by D3"
                if p >= 0.5:
                    rdetail["reply_check"]["original"] = out["text"]
                    key = ctx.get("what") or verdict
                    out["text"] = reply.FALLBACK.get(key, reply.FALLBACK["need_order"])
                    if end:
                        out["text"] = reply.FALLBACK.get(key + "_end", out["text"])
                    summary = f"Replaced: the reply claimed an approval ({p:.2f})"
            yield step("reply", "done", summary, model, ms, rdetail)
        for piece in out["text"].split(" "):
            yield {"type": "token", "text": piece + " "}
        self.history.append({"role": "assistant", "content": out["text"]})
        if end:
            self.ended = ending(end, c)
        yield {"type": "message", "role": "assistant", "text": out["text"], "verdict": verdict,
               "order": c and {k: c.order[k] for k in ("order", "product", "item", "price", "delivered_days_ago")},
               "policy_ref": policy_ref(verdict, ctx, c), "ended": self.ended}
        yield {"type": "done", "turn": tid, "calls": calls,
               "steps": [steps[n] for n in NODES if n in steps],
               "avoided": bool(c and c.final and verdict in ("not_eligible", "human") and not c.photo_checked)}

    def _ended_turn(self):
        """A message to a chat that has ended: nothing runs, nothing changes."""
        tid = f"{self.sid}-x{self.turns}"
        steps = []
        for n in NODES:
            ev = {"type": "step", "node": n, "status": "skipped", "summary": "The chat has ended", "model": None,
                  "ms": None, "detail": None}
            steps.append(ev)
            yield ev
        text = "This chat has ended. Please start a new chat if you need help with another order."
        yield {"type": "token", "text": text}
        yield {"type": "message", "role": "assistant", "text": text, "verdict": "ended", "order": None,
               "policy_ref": None, "ended": self.ended}
        yield {"type": "done", "turn": tid, "calls": [], "steps": steps, "avoided": False, "no_turn": True}

    # ------------------------------------------------------------------ questions that are not claim steps
    def _next_step(self):
        c = self.claim
        if not c:
            return reply.NEXT["need_order"]
        if c.final:
            return reply.NEXT[f"closed_{c.final[0]}"].format(item=c.order["item"])
        if c.stage == "clarify":
            q = next((m["content"] for m in reversed(self.history[:-1]) if m["role"] == "assistant"), "")
            return reply.NEXT["clarify"].format(question=q)
        return reply.NEXT[c.stage].format(item=c.order["item"])

    def _answer(self, intent, text, step, skip_rest, calls, ctx):
        """FAQ, policy questions, change of mind, "why?" and small talk: answered from facts, the claim is unchanged."""
        c = self.claim
        yield step("claim", "skipped", f"Not a claim step ({intent.replace('_', ' ')}); claim unchanged")
        yield from skip_rest("claim", "Not needed: a question, the claim is unchanged")
        ctx["next"] = self._next_step()
        facts = []
        if intent == "refund_info":
            facts = [FAQ["label"], FAQ["refund"], FAQ["replacement"]]
            if c and c.final:
                facts.append(f"This claim's status: {c.final[0].replace('_', ' ')}.")
            elif c:
                facts.append("This claim is not decided yet, so no label has been sent.")
        elif intent == "policy_info":
            named = claim.products_in(text)
            prod = named[0] if named else c and c.order["product"]
            if not prod:
                chosen, _c, mdet, mms, mtok = claim.match_order(text)
                calls.append(("d3", mms, mtok))
                prod = chosen and claim.ORDERS[chosen]["product"]
            if prod:
                p = claim.POLICIES[prod]
                facts = [f"{p['name']} policy: returns within {p['window_days']} days of delivery. Covered: {p['covered']} "
                         f"Not covered: {'; '.join(p['not_covered'])}. Photo needed: {p['photo']}"]
            else:
                facts = ["The return policy depends on the item. Ask which item they mean."]
        elif intent == "change_of_mind":
            facts = [FAQ["other_returns"]]
            ctx["extra_note"] = "Name the 'Returns page' in their account, in those words."
            ctx["next"] = reply.NEXT["none"] if not c or c.final else ctx["next"]
        elif intent == "why":
            if c and c.photo_log and any(v != "accept" for v, _r in c.photo_log):
                facts = [f"Photo {i} was not accepted: {', '.join(r).lower() or v.replace('_', ' ')}."
                         for i, (v, r) in enumerate(c.photo_log, 1) if v != "accept"]
                tips = {t for _v, r in c.photo_log for t in r if t in reply.TIPS}
                if tips:
                    facts.append("Tips: " + "; ".join(reply.TIPS[t] for t in tips) + ".")
                if c.final:
                    facts.append(f"The claim's status: {c.final[0].replace('_', ' ')}.")
            elif c and c.last:
                v, reasons, reason = c.last
                facts = [f"The last decision was '{v.replace('_', ' ')}' because: {reason or ', '.join(reasons) or 'no reason recorded'}."]
                if reasons and v in ("retake", "no_damage", "mismatch"):
                    facts.append("Tips: " + "; ".join(reply.TIPS.get(r, "") for r in reasons if r in reply.TIPS))
            else:
                facts = ["Nothing has been decided on a claim in this chat yet."]
        else:  # chitchat
            facts = ["The customer is greeting or thanking."]
        ctx["facts"] = facts
        ctx["what"] = "info"
        return "info"

    def _to_person(self, step, skip_rest, ctx):
        """The customer asked for a person: hand over straight away."""
        c = self.claim
        yield step("claim", "skipped", "Customer asked for a person")
        yield from skip_rest("claim", "Not needed: handed to a person")
        ctx["reason"] = "the customer asked for a person"
        if c and c.final and c.final[0] == "accept":
            ctx["what"] = "human_after_accept"
        elif c and c.final and c.final[0] == "not_eligible":
            ctx["extra_note"] = "A colleague will look at the earlier 'not covered' decision too."
        return "human" if not (c and c.final and c.final[0] == "human") else "closed"

    # ------------------------------------------------------------------ claim, policy, eligibility, photo
    def _open(self, order_id):
        """Make order_id the current claim (an existing one, or a new one)."""
        if order_id not in self.claims:
            self.claims[order_id] = Claim(claim.ORDERS[order_id])
        self.claim = self.claims[order_id]
        self.queued = [q for q in self.queued if q[0] != order_id]
        return self.claim

    def _other_claim_named(self, text):
        """An order of this customer, other than the current claim, named by product word (one match only)."""
        prods = claim.products_in(text)
        cur = self.claim and self.claim.order["order"]
        hits = [o for o in claim.CUSTOMER["orders"] if claim.ORDERS[o]["product"] in prods and o != cur]
        opened = [o for o in hits if o in self.claims]
        if len(opened) == 1:
            return opened[0]
        return hits[0] if len(hits) == 1 else None

    def _claim_flow(self, text, img, step, skip_rest, calls, ctx):
        c = self.claim
        mine = claim.CUSTOMER["orders"]
        new_problem = False
        # 3. claim: which order, and is there problem text
        if text:
            yield step("claim", "running", None, "gptoss20b")
            d, ms, tok = claim.extract(text)
            calls.append(("gptoss20b", ms, tok))
            named = [n for n in re.findall(r"\b\d{4,6}\b", text)]
            if d.get("order") and d["order"] not in named:
                named.insert(0, d["order"])
            own = [n for n in dict.fromkeys(named) if n in mine]
            foreign = [n for n in dict.fromkeys(named) if n not in mine]
            detail = {"problem": d.get("problem"), "extracted_order": d.get("order")}
            how = ""
            other = None if own else self._other_claim_named(text)
            if own:
                if not c or c.order["order"] != own[0]:
                    new = own[0] not in self.claims
                    c = self._open(own[0])
                    how = (" (new claim)" if new else " (back to this claim)") if len(self.claims) > 1 else ""
                for extra in own[1:]:
                    if extra not in [q[0] for q in self.queued] and extra not in self.claims:
                        self.queued.append((extra, text))
                if own[1:]:
                    ctx["extra_note"] = (f"The customer also mentioned order {', '.join(own[1:])}: say you will look at "
                                         f"that one right after this one.")
            elif foreign:
                ctx["not_found"] = foreign[0]
                ctx["what"] = "not_found"
            elif other and (not c or other != c.order["order"]):
                new = other not in self.claims
                c = self._open(other)
                how = " (new claim, named by product)" if new else " (back to this claim)"
            elif c and c.final and self.queued:
                # the earlier message named another order: it is next, with that message as its first description
                nxt, seed = self.queued[0]
                c = self._open(nxt)
                how = " (next order from the earlier message)"
                if seed:
                    c.complaint.append((None, seed))
                    new_problem = True
            # no own order number: D3 matches the text against the customer's orders
            if not own and not how and (not c or c.final or (d.get("problem") and not foreign)):
                self.unmatched.append(text)
                chosen, cands, mdetail, mms, mtok = claim.match_order(" ".join(self.unmatched[-3:]) if not c else text)
                calls.append(("d3", mms, mtok))
                mdetail["ms"] = mms
                detail["order_match"] = mdetail
                ms += mms
                p = dict(cands).get(chosen, 0) if chosen else 0
                if chosen and foreign and not c:
                    # they typed a number that is not theirs: offer a match from their description, never assume it
                    if d.get("problem"):
                        ctx["options"] = f"{claim.ORDERS[chosen]['item']} (order {chosen})"
                elif chosen and (not c or (chosen != c.order["order"] and p >= MATCH_SWITCH)):
                    c = self._open(chosen)
                    how = f" (matched by D3, {p:.2f})"
                elif not c and len(cands) == 2 and cands[0][1] + cands[1][1] >= 0.6:
                    ctx["options"] = " or ".join(f"{claim.ORDERS[k]['item']} (order {k})" for k, _ in cands)
            if c and not c.final and (d.get("problem") or c.stage in ("need_problem", "clarify")) \
                    and not (own and not d.get("problem") and len(text.split()) <= 3):
                asked = self.history[-2]["content"] if c.stage == "clarify" and len(self.history) > 1 else None
                c.complaint.append((asked, text))
                new_problem = True
            if c:
                self.unmatched = []
            detail.update(order=c and c.order["order"], product=c and c.order["product"])
            summary = (f"Order {c.order['order']}: {c.order['item']}{how}" if c else
                       f"Order {ctx['not_found']} is not on this account" if ctx.get("not_found") else
                       "Which order? " + ctx["options"] if ctx.get("options") else "No order yet")
            yield step("claim", "done", summary, "gptoss20b + vllm-sr/d3-mini" if "order_match" in detail else "gptoss20b",
                       ms, detail)
        else:
            if c and c.final and self.queued:
                nxt, seed = self.queued[0]
                c = self._open(nxt)
                if seed:
                    c.complaint.append((None, seed))
                    new_problem = True
            yield step("claim", "skipped", f"From an earlier turn: order {c.order['order']}" if c else "No text")

        if not c:
            if img:
                self.pending_image = img
                ctx["photo_note"] = "Say their photo is kept and will be checked once we know the order; do not describe it."
            yield from skip_rest("claim", "Waiting for the order")
            return "need_order"
        if ctx.get("what") == "not_found":
            ctx.pop("what")   # a foreign number while a claim is open: mention it, continue with the open claim
            ctx["extra_note"] = (ctx.get("extra_note", "") + f" Also say order {ctx.pop('not_found')} is not on their "
                                 "account.").strip()
        if img is None and self.pending_image:
            img, self.pending_image = self.pending_image, None   # the photo sent before the order was known
        if c.final:
            yield from skip_rest("claim", f"Decided in an earlier turn: {c.final[0].replace('_', ' ')}")
            ctx["photo_sent"] = img is not None
            return "closed"

        # 4. policy (hard rules, once per claim)
        if c.policy_ok is None:
            ok, outcome, rules = claim.hard_rules(c.order)
            c.policy_ok = ok
            yield step("policy", "done" if ok else "blocked",
                       "All rules passed" if ok else next(r["label"] for r in rules if not r["ok"]), None, 0,
                       {"rules": rules})
            if not ok:
                pol = claim.POLICIES[c.order["product"]]
                ctx["reason"] = next(r["label"] for r in rules if not r["ok"])
                ctx["policy_line"] = (f"Returns within {pol['window_days']} days of delivery" if outcome == "not_eligible"
                                      else "Orders over the review limit are checked by a colleague")
                ctx["policy_ref"] = "window" if outcome == "not_eligible" else "review_limit"
                yield from skip_rest("policy", "Not needed: policy rule failed")
                return outcome
        else:
            yield step("policy", "skipped", "Passed in an earlier turn")

        # 5. eligibility (D3, text): re-run whenever the customer adds to the problem, until the claim is closed
        if new_problem:
            yield step("eligibility", "running", None, "vllm-sr/d3-mini")
            a, ms, tok = eligibility.ask(c.order["product"], c.complaint)
            calls.append(("d3", ms, tok))
            outcome, reason = eligibility.decide(a)
            if outcome == "clarify":
                c.clarified += 1
                if c.clarified > MAX_CLARIFY:
                    outcome, reason = "human", f"{MAX_CLARIFY} clarifying questions did not settle the claim"
            was = c.stage
            yield step("eligibility", "blocked" if outcome == "not_eligible" else "done",
                       f"{outcome.replace('_', ' ').capitalize()}: {reason}"
                       + (" (re-checked after the customer's new message)" if was == "need_photo" else ""),
                       "vllm-sr/d3-mini", ms, eligibility.detail(a))
            ctx["reason"] = reason
            if outcome == "not_eligible":
                pol = claim.POLICIES[c.order["product"]]
                if a["issue"]["choice"] == "no_damage":
                    ctx["policy_line"] = FAQ["other_returns"]
                else:
                    ctx["policy_line"] = "; ".join(pol["not_covered"])
                    ctx["policy_ref"] = "not_covered"
                yield from skip_rest("eligibility", "Not needed: claim not eligible")
                return "not_eligible"
            if outcome == "human":
                yield from skip_rest("eligibility", "Not needed: goes to a person")
                return "human"
            c.stage = "clarify" if outcome == "clarify" else "need_photo"
        elif c.stage in ("need_problem", "clarify"):
            yield step("eligibility", "skipped", "Waiting for " + ("an answer" if c.stage == "clarify" else
                                                                   "a description of the problem"))
        else:
            yield step("eligibility", "skipped", "Eligible in an earlier turn")

        # 6. photo (D3, image)
        if img is not None:
            v, reasons = yield from self._photo(img, step, calls, ctx, early=c.stage != "need_photo")
            kind = ctx.get("photo_kind")
            target = next((o for o, cl in self.claims.items() if cl is not c and not cl.final
                           and cl.order["product"] == kind), None)
            if v == "mismatch" and target:
                # the photo shows the product of another open claim in this chat: judge it for that claim
                c = self._open(target)
                ctx["extra_note"] = f"The photo was for their {c.order['item']} claim (order {target})."
                if c.policy_ok is None or c.stage in ("need_problem", "clarify"):
                    c.photo = None
                    yield from self._photo(img, step, calls, ctx, early=True)
                    return c.stage if c.stage != "need_photo" else "need_photo"
                v, reasons = yield from self._photo(img, step, calls, ctx)
            if c.stage != "need_photo":
                # the claim is not clear yet: keep a usable result, point out a wrong or unusable photo now
                c.photo = (v, reasons) if v in ("accept", "no_damage", "human") else None
                ctx["photo_note"] = (
                    f"The photo they sent does not show the {c.order['item']} ({', '.join(reasons)}); mention it briefly."
                    if v == "mismatch" else
                    f"The photo they sent cannot be used ({', '.join(reasons)}); mention that a new one will be needed."
                    if v == "retake" else
                    "Their photo was received and will be used once the problem is clear; do not describe it.")
                ctx["reasons"] = []
                return c.stage
            c.photo = None
        elif c.stage == "need_photo" and c.photo:
            v, reasons = c.photo
            c.photo = None
            ctx["reasons"] = reasons
            yield step("image_check", "skipped", "Checked in an earlier turn: " +
                       ("damage clearly visible" if v == "accept" else ", ".join(reasons).lower()))
        else:
            yield step("image_check", "skipped", "Waiting for a photo" if c.stage == "need_photo" else
                       "Not needed yet: the problem is not clear")
            return c.stage
        if v != "accept" and c.photos >= MAX_PHOTOS:
            ctx["reason"] = f"{c.photos} photos could not confirm the damage"
            return "human"
        return v

    def _photo(self, img, step, calls, ctx, early=False):
        """D3 photo check of one decoded upload; yields the image_check step, returns (verdict, reasons)."""
        c = self.claim
        yield step("image_check", "running", None, "vllm-sr/d3-mini")
        v, reasons, det, ms, tok = photo.check(c.order["product"], img)
        c.photos += 1
        c.photo_checked = True
        c.photo_log.append((v, list(reasons)))
        calls.append(("d3", ms, tok))
        det["image"] = "upload"
        ctx["photo_kind"] = next((q["choice"] for q in det["questions"] if q["key"] == "kind"), None)
        note = " (text asking for approval ignored)" if det["note_flagged"] else ""
        when = "Checked before the claim was clear: " if early else ""
        yield step("image_check", "done" if v == "accept" else "blocked",
                   when + ("Damage clearly visible" if v == "accept" else ", ".join(reasons)) + note,
                   "vllm-sr/d3-mini", ms, det)
        ctx["reasons"] = reasons
        return v, reasons


def policy_ref(verdict, ctx, c=None):
    """Which part of the product policy the reply rests on, for the UI's policy popover."""
    if ctx.get("policy_ref"):
        return ctx["policy_ref"]
    if verdict == "closed" and c and c.final:
        return {"accept": "covered", "not_eligible": "not_covered"}.get(c.final[0])
    return {"accept": "covered", "need_photo": "photo", "retake": "photo", "mismatch": "photo", "no_damage": "photo",
            "clarify": "covered"}.get(verdict)


def model_name(m):
    return {"gptoss20b": "gpt-oss-20B", "gptoss120b": "gpt-oss-120B"}.get(m, m)


END = {
    "approved": ("Return approved", "Your free return label will be emailed within 10 minutes. The replacement or refund "
                 "follows once the courier collects the item."),
    "approved_and_handed_over": ("Return approved", "Your return stays approved, and a colleague will also email you "
                                 "within one working day."),
    "handed_over": ("Passed to a colleague", "A colleague will review your case and reply by email within one working "
                    "day."),
    "not_covered": ("Not covered", "This return is not covered by the policy for this item. If you think this is wrong, "
                    "a colleague can review the decision."),
    "blocked": ("Closed after repeated bypass attempts", "This chat was closed after repeated attempts to bypass our "
                "review. Start a new chat if you need help with an order."),
    "off_topic": ("Closed: not about orders", "This chat is only for orders, returns and refunds, so it has been "
                  "closed. Start a new chat if you need help with an order."),
}


def ending(reason, c):
    title, text = END[reason]
    return {"reason": reason, "title": title, "text": text,
            "order": c and c.order["order"] if reason not in ("blocked", "off_topic") else None,
            "actions": ["review", "new_chat"] if reason == "not_covered" else ["new_chat"]}
