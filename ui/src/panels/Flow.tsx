/* Right panel: how this turn was decided. One node per step in flow order; nodes move waiting -> running -> done,
 * skipped, blocked or failed as `step` events arrive. D3 nodes expand to their questions. Clicking an earlier user
 * message shows that turn's stored steps here instead of the live turn. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, ChevronDown, History, TriangleAlert, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Badge } from "../components/arc/badge/badge";
import { Button } from "../components/arc/button/button";
import { ScrollArea } from "../components/arc/scroll-area/scroll-area";
import { motionTokens } from "../components/arc/lib/motion-tokens";
import {
  NODES, type ChoiceQ, type ClaimDetail, type D3Detail, type DecisionDetail, type GuardrailDetail, type NodeKey, type OrderMatch, type PolicyDetail,
  type ReplyDetail, type RouterDetail, type StepEvent,
} from "../api";
import { D3_NODES, NODE_HINT, NODE_LABEL, fmtMs, modelName, titleCase, verdictMeta } from "../format";
import type { Flow as FlowT, Replay } from "../useDemo";
import { D3Legend, D3Questions } from "./D3Questions";
import styles from "./Flow.module.css";

/** Live turn ids are "<session uuid>-t2"; show the "t2" part. */
const shortTurn = (t: string) => t.match(/-(t\d+)$/)?.[1] ?? t;

const STATUS_TEXT = { waiting: "Waiting", running: "Running", done: "Done", skipped: "Not needed", blocked: "Blocked", failed: "Failed" } as const;

/** Vela's pick: the chosen model as one line and the Vela decision name small. No pick (guard decision): the summary says it. */
function RouterView({ d }: { d: RouterDetail }) {
  if (!d.chosen) return null;
  const hard = d.chosen === "gptoss120b";
  return (
    <div className={styles.router}>
      <p className={styles.routerPick}>{modelName(d.chosen)} <span className={styles.routerWhy}>({hard ? "hard turn" : "normal turn"})</span></p>
      {d.router_decision && <p className={styles.small}>Vela decision <code className={styles.routerName}>{d.router_decision}</code></p>}
    </div>
  );
}

/** D3 matched a message without an order number to one of the customer's orders. */
function OrderMatchView({ m }: { m: OrderMatch }) {
  const q = m.questions.find((x) => x.type === "choice") as ChoiceQ | undefined;
  if (!q) return null;
  const top = q.probs[q.choice] ?? 0;
  const th = m.threshold;
  const matched = q.choice !== "unclear" && (th == null || top >= th);
  return (
    <div className={styles.match}>
      <div className={styles.matchHead}>
        <span className={styles.matchName}>Order match<span className={styles.d3}>D3</span></span>
        <span className={styles.matchResult} data-ok={matched}>
          {matched ? `Matched at ${top.toFixed(2)}` : `Best ${top.toFixed(2)}, not matched`}{th != null ? `, needs ${th}` : ""}
        </span>
      </div>
      <D3Questions detail={m} threshold={th} />
    </div>
  );
}

function ClaimView({ d }: { d: ClaimDetail }) {
  const rows = ([["Order", d.order], ["Product", d.product], ["Issue", d.issue], ["Part", d.part]] as const).filter(([, v]) => v);
  const match = d.order_match?.questions?.length ? d.order_match : null;
  if (!rows.length && !match) return null;
  return (
    <>
      {rows.length > 0 && (
        <dl className={styles.kv}>
          {rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{titleCase(String(v)).replace(/_/g, " ")}</dd></div>)}
        </dl>
      )}
      {match && <OrderMatchView m={match} />}
    </>
  );
}

function PolicyView({ d }: { d: PolicyDetail }) {
  return (
    <ul className={styles.rules}>
      {d.rules.map((r) => (
        <li key={r.label} data-ok={r.ok}>
          {r.ok ? <Check size={14} strokeWidth={2.2} aria-label="Pass" /> : <X size={14} strokeWidth={2.2} aria-label="Fail" />}
          <span>{r.label}</span>
        </li>
      ))}
    </ul>
  );
}

