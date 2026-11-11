"""Plays data/scenarios.json through the real flow and checks every turn's verdict.

Usage: python bench/run_scenarios.py [scenario-id ...]
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from flow import Session  # noqa: E402

groups = json.loads((ROOT / "data/scenarios.json").read_text())["groups"]
want = set(sys.argv[1:])
bad = total = 0
models = {}   # scenario id -> the LLM that wrote each reply (None: fixed reply), for the "120B" marker
for g in groups:
    print(f"\n## {g['title']}")
    for sc in g["scenarios"]:
        if want and sc["id"] not in want:
            continue
        s = Session(sc["id"])
        print(f"- {sc['id']}: {sc['title']}")
        for t in sc["turns"]:
            t0 = time.perf_counter()
            msg, summ, used = None, {}, None
            for ev in s.turn(t.get("text"), t.get("image")):
                if ev["type"] == "step" and ev["status"] not in ("waiting", "running", "skipped"):
                    summ[ev["node"]] = ev["summary"]
                if ev["type"] == "step" and ev["node"] == "reply" and ev["status"] == "done":
                    used = ev["model"]
                if ev["type"] == "message":
                    msg = ev
            models.setdefault(sc["id"], []).append(used)
            total += 1
            ok = msg["verdict"] == t["expect"]
            bad += not ok
            what = t.get("text") or f"[{t['image'].split('/')[-1]}]"
            print(f"    {'ok ' if ok else 'BAD'} {msg['verdict']:12s} (want {t['expect']:12s}) {time.perf_counter() - t0:4.1f} s | {what[:60]}")
            for n in ("guardrail", "eligibility", "image_check"):
                if n in summ:
                    print(f"        {n}: {summ[n]}")
            print(f"        reply: {msg['text']}")
print(f"\n{total - bad}/{total} turns as expected")
if not want:
    (ROOT / "data/scenario_models.json").write_text(json.dumps(models, indent=1))
    print("27B used in:", [k for k, v in models.items() if "gptoss120b" in v])
sys.exit(1 if bad else 0)
