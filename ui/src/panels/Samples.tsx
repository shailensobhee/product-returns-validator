/* Left panel: the preloaded conversations in their groups (valid, invalid, malicious), each with its expected verdict
 * and, after a run, whether every turn matched. Groups are collapsible and start open; Play all at the bottom. */
import { useState } from "react";
import { ChevronRight, Play, Square } from "lucide-react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { Button } from "../components/arc/button/button";
import { ScrollArea } from "../components/arc/scroll-area/scroll-area";
import { motionTokens } from "../components/arc/lib/motion-tokens";
import type { Group, Scenario } from "../api";
import { verdictMeta } from "../format";
import { matches, type Outcomes, type Playing, type Used27 } from "../useDemo";
import styles from "./Samples.module.css";

function ExpectedIcon({ expected }: { expected: string }) {
  const v = verdictMeta(expected);
  if (!v) return null;
  const Icon = v.icon;
  return <span className={styles.expected} data-tone={v.tone} aria-hidden="true"><Icon size={15} strokeWidth={1.9} /></span>;
}

/** Turn-by-turn comparison for the tooltip of a finished run. */
function diffText(sc: Scenario, got: Outcomes[string]) {
  return sc.turns.map((t, i) => {
    const g = verdictMeta(got[i])?.label ?? got[i] ?? "no reply";
    const e = verdictMeta(t.expect)?.label ?? t.expect;
    return `Turn ${i + 1}: ${g}${got[i] === t.expect ? "" : `, expected ${e}`}`;
  }).join("\n");
}

export function Samples({ groups, error, playing, outcomes, used27, onPlay, onPlayAll, onStop }: {
  groups: Group[]; error: string | null; playing: Playing | null; outcomes: Outcomes;
  /** What the last run of each scenario did; before a run the badge follows `uses_27b` from the bench. */
  used27: Used27;
  onPlay: (group: string, sc: Scenario) => void; onPlayAll: () => void; onStop: () => void;
}) {
  const reduce = useReducedMotion();
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const toggle = (k: string) => setClosed((c) => ({ ...c, [k]: !c[k] }));
  const total = groups.reduce((a, g) => a + g.scenarios.length, 0);
  return (
    <nav className={styles.root} aria-label="Sample conversations">
      <div className={styles.head}>
        <h2>Sample conversations</h2>
        <span className={styles.count}>{total || ""}</span>
      </div>
      <ScrollArea className={styles.scroll} label="Sample conversations">
        {error && <p className={styles.error}>{error}</p>}
        <ul className={styles.groups}>
          {groups.map((g) => {
            const open = !closed[g.key] || playing?.group === g.key;
            return (
              <li key={g.key} className={styles.group} data-group={g.key}>
                <button type="button" className={styles.groupBtn} aria-expanded={open} onClick={() => toggle(g.key)}>
                  <motion.span className={styles.chev} animate={{ rotate: open ? 90 : 0 }} transition={reduce ? { duration: 0 } : motionTokens.spring.snappy}>
                    <ChevronRight size={15} strokeWidth={1.9} aria-hidden="true" />
                  </motion.span>
                  <span className={styles.groupName}>{g.title}</span>
                  <span className={styles.groupCount}>{g.scenarios.length}</span>
                </button>
                <AnimatePresence initial={false}>
                  {open && (
                    <motion.ul className={styles.list}
                      initial={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }} animate={reduce ? { opacity: 1 } : { height: "auto", opacity: 1 }}
                      exit={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }} transition={reduce ? { duration: 0.12 } : motionTokens.spring.smooth}>
                      {g.scenarios.map((sc) => {
                        const active = playing?.scenario.id === sc.id;
                        const got = outcomes[sc.id];
                        const ok = got ? matches(sc, got) : null;
                        const exp = verdictMeta(sc.expected);
                        const ran = Object.prototype.hasOwnProperty.call(used27, sc.id);
                        const big = ran ? used27[sc.id] : !!sc.uses_27b;
                        return (
                          <li key={sc.id}>
                            <button type="button" className={styles.scenario} data-active={active || undefined} aria-current={active ? "true" : undefined}
                              onClick={() => onPlay(g.key, sc)} title={got ? diffText(sc, got) : sc.turns.map((t) => t.text ?? `[photo] ${t.image ?? ""}`).join("\n")}
                              aria-label={`${sc.title}. ${sc.subtitle ?? ""}. Expected: ${exp?.label ?? sc.expected}${big ? ". Uses gpt-oss-120B" : ""}${ok == null ? "" : ok ? ". Matched" : ". Differs"}`}>
                              <ExpectedIcon expected={sc.expected} />
                              <span className={styles.scText}>
                                <span className={styles.scLine}>
                                  <span className={styles.scTitle}>{sc.title}</span>
                                  {big && <span className={styles.b27} title={ran ? "Vela routed a turn of this run to gpt-oss-120B" : "Vela routes a turn of this chat to gpt-oss-120B"}>120B</span>}
                                  {active ? <span className={styles.live}>{`Turn ${playing!.turn + 1}/${sc.turns.length}`}</span>
                                    : ok != null && <span className={styles.result} data-ok={ok}>{ok ? "Matched" : "Differs"}</span>}
                                </span>
                                <span className={styles.scSub}>
                                  {exp && <span className={styles.expWord} data-tone={exp.tone}>{exp.short}</span>}
                                  {sc.subtitle && <span>{sc.subtitle}</span>}
                                </span>
                              </span>
                            </button>
                          </li>
                        );
                      })}
                    </motion.ul>
                  )}
                </AnimatePresence>
              </li>
            );
          })}
        </ul>
      </ScrollArea>
      <div className={styles.foot}>
        {playing?.all
          ? <Button variant="secondary" onClick={onStop}><Square size={14} strokeWidth={2} aria-hidden="true" /> Stop</Button>
          : <Button onClick={onPlayAll} disabled={!total}><Play size={14} strokeWidth={2} aria-hidden="true" /> Play all</Button>}
        {playing && !playing.all && <Button variant="ghost" onClick={onStop}>Stop</Button>}
      </div>
    </nav>
  );
}
