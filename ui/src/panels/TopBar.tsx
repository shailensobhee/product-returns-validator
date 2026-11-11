/* Title, the stats of the current chat session (`stats` events / GET /api/stats?session=), the theme switch.
 * One row of stat blocks spread over the bar in two groups: "This chat" counts and "Routed vs all on gpt-oss-120B".
 * Missing values render as a dash; a new chat starts from an empty bar. */
import type { ReactNode } from "react";
import { ThemeSwitch, type Theme } from "../components/arc/theme-switch/theme-switch";
import type { Stats } from "../api";
import { DASH, fmtCompact, fmtInt, fmtPct } from "../format";
import styles from "./TopBar.module.css";

type Pair = { routed: number | null; baseline_27b: number | null } | undefined;

function saving(p: Pair) {
  if (!p || p.routed == null || p.baseline_27b == null || !(p.baseline_27b > 0)) return null;
  const pct = Math.round(((p.baseline_27b - p.routed) / p.baseline_27b) * 100);
  return pct === 0 ? "same" : pct > 0 ? `${pct}% less` : `${-pct}% more`;
}

function Stat({ label, hint, wide, children }: { label: string; hint?: string; wide?: boolean; children: ReactNode }) {
  return (
    <dl className={styles.item} title={hint} data-wide={wide || undefined}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </dl>
  );
}

/** Routed value, "vs" the all-120B value, and the saving. Dashes until the chat has a turn with a baseline. */
function Compare({ label, pair, fmt, hint, ready }: { label: string; pair: Pair; fmt: (n: number) => string; hint: string; ready: boolean }) {
  const has = ready && pair && pair.baseline_27b != null;
  const s = has ? saving(pair) : null;
  return (
    <Stat label={label} hint={hint} wide>
      {has ? <>
        <span>{pair.routed == null ? DASH : fmt(pair.routed)}</span>
        <span className={styles.vs}>vs</span>
        <span className={styles.base}>{fmt(pair.baseline_27b as number)}</span>
        {s && <span className={styles.saving} data-worse={s.endsWith("more") || undefined}>{s}</span>}
      </> : DASH}
    </Stat>
  );
}

export function TopBar({ stats, theme, onTheme }: { stats: Stats | null; theme: Theme; onTheme: (t: Theme) => void }) {
  const s = stats ?? {};
  const share = s.share ?? null;
  const ready = (s.turns ?? 0) > 0;
  return (
    <header className={styles.bar}>
      <div className={styles.brand}>
        <h1 title="D3 decides, gpt-oss chats">Returns validator Demo</h1>
      </div>

      <div className={styles.stats} role="group" aria-label="Stats of this chat">
        <span className={styles.caption} data-group="chat">This chat</span>
        <span className={styles.caption} data-group="compare">Routed vs all on gpt-oss-120B</span>
        <span className={styles.divider} aria-hidden="true" />

        <Stat label="D3 calls" hint="D3 calls in this chat and their median time">
          {fmtInt(s.d3_calls)}
          {s.d3_ms_median != null && <span className={styles.unit}>{Math.round(s.d3_ms_median)} ms<span className={styles.long}> median</span></span>}
        </Stat>
        <Stat label="gpt-oss 20B / 120B" hint="Share of this chat's turns that Vela sent to gpt-oss-20B and to gpt-oss-120B">
          {share ? <>{fmtPct(share.gptoss20b)}<span className={styles.vs}>/</span>{fmtPct(share.gptoss120b)}</> : DASH}
        </Stat>
        <Stat label="Blocked / off-topic" hint="Turns stopped by the guardrail, and off-topic turns, in this chat; both get a fixed reply with no model call">
          {s.blocked == null && s.off_topic == null ? DASH : <>{fmtInt(s.blocked ?? 0)}<span className={styles.vs}>/</span>{fmtInt(s.off_topic ?? 0)}</>}
        </Stat>
        <Stat label="Image checks avoided" hint="1 when this chat was settled without a photo check (not eligible or human review)">
          {fmtInt(s.image_checks_avoided)}
        </Stat>

        <Compare label="Model time" pair={s.model_seconds} ready={ready} fmt={(n) => `${n.toFixed(1)} s`}
          hint="Model time of this chat: routed (Vela + D3) against sending every turn to gpt-oss-120B" />
        <Compare label="LLM tokens" pair={s.tokens} ready={ready} fmt={fmtCompact}
          hint={`gpt-oss tokens, prompt plus completion, routed against all on gpt-oss-120B. D3 also read ${fmtCompact(s.tokens?.d3 ?? 0)} tokens, mostly image tokens, so all models together use more tokens when routed.`} />
        <Compare label="$ per 1M chats" pair={s.usd_per_million_chats} ready={ready} fmt={(n) => `$${fmtCompact(Math.round(n))}`}
          hint="Estimated GPU cost per million chats like this one" />
      </div>

      <div className={styles.actions}>
        <ThemeSwitch theme={theme} iconOnly variant="rise" onThemeChange={(next) => onTheme(next)} />
      </div>
    </header>
  );
}