function DecisionView({ d }: { d: DecisionDetail }) {
  const v = verdictMeta(d.verdict);
  const Icon = v?.icon;
  return (
    <div className={styles.decision}>
      {v && Icon && <Badge tone={v.tone} icon={<Icon size={13} strokeWidth={2} aria-hidden="true" />}>{v.label}</Badge>}
      {!!d.reasons?.length && <ul className={styles.reasons}>{d.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
      {d.rule && <code className={styles.rule}>{d.rule}</code>}
    </div>
  );
}

/** The D3 scope check on the guardrail: what the message is (choice `scope`), commands or code, real product problem,
 * then what the customer wants (choice `intent`, top 3 options so the node stays short). */
function ScopeView({ m }: { m: D3Detail }) {
  const q = m.questions.find((x) => x.key === "scope" && x.type === "choice") as ChoiceQ | undefined;
  const intent = m.questions.find((x) => x.key === "intent" && x.type === "choice") as ChoiceQ | undefined;
  const name = q ? q.labels?.[q.choice] ?? titleCase(q.choice) : "";
  const tone = !q ? undefined : q.choice === "returns" ? "ok" : q.choice === "off_topic" ? "info" : "bad";
  const rest: D3Detail = { ...m, questions: m.questions.filter((x) => x !== intent) };
  return (
    <div className={styles.match}>
      <div className={styles.matchHead}>
        <span className={styles.matchName}>Scope check<span className={styles.d3}>D3</span></span>
        {q && <span className={styles.matchResult} data-tone={tone}>{name} at {(q.probs[q.choice] ?? 0).toFixed(2)}</span>}
      </div>
      <D3Questions detail={rest} compact />
      {intent && (
        <>
          <div className={styles.subHead}>
            <span>{intent.label || "Intent"}</span>
            <span className={styles.subResult}>{intent.labels?.[intent.choice] ?? titleCase(intent.choice)} at {(intent.probs[intent.choice] ?? 0).toFixed(2)}</span>
          </div>
          <D3Questions detail={{ questions: [intent] }} compact top={3} />
        </>
      )}
    </div>
  );
}

function GuardView({ d }: { d: GuardrailDetail }) {
  const scope = d.scope_check?.questions?.length ? d.scope_check : null;
  return (
    <>
      <p className={styles.small}>
        {d.jailbreak != null && <>Vela jailbreak or injection <span className={styles.num}>{d.jailbreak.toFixed(2)}</span></>}
        {d.pii != null && <>{d.jailbreak != null ? " · " : ""}PII {d.pii ? "found" : "none"}</>}
      </p>
      {scope && <ScopeView m={scope} />}
    </>
  );
}

/** Tokens, then the D3 reply check; a replaced reply shows what was caught. */
function ReplyView({ step, d }: { step: StepEvent; d: ReplyDetail }) {
  const c = d.reply_check;
  const tokens = !step.summary && d.tokens != null ? <p className={styles.small}>{d.tokens} tokens</p> : null;
  if (!c) return tokens;
  return (
    <>
      {tokens}
      <p className={styles.small}>
        Reply check (D3): claims approval <span className={styles.num}>{c.claims_approval.toFixed(2)}</span>, needs &lt; {c.threshold.toFixed(2)}
      </p>
      {c.replaced && (
        <div className={styles.caught} role="note">
          <p className={styles.caughtHead}><TriangleAlert size={14} strokeWidth={2} aria-hidden="true" />Replaced: the reply claimed an approval</p>
          {c.original && (
            <details className={styles.original}>
              <summary>Original reply</summary>
              <del>{c.original}</del>
            </details>
          )}
        </div>
      )}
    </>
  );
}

function Detail({ node, step, open }: { node: NodeKey; step: StepEvent; open: boolean }) {
  const d = step.detail as unknown;
  if (!d || (step.status !== "done" && step.status !== "blocked" && step.status !== "failed")) return null;
  switch (node) {
    case "guardrail": return <GuardView d={d as GuardrailDetail} />;
    case "router": return <RouterView d={d as RouterDetail} />;
    case "claim": return <ClaimView d={d as ClaimDetail} />;
    case "policy": return (d as PolicyDetail).rules ? <PolicyView d={d as PolicyDetail} /> : null;
    case "decision": return <DecisionView d={d as DecisionDetail} />;
    case "reply": return <ReplyView step={step} d={d as ReplyDetail} />;
    case "eligibility": case "image_check":
      return open && (d as D3Detail).questions ? <><D3Questions detail={d as D3Detail} /><D3Legend /></> : null;
  }
}

function Node({ node, step, last }: { node: NodeKey; step: StepEvent | undefined; last: boolean }) {
  const reduce = useReducedMotion();
  const status = step?.status ?? "waiting";
  const isD3 = D3_NODES.includes(node);
  const usesD3 = isD3 || (status !== "skipped" && (
    (node === "claim" && !!(step?.detail as ClaimDetail | undefined)?.order_match)
    || (node === "guardrail" && !!(step?.detail as GuardrailDetail | undefined)?.scope_check)
    || (node === "reply" && !!(step?.detail as ReplyDetail | undefined)?.reply_check)));
  const replaced = node === "reply" && !!(step?.detail as ReplyDetail | undefined)?.reply_check?.replaced;
  const hasQuestions = isD3 && !!(step?.detail as D3Detail | undefined)?.questions?.length && status !== "skipped";
  const [open, setOpen] = useState(true);
  const verdictShown = node === "decision" && !!(step?.detail as DecisionDetail | undefined)?.verdict && step?.status === "done";
  // A replaced reply says so in its warning block (with the original), so the summary line is not repeated.
  // A router that picked a model shows the pick as its own line, so the summary ("Normal turn, gpt-oss-20B") is not repeated.
  const routerPick = node === "router" && status === "done" && !!(step?.detail as RouterDetail | undefined)?.chosen;
  const summary = verdictShown || replaced || routerPick ? "" : step?.summary || (status === "skipped" ? "Not needed" : status === "waiting" || !step ? NODE_HINT[node] : "");
  const hasDetail = !!step?.detail && (step.status === "done" || step.status === "blocked" || step.status === "failed") && (!isD3 || (hasQuestions && open));
  const detail: ReactNode = hasDetail ? <Detail node={node} step={step!} open={open} /> : null;
  const model = step?.model && status !== "skipped" ? modelName(step.model) : "";
  const ref = useRef<HTMLLIElement>(null);
  // Keep the active step in view on a big screen; D3 nodes scroll their questions in when they land.
  useEffect(() => {
    if (status !== "running" && !(status === "done" && hasQuestions)) return;
    ref.current?.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
  }, [status, hasQuestions, reduce]);
  const skipped = status === "skipped";
  return (
    <li ref={ref} className={styles.node} data-status={status} data-last={last || undefined} data-replaced={replaced || undefined}>
      <span className={styles.rail} aria-hidden="true"><span className={styles.dot} /></span>
      <div className={styles.body}>
        <div className={styles.headRow}>
          <h3 className={styles.name}>{NODE_LABEL[node]}{usesD3 && <span className={styles.d3}>D3</span>}</h3>
          {model && <span className={styles.model} data-big={(node === "reply" && /gptoss120b/.test(step?.model ?? "")) || undefined}>{model}</span>}
          {skipped && <span className={styles.inlineSkip}><span className={styles.skipTag}>Skipped</span>{summary}</span>}
          <span className={styles.ms}>{step?.ms != null && !skipped ? fmtMs(step.ms) : ""}</span>
          <span className={styles.srOnly}>{STATUS_TEXT[status]}</span>
        </div>
        {summary !== "" && !skipped && <p className={styles.summary}>
          {summary}
        </p>}
        {hasQuestions && (
          <button type="button" className={styles.expand} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            <motion.span animate={{ rotate: open ? 0 : -90 }} transition={reduce ? { duration: 0 } : motionTokens.spring.snappy} className={styles.expandIcon}>
              <ChevronDown size={14} strokeWidth={1.9} aria-hidden="true" />
            </motion.span>
            {open ? "Hide questions" : `Show ${(step!.detail as D3Detail).questions.length} questions`}
          </button>
        )}
        <AnimatePresence initial={false}>
          {detail && (
            <motion.div key="detail" className={styles.detailWrap}
              initial={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }} animate={reduce ? { opacity: 1 } : { height: "auto", opacity: 1 }}
              exit={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }} transition={reduce ? { duration: 0.12 } : motionTokens.spring.smooth}>
              <div className={styles.detail}>{detail}</div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </li>
  );
}

/** D3 time this turn (docs/UI.md "D3 time in the flow header"): steps run by D3 alone (model contains "d3", e.g. eligibility,
 * photo check; for the guardrail only its D3 scope check, `scope_check.ms`), the
 * order match inside the claim step when the server times it (`order_match.ms`), and the reply check (`reply_check.ms`).
 * The claim and reply steps themselves are gpt-oss time and are not counted. */
function d3Ms(steps: StepEvent[]): number {
  let ms = 0;
  for (const s of steps) {
    if (s.status === "skipped" || s.status === "waiting") continue;
    if (s.node === "claim") ms += (s.detail as ClaimDetail | undefined)?.order_match?.ms ?? 0;
    else if (s.node === "reply") ms += (s.detail as ReplyDetail | undefined)?.reply_check?.ms ?? 0;
    else if (s.node === "guardrail" && (s.detail as GuardrailDetail | undefined)?.scope_check?.ms != null)
      ms += (s.detail as GuardrailDetail).scope_check!.ms ?? 0;  // only D3's scope check, not the Vela screen
    else if (/d3/i.test(s.model ?? "") || D3_NODES.includes(s.node)) ms += s.ms ?? 0;
  }
  return ms;
}

export function Flow({ flow, replay, hasTurn, onBack }: { flow: FlowT; replay: Replay | null; hasTurn: boolean; onBack: () => void }) {
  const shown: FlowT = replay ? replay.steps ?? {} : flow;
  const steps = Object.values(shown).filter(Boolean) as StepEvent[];
  const total = steps.filter((s) => s.status !== "skipped" && s.ms != null).reduce((a, s) => a + (s.ms ?? 0), 0);
  const d3 = d3Ms(steps);
  const running = steps.some((s) => s.status === "running");
  return (
    <section className={styles.root} aria-label="How this was decided">
      <div className={styles.head}>
        <div className={styles.titleRow}>
          <h2>How this was decided</h2>
          {replay && <Button variant="ghost" size="sm" onClick={onBack}>Back to latest</Button>}
        </div>
        <p className={styles.sub}>
          {replay ? <><History size={13} strokeWidth={1.9} aria-hidden="true" /> Earlier turn {shortTurn(replay.turn)}{replay.error ? `: ${replay.error}` : ""}</>
            : !hasTurn && !steps.length ? "Send a message to see each step"
            : running ? "Running" : steps.length ? total > 0 ? `Turn total ${fmtMs(total)}${d3 ? `, D3 ${fmtMs(d3)}` : ""}` : "No model time this turn" : ""}
        </p>
      </div>
      <ScrollArea className={styles.scroll} label="Flow steps">
        <ol className={styles.list}>
          {NODES.map((n, i) => <Node key={replay ? `${replay.turn}-${n}` : n} node={n} step={shown[n]} last={i === NODES.length - 1} />)}
        </ol>
      </ScrollArea>
    </section>
  );
}
