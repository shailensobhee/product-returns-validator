/* Labels, tones and icons shared by the panels. */
import type { LucideIcon } from "lucide-react";
import {
  Ban, Camera, CircleAlert, CircleCheck, CircleDot, CircleHelp, CircleSlash, EyeOff, FileX, Hash, Info, Lock, MessageCircleQuestion, MessageSquareOff, RefreshCw, Shuffle, ShieldAlert, UserRound,
} from "lucide-react";
import type { BadgeTone } from "./components/arc/badge/badge";
import type { NodeKey, Verdict } from "./api";

export interface VerdictMeta { label: string; short: string; tone: BadgeTone; icon: LucideIcon }
export const VERDICT: Record<Verdict, VerdictMeta> = {
  accept: { label: "Accepted", short: "Accept", tone: "success", icon: CircleCheck },
  retake: { label: "Retake photo", short: "Retake", tone: "warning", icon: RefreshCw },
  mismatch: { label: "Wrong product", short: "Mismatch", tone: "warning", icon: Shuffle },
  no_damage: { label: "No damage visible", short: "No damage", tone: "neutral", icon: EyeOff },
  not_eligible: { label: "Not eligible", short: "Not eligible", tone: "danger", icon: Ban },
  human: { label: "Human review", short: "Human", tone: "info", icon: UserRound },
  clarify: { label: "Clarifying question", short: "Clarify", tone: "info", icon: CircleHelp },
  blocked: { label: "Blocked by guardrail", short: "Blocked", tone: "danger", icon: ShieldAlert },
  need_photo: { label: "Photo requested", short: "Need photo", tone: "neutral", icon: Camera },
  need_order: { label: "Order number requested", short: "Need order", tone: "neutral", icon: Hash },
  need_problem: { label: "Problem requested", short: "Need problem", tone: "neutral", icon: MessageCircleQuestion },
  closed: { label: "Already decided", short: "Closed", tone: "neutral", icon: Lock },
  off_topic: { label: "Off-topic", short: "Off-topic", tone: "info", icon: CircleSlash },
  info: { label: "Answered", short: "Answered", tone: "info", icon: Info },
  bad_file: { label: "Not a photo file", short: "Bad file", tone: "warning", icon: FileX },
  ended: { label: "Chat ended", short: "Ended", tone: "neutral", icon: MessageSquareOff },
  error: { label: "Error", short: "Error", tone: "danger", icon: CircleAlert },
};
/** Known verdicts get their entry; an unknown one gets a neutral chip with its own name, so a new server verdict never breaks the page. */
export const verdictMeta = (v: string | undefined | null): VerdictMeta | null =>
  !v ? null : Object.prototype.hasOwnProperty.call(VERDICT, v) ? VERDICT[v as Verdict]
    : { label: titleCase(String(v)), short: titleCase(String(v)), tone: "neutral", icon: CircleDot };

export const NODE_LABEL: Record<NodeKey, string> = {
  guardrail: "Guardrail", router: "Router", claim: "Claim", policy: "Policy rules",
  eligibility: "Eligibility", image_check: "Photo check", decision: "Decision", reply: "Reply",
};
/** What each node is, shown under the name while it waits. */
export const NODE_HINT: Record<NodeKey, string> = {
  guardrail: "Vela jailbreak screen and D3 scope check on the message",
  router: "Vela picks gpt-oss-20B or gpt-oss-120B for this turn",
  claim: "Order, item, issue and part as JSON",
  policy: "Return window, delivery and price limit, in code",
  eligibility: "D3 text decision over the policy and complaint",
  image_check: "D3 image decision: quality, product, damage",
  decision: "Rules in code turn D3 answers into a verdict",
  reply: "The LLM words the verdict; it cannot change it",
};
export const D3_NODES: NodeKey[] = ["eligibility", "image_check"];

const MODEL_NAME: Record<string, string> = {
  gptoss20b: "gpt-oss-20B", gptoss120b: "gpt-oss-120B", "vllm-sr/d3-mini": "D3 mini", "vllm-sr/d3-flash": "D3 flash", "vllm-sr/d3-lite": "D3 lite",
  "vllm-sr/d3-edge": "D3 edge", "vllm-sr/vela": "Vela", "Vela 2.0 (vllm-sr)": "Vela 2.0", "vllm-sr/guardrail": "Guardrail",
};
const oneModel = (m: string) => MODEL_NAME[m] || m.replace(/^vllm-sr\//, "");
/** "gptoss20b + vllm-sr/d3-mini" -> "gpt-oss-20B + D3 mini". */
export const modelName = (m: string | undefined) => (m ? m.split(/\s*\+\s*/).map(oneModel).join(" + ") : "");

export const DASH = "–";
export const fmtMs = (ms: number | undefined | null) => (ms == null ? DASH : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);
export const fmtInt = (n: number | undefined | null) => (n == null ? DASH : Math.round(n).toLocaleString("en-US"));
export const fmtPct = (x: number | undefined | null) => (x == null ? DASH : `${Math.round(x * 100)}%`);
export const fmtCompact = (n: number) => (n >= 10000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : n.toLocaleString("en-US"));
export const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ");
