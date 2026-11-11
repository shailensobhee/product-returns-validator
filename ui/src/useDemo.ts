/* Chat state, the live flow, past-turn replay, and scenario playback (one scenario or Play all). */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  NODES, errText, getCustomer, getStats, getTurn, newSession, streamChat,
  type Customer, type Ended, type Group, type NodeKey, type OrderInfo, type PolicyRef, type Scenario, type Stats, type StepEvent, type Verdict,
} from "./api";

export interface Msg {
  id: string;
  role: "user" | "assistant";
  text?: string;
  image?: string;
  verdict?: Verdict;
  /** Order known at this reply (message.order) and the policy part it rests on (message.policy_ref). */
  order?: OrderInfo | null;
  policyRef?: PolicyRef | null;
  /** Turn id from `done`, for replaying its flow. */
  turn?: string;
  /** User text still being typed by the scenario player. */
  typing?: boolean;
  /** Assistant reply still streaming. */
  streaming?: boolean;
  error?: string;
  /** The customer greeting that opens every chat (client side, no turn). */
  greeting?: boolean;
  /** A user turn sent by a button, not typed (the review request): shown as a small system-style bubble. */
  system?: boolean;
  /** message.ended of this reply (the chat ended with it). */
  ended?: Ended | null;
}

export type Flow = Partial<Record<NodeKey, StepEvent>>;
export interface Replay { turn: string; msgId: string; steps: Flow | null; error?: string }
export interface Playing { scenario: Scenario; group: string; turn: number; all: boolean }
/** Actual verdict of every turn of a finished scenario run. */
export type Outcomes = Record<string, (Verdict | undefined)[]>;
/** Per scenario: did a reply step of the last run use gpt-oss-120B (Vela decides live, so this can differ from `uses_27b`). */
export type Used27 = Record<string, boolean>;
/** Matched only when every turn's verdict equals that turn's `expect`. */
export const matches = (sc: Scenario, got: (Verdict | undefined)[]) => sc.turns.every((t, i) => !t.expect || got[i] === t.expect);

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let seq = 0;
const mid = () => `m${++seq}`;

export function toFlow(steps: StepEvent[]): Flow {
  const f: Flow = {};
  for (const s of steps) f[s.node] = { ...f[s.node], ...s };
  return f;
}

/** On an `error` event: waiting and running nodes become failed; the running one gets the error text as summary, the
 * waiting ones "Not run: the turn failed". */
export function failOpen(f: Flow, message: string): Flow {
  const out: Flow = { ...f };
  for (const n of NODES) {
    const st = out[n];
    const status = st?.status ?? "waiting";
    if (status !== "waiting" && status !== "running") continue;
    out[n] = { ...(st ?? { type: "step", node: n }), status: "failed", summary: status === "running" ? message : "Not run: the turn failed" } as StepEvent;
  }
  return out;
}

