"""Returns photo check demo server (127.0.0.1:8602): the docs/API.md contract over app/flow.py, and the built page.

Run: ./start_server.sh [--bg]   (or: python app/server.py)
"""
import json
import mimetypes
import os
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claim  # noqa: E402
from flow import Session  # noqa: E402

try:  # iPhone photos
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SAMPLES = ROOT / "data/samples"
PORT = int(os.environ.get("PORT", "8602"))
MAX_BODY = 25_000_000   # a base64 phone photo is ~4-8 MB
HOST = os.environ.get("HOST", "127.0.0.1")
# Price of one GPU hour for the $ figures: DigitalOcean's on-demand MI300X GPU Droplet price per GPU (checked
# 2026-10-26). Set your own cloud's price.
GPU_USD_PER_HOUR = float(os.environ.get("GPU_USD_PER_HOUR", "2.59"))
BASELINE = (json.loads((ROOT / "data/baseline_27b.json").read_text())
            if (ROOT / "data/baseline_27b.json").exists() else None)
# which LLM answered each sample turn in the last bench/run_scenarios.py run (Vela's choice), for the "120B" marker
MODELS = (json.loads((ROOT / "data/scenario_models.json").read_text())
          if (ROOT / "data/scenario_models.json").exists() else {})
CASES = ["note_screen", "cut_off", "clear", "blurry", "dark", "glare", "intact", "hand", "finger", "note"]


class SessionStats:
    """Stats of one chat (the top bar is per chat: a new chat or a new sample starts from zero)."""
    def __init__(self):
        self.calls = []            # (kind, ms, tokens) of every model call
        self.reply_models = []     # model per turn
        self.baseline = []         # (seconds, tokens) of the all-120B run for each turn
        self.blocked = 0
        self.off_topic = 0
        self.avoided = 0           # claims closed before any photo check

    def as_dict(self):
        d3 = [ms for k, ms, _ in self.calls if k == "d3"]
        n = len(self.reply_models)
        llm = [m for m in self.reply_models if m]
        seconds = sum(ms for _k, ms, _t in self.calls) / 1000
        llm_tokens = sum(t for k, _ms, t in self.calls if k.startswith("gptoss"))
        d3_tokens = sum(t for k, _ms, t in self.calls if k == "d3")
        b_seconds = sum(x for x, _ in self.baseline) if n else None
        b_tokens = sum(t for _, t in self.baseline) if n else None
        usd = lambda secs: round(secs / 3600 * GPU_USD_PER_HOUR * 1e6) if secs else None
        return {"turns": n, "d3_calls": len(d3), "d3_ms_median": round(statistics.median(d3)) if d3 else None,
                # share of the turns where an LLM actually wrote the reply
                "share": {m: round(llm.count(m) / len(llm), 3) for m in ("gptoss20b", "gptoss120b")} if llm else None,
                "llm_turns": len(llm),
                "blocked": self.blocked, "off_topic": self.off_topic, "image_checks_avoided": self.avoided,
                "model_seconds": {"routed": round(seconds, 2), "baseline_27b": round(b_seconds, 2) if n else None},
                "tokens": {"routed": llm_tokens, "baseline_27b": b_tokens, "d3": d3_tokens,
                           "note": "LLM tokens only; D3 reads images as tokens too (d3)"},
                "usd_per_million_chats": {"routed": usd(seconds), "baseline_27b": usd(b_seconds)},
                "gpu_usd_per_hour": GPU_USD_PER_HOUR,
                "baseline": BASELINE and {"correct": BASELINE["correct"], "turns": BASELINE["turns"],
                                          "measured": BASELINE["measured"]}}


def baseline_turn(text, image):
    """All-120B time and tokens for this turn: the measured value when it is a sample turn, else the measured average
    for that kind of turn (text only, or with a photo)."""
    if not BASELINE:
        return 0.0, 0
    hit = BASELINE.get("by_input", {}).get(image if image and not image.startswith("data:") else text or "")
    if hit and not (image and text and image.startswith("data:")):
        return hit["ms"] / 1000, hit["tokens"]
    a = BASELINE.get("avg", {}).get("image" if image else "text")
    if a:
        return a["ms"] / 1000, a["tokens"]
    return BASELINE["seconds"] / BASELINE["turns"], round(BASELINE["tokens"] / BASELINE["turns"])


