"""Model calls: D3 (System One API) and gpt-oss (chat completions), both through the vllm-sr Router on :8899."""
import base64
import os
import io
import json
import time
from pathlib import Path

import requests
from PIL import Image, ImageOps

ROUTER_URL = os.environ.get("ROUTER_URL", "http://localhost:8899")
D3_MODEL = "vllm-sr/d3-mini"
ROOT = Path(__file__).resolve().parent.parent


def d3(state, questions, images=(), model=D3_MODEL, timeout=(3, 30)):
    """Returns (answers, ms, tokens)."""
    body = {"model": model, "state": state, "questions": questions}
    if images:
        body["images"] = list(images)
    t = time.perf_counter()
    r = requests.post(f"{ROUTER_URL}/v1/systemone", json=body, timeout=timeout)
    r.raise_for_status()
    out = r.json()
    u = out.get("usage") or {}
    return out["answers"], round((time.perf_counter() - t) * 1000, 1), (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)


try:  # iPhone photos (HEIC) wherever images are read, not only in the server
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

Image.MAX_IMAGE_PIXELS = 60_000_000  # a 48 MP phone photo fits; decompression bombs raise instead of filling memory


class BadImage(ValueError):
    """The upload is not an image we can read; the flow answers with a "send a photo" reply and changes no state."""


def image_url(image):
    """A sample path (relative to data/) or a data URL -> an upright JPEG data URL, at most 1600 px on the long side.
    Raises BadImage for anything that is not a readable image."""
    try:
        if image.startswith("data:"):
            head, sep, b64 = image.partition(",")
            if not sep or ";base64" not in head:
                raise BadImage("not a base64 data URL")
            raw = base64.b64decode(b64, validate=False)
        else:
            p = (ROOT / "data" / image).resolve()
            if not p.is_relative_to(ROOT / "data") or not p.is_file():
                raise BadImage("unknown image path")
            raw = p.read_bytes()
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    except BadImage:
        raise
    except Exception as e:  # PIL: not an image, truncated, too many pixels; base64 errors
        raise BadImage(f"{type(e).__name__}") from e
    im.thumbnail((1600, 1600), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def chat(model, messages, max_tokens=300, schema=None, timeout=(3, 120)):
    """Non-streaming call. Returns (text, ms, tokens)."""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0,
            "reasoning_effort": "low"}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "schema": schema}}
    t = time.perf_counter()
    r = requests.post(f"{ROUTER_URL}/v1/chat/completions", json=body, timeout=timeout)
    r.raise_for_status()
    out = r.json()
    return (out["choices"][0]["message"]["content"].strip(), round((time.perf_counter() - t) * 1000, 1),
            (out.get("usage") or {}).get("total_tokens") or 0)


def chat_stream(model, messages, max_tokens=300, timeout=(3, 120)):
    """Streaming call. Yields ("token", text) and finally ("done", {"ms", "tokens", "text"})."""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.3, "stream": True,
            "stream_options": {"include_usage": True}, "reasoning_effort": "low"}
    t = time.perf_counter()
    text, tokens = [], 0
    with requests.post(f"{ROUTER_URL}/v1/chat/completions", json=body, timeout=timeout, stream=True) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if chunk.get("usage"):
                tokens = chunk["usage"].get("total_tokens") or tokens
            for c in chunk.get("choices") or []:
                piece = (c.get("delta") or {}).get("content")
                if piece:
                    text.append(piece)
                    yield "token", piece
    yield "done", {"ms": round((time.perf_counter() - t) * 1000, 1), "tokens": tokens, "text": "".join(text).strip()}