export function useDemo() {
  const [session, setSession] = useState(newSession);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [flow, setFlow] = useState<Flow>({});
  const [liveTurn, setLiveTurn] = useState<string | null>(null);
  const [replay, setReplay] = useState<Replay | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState<Playing | null>(null);
  const [outcomes, setOutcomes] = useState<Outcomes>({});
  const [used27, setUsed27] = useState<Used27>({});
  /** The chat is over (message.ended); the composer is replaced by the end banner. */
  const [ended, setEnded] = useState<Ended | null>(null);
  const [order, setOrder] = useState<OrderInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [customer, setCustomer] = useState<Customer | null>(null);
  /** The current chat was started by a sample (no order cards there). */
  const [sampleChat, setSampleChat] = useState(false);

  const sessionRef = useRef(session);
  const abortRef = useRef<AbortController | null>(null);
  const runRef = useRef(0);
  const greetingRef = useRef<string | null>(null);
  const used27Ref = useRef(false);
  const opening = (): Msg[] => (greetingRef.current ? [{ id: mid(), role: "assistant", text: greetingRef.current, greeting: true }] : []);

  // Every chat opens with the customer greeting; the first chat gets it as soon as GET /api/customer answers.
  useEffect(() => {
    getCustomer().then((c) => {
      setCustomer(c);
      if (!c?.greeting) return;
      greetingRef.current = c.greeting;
      setMessages((ms) => (ms.some((m) => m.greeting || m.role === "user") ? ms : [...opening(), ...ms]));
    }, () => { /* no customer: the chat falls back to its empty state */ });
  }, []);

  /** Stats are per chat session: only accept an object for the current session (a late event from an old chat is dropped). */
  const takeStats = useCallback((st: Stats, forSession: string) => {
    if (forSession !== sessionRef.current) return;
    if (st.session && st.session !== sessionRef.current) return;
    // Within one chat turns only grow, so a slow GET answer cannot replace a newer `stats` event.
    setStats((prev) => (prev && (prev.turns ?? 0) > (st.turns ?? 0) ? prev : st));
  }, []);

  useEffect(() => {
    const sid = session;
    getStats(sid).then((st) => takeStats(st, sid), () => { /* top bar shows dashes */ });
  }, [session, takeStats]);

  const patch = (id: string, p: Partial<Msg> | ((m: Msg) => Partial<Msg>)) =>
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...(typeof p === "function" ? p(m) : p) } : m)));

  /** Send one turn. `userId` reuses a bubble the player has already typed. Resolves with the verdict of the reply. */
  const send = useCallback(async (input: { text?: string; image?: string; action?: "review" }, userId?: string): Promise<Verdict | undefined> => {
    const uid = userId ?? mid();
    const aid = mid();
    if (!userId) setMessages((ms) => [...ms, input.action === "review"
      ? { id: uid, role: "user", text: "Asked a colleague to review the decision", system: true }
      : { id: uid, role: "user", text: input.text, image: input.image }]);
    else patch(uid, { typing: false, image: input.image });
    setMessages((ms) => [...ms, { id: aid, role: "assistant", text: "", streaming: true }]);
    setReplay(null);
    setFlow({});
    setLiveTurn(null);
    setBusy(true);
    setError(null);
    const ac = new AbortController();
    abortRef.current = ac;
    let verdict: Verdict | undefined;
    let gotMessage = false;
    let streamError: string | null = null;
    const sid = sessionRef.current;
    try {
      await streamChat({ session: sid, ...input }, (e) => {
        switch (e.type) {
          case "step":
            if (e.node === "reply" && e.status !== "skipped" && /gptoss120b/.test(`${e.model ?? ""} ${(e.detail as { model?: string } | null)?.model ?? ""}`)) used27Ref.current = true;
            setFlow((f) => ({ ...f, [e.node]: { ...f[e.node], ...e } }));
            break;
          case "token": patch(aid, (m) => ({ text: (m.text ?? "") + e.text })); break;
          case "message":
            gotMessage = true;
            verdict = e.verdict;
            patch(aid, { text: e.text, verdict: e.verdict, order: e.order ?? null, policyRef: e.policy_ref ?? null, ended: e.ended ?? null, streaming: false });
            if (sid === sessionRef.current) setEnded(e.ended ?? null);
            if (e.order) setOrder(e.order);
            break;
          case "stats": { const { type: _t, ...s } = e; void _t; takeStats(s, sid); break; }
          case "done": patch(uid, { turn: e.turn }); patch(aid, { turn: e.turn, streaming: false }); setLiveTurn(e.turn); break;
          case "error":
            // The turn failed: every node not finished is marked failed (the running one carries the error), the
            // "Checking" state stops, and the `message` with verdict `error` that follows renders as a normal reply.
            streamError = e.message;
            setFlow((f) => failOpen(f, e.message));
            patch(aid, { streaming: false });
            break;
        }
      }, ac.signal);
    } catch (e) {
      if ((e as Error)?.name !== "AbortError") { patch(aid, { error: errText(e), streaming: false }); setError(errText(e)); }
    } finally {
      // An error with no `message` after it: show the error text in the reply bubble so it is not left empty.
      if (streamError && !gotMessage) patch(aid, { error: streamError });
      patch(aid, { streaming: false });
      if (abortRef.current === ac) abortRef.current = null;
      setBusy(false);
    }
    return verdict;
  }, [takeStats]);

  /** New session: chat back to the greeting, empty flow and top bar (stats are per chat). */
  const reset = useCallback((fromSample = false) => {
    abortRef.current?.abort();
    const s = newSession();
    sessionRef.current = s;
    setSession(s);
    setStats(null);
    setMessages(opening());
    setSampleChat(fromSample);
    setOrder(null);
    setEnded(null);
    setFlow({});
    setLiveTurn(null);
    setReplay(null);
  }, []);

  const stop = useCallback(() => {
    runRef.current++;
    abortRef.current?.abort();
    setPlaying(null);
  }, []);

  /** Plays one scenario's turns: text is typed into a bubble first, images are sent as uploads. */
  const playOne = useCallback(async (group: string, sc: Scenario, run: number, all: boolean) => {
    reset(true);
    await sleep(350);
    const got: (Verdict | undefined)[] = [];
    used27Ref.current = false;
    for (let i = 0; i < sc.turns.length; i++) {
      if (runRef.current !== run) return undefined;
      setPlaying({ scenario: sc, group, turn: i, all });
      const t = sc.turns[i];
      if (t.text) {
        const id = mid();
        setMessages((ms) => [...ms, { id, role: "user", text: "", typing: true }]);
        const step = Math.max(8, Math.min(28, 1400 / t.text.length));
        for (let n = 1; n <= t.text.length; n += 2) {
          if (runRef.current !== run) return undefined;
          patch(id, { text: t.text.slice(0, n) });
          await sleep(step * 2);
        }
        patch(id, { text: t.text });
        await sleep(250);
        got[i] = await send({ text: t.text, image: t.image }, id);
      } else if (t.image) {
        await sleep(500);
        got[i] = await send({ image: t.image });
      }
      if (runRef.current !== run) return undefined;
      await sleep(900);
    }
    setOutcomes((o) => ({ ...o, [sc.id]: got }));
    setUsed27((u) => ({ ...u, [sc.id]: used27Ref.current }));
    return got;
  }, [reset, send]);

  const playScenario = useCallback(async (group: string, sc: Scenario) => {
    const run = ++runRef.current;
    abortRef.current?.abort();
    await playOne(group, sc, run, false);
    if (runRef.current === run) setPlaying(null);
  }, [playOne]);

  const playAll = useCallback(async (groups: Group[]) => {
    const run = ++runRef.current;
    abortRef.current?.abort();
    for (const g of groups) for (const sc of g.scenarios) {
      if (runRef.current !== run) return;
      await playOne(g.key, sc, run, true);
      if (runRef.current !== run) return;
      await sleep(1600);
    }
    if (runRef.current === run) setPlaying(null);
  }, [playOne]);

  /** Show the flow of an earlier turn on the right; the latest turn goes back to the live view. */
  const showTurn = useCallback(async (msg: Msg) => {
    if (!msg.turn) return;
    if (msg.turn === liveTurn && !replay) return;
    if (msg.turn === liveTurn) { setReplay(null); return; }
    setReplay({ turn: msg.turn, msgId: msg.id, steps: null });
    try {
      const steps = await getTurn(msg.turn);
      setReplay((r) => (r && r.turn === msg.turn ? { ...r, steps: toFlow(steps) } : r));
    } catch (e) {
      setReplay((r) => (r && r.turn === msg.turn ? { ...r, error: errText(e) } : r));
    }
  }, [liveTurn, replay]);

  return {
    session, messages, flow, liveTurn, replay, stats, busy, playing, outcomes, used27, order, error, customer, sampleChat, ended,
    send, reset: () => reset(false), stop, playScenario, playAll, showTurn, closeReplay: () => setReplay(null),
  };
}
export type Demo = ReturnType<typeof useDemo>;