_health = {"at": 0.0, "value": None}


def health():
    """Each part, probed the way the flow calls it (cached for 10 s). ok is true when all are up."""
    import requests
    from models import ROUTER_URL, chat, d3
    from turn_checks import router_preview
    if _health["value"] and time.time() - _health["at"] < 10:
        return _health["value"]
    parts, detail = {}, {}

    def probe(name, fn):
        try:
            fn()
            parts[name] = True
        except Exception as e:  # noqa: BLE001
            parts[name], detail[name] = False, f"{type(e).__name__}: {str(e)[:200]}"

    probe("router", lambda: requests.get(f"{ROUTER_URL}/v1/models", timeout=5).raise_for_status())

    def preview():
        pv, _ms = router_preview([{"role": "user", "content": "hello"}])
        if pv is None:
            raise RuntimeError("no answer from the Router management API (ROUTER_ADMIN_URL)")
        if not pv["decision"] or not str(pv["decision"]).startswith("returns"):
            raise RuntimeError(f"returns/auto is not configured (decision {pv['decision']!r}); see deploy/router.yaml")
    probe("vela_routing", preview)
    probe("d3", lambda: d3("health check", {"q": {"type": "noul", "instructions": "This is a test."}}, timeout=(3, 20)))
    probe("gptoss20b", lambda: chat("gptoss20b", [{"role": "user", "content": "Say OK."}], max_tokens=2, timeout=(3, 30)))
    probe("gptoss120b", lambda: chat("gptoss120b", [{"role": "user", "content": "Say OK."}], max_tokens=2, timeout=(3, 60)))
    value = {"ok": all(parts.values()), **parts, "detail": detail}
    _health.update(at=time.time(), value=value)
    return value


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.sessions, self.session_locks, self.turns, self.stats_by_session = {}, {}, {}, {}

    def session(self, sid):
        with self.lock:
            if sid not in self.sessions:
                self.sessions[sid] = Session(sid)
                self.session_locks[sid] = threading.Lock()
                self.stats_by_session[sid] = SessionStats()
            return self.sessions[sid], self.session_locks[sid]

    def stats(self, sid):
        with self.lock:
            st = self.stats_by_session.get(sid) or SessionStats()
            return {"session": sid, **st.as_dict()}

    def record(self, done, verdict, sid, text, image):
        with self.lock:
            st = self.stats_by_session.get(sid)
            if st is None:      # the stats were reset while this turn ran
                return
            st.calls += done["calls"]
            # the model that wrote the reply; None when the turn got a fixed reply (blocked, off-topic): no LLM ran
            st.reply_models.append(next((k for k, _ms, _t in reversed(done["calls"]) if k.startswith("gptoss")), None))
            st.baseline.append(baseline_turn(text, image))
            st.blocked += verdict == "blocked"
            st.off_topic += verdict == "off_topic"
            st.avoided += bool(done.get("avoided"))
            self.turns[done["turn"]] = {"turn": done["turn"], "steps": done["steps"]}


STATE = State()


def samples():
    data = json.loads((ROOT / "data/scenarios.json").read_text())
    for g in data["groups"]:
        for sc in g["scenarios"]:
            sc["product"] = claim.ORDERS[sc["order"]]["product"] if sc.get("order") in claim.ORDERS else None
            # the outcome that matters: the last verdict before the claim was closed
            used = MODELS.get(sc["id"], [])
            for i, t in enumerate(sc["turns"]):
                t["model"] = used[i] if i < len(used) else None
            sc["uses_27b"] = "gptoss120b" in used
            sc["expected"] = sc.get("expected") or next(t["expect"] for t in reversed(sc["turns"]) if t["expect"] != "closed")
    data["policies"] = claim.POLICIES
    return data


def gallery():
    out = []
    for p in sorted(SAMPLES.glob("*/*.jpg")):
        stem = p.stem
        case = next((c for c in CASES if stem.endswith("_" + c)), "other")
        out.append({"path": f"samples/{p.parent.name}/{p.name}", "product": p.parent.name, "case": case})
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path):
        if not path.is_file():
            return self._json(404, {"error": "not found"})
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("content-type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/health", "/api/health"):
            h = health()
            return self._json(200 if h["ok"] else 503, h)
        if path == "/api/samples":
            return self._json(200, samples())
        if path == "/api/customer":
            c = claim.CUSTOMER
            orders = sorted((claim.ORDERS[o] for o in c["orders"]), key=lambda o: o["delivered_days_ago"])
            return self._json(200, {"name": c["name"], "email": c["email"], "greeting": c["greeting"],
                                    "orders": [{**o, "product_name": claim.POLICIES[o["product"]]["name"],
                                                "window_days": claim.POLICIES[o["product"]]["window_days"]}
                                               for o in orders]})
        if path == "/api/gallery":
            return self._json(200, gallery())
        if path == "/api/stats":
            q = parse_qs(urlparse(self.path).query)
            return self._json(200, STATE.stats((q.get("session") or [""])[0]))
        if path.startswith("/api/turn/"):
            t = STATE.turns.get(path.rsplit("/", 1)[1])
            return self._json(200, t) if t else self._json(404, {"error": "unknown turn"})
        if path.startswith("/samples/"):
            p = (SAMPLES / path[len("/samples/"):]).resolve()
            return self._file(p) if p.is_relative_to(SAMPLES) else self._json(404, {"error": "not found"})
        p = (WEB / path.lstrip("/")).resolve()
        if path == "/" or not p.is_file() or not p.is_relative_to(WEB):
            p = WEB / "index.html"
        return self._file(p)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("content-length", 0) or 0)
        if length > MAX_BODY:
            return self._json(413, {"error": f"upload too large (max {MAX_BODY // 1_000_000} MB)"})
        body = self.rfile.read(length)
        if path == "/api/reset":
            with STATE.lock:
                STATE.reset()
            return self._json(200, {"ok": True})
        if path != "/api/chat":
            return self._json(404, {"error": "not found"})
        try:
            req = json.loads(body or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        sid, text, image = str(req.get("session") or "default")[:64], req.get("text"), req.get("image")
        action = req.get("action") if req.get("action") in ("review",) else None
        text = text.strip() if isinstance(text, str) else None
        image = image if isinstance(image, str) and image else None
        if not (text or image or action):
            return self._json(400, {"error": "text, image or action required"})
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-store")
        self.send_header("x-accel-buffering", "no")
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True

        def send(ev):
            self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
            self.wfile.flush()

        session, lock = STATE.session(sid)
        verdict = None
        try:
            with lock:
                for ev in session.turn(text, image, action):
                    if ev["type"] == "message":
                        verdict = ev["verdict"]
                    if ev["type"] == "done" and ev.get("no_turn"):
                        ev = {"type": "done", "turn": ev["turn"]}   # a message to an ended chat: no stats
                    elif ev["type"] == "done":
                        STATE.record(ev, verdict, sid, text, image)
                        send({"type": "stats", **STATE.stats(sid)})
                        ev = {"type": "done", "turn": ev["turn"]}
                    send(ev)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # model or Router failure: end the turn cleanly, keep serving
            sorry = "Sorry, something went wrong on our side. Please send that again."
            if session.history and session.history[-1]["role"] == "user":
                session.history.append({"role": "assistant", "content": sorry})
            try:
                send({"type": "error", "message": f"{type(e).__name__}: {str(e)[:300]}"})
                send({"type": "message", "role": "assistant", "text": sorry, "verdict": "error", "order": None,
                      "policy_ref": None})
            except OSError:
                pass


if __name__ == "__main__":
    print(f"returns validator demo on http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
