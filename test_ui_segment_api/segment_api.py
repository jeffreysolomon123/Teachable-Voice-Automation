"""UI segmentation + OCR of a screenshot in ONE OpenRouter vision-LLM call.

Replaces the 3-step ui-segmentation pipeline (OmniParser YOLO + EasyOCR + combine)
with a single request that returns every UI element with its type, text and box.
Output JSON uses the same element fields as ui-segmentation's *_combined.json.

    python segment_api.py screenshot.png
    python segment_api.py screenshot.png --max-side 1280 --compact   # experimental: fewer tokens, lower recall
    python segment_api.py screenshot.png -m qwen/qwen3-vl-30b-a3b-instruct

Reads API_KEY from .env next to this script.
"""
import argparse
import base64
import json
import re
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
DEFAULT_MODEL = "google/gemini-2.5-flash-lite"
TYPES = ["button", "icon_button", "text_field", "tab", "chip", "checkbox", "toggle", "link",
         "image", "icon", "text", "card", "list_item", "nav_bar", "status_bar", "container"]

PROMPT = """You are a UI parser. Detect EVERY visible UI element in this mobile screenshot and read its text.

Include: buttons, icon buttons (back, menu, share, bookmark...), text fields, tabs, chips/filters,
checkboxes, toggles, links, images, standalone icons, every text line/label, cards, list items,
status bar and nav bar. Include both a card AND the separate elements inside it.

Return ONLY JSON: {"elements": [{"type": ..., "text": ..., "label": ..., "interactive": ..., "BOX_KEY": ...}, ...]}
- type: one of TYPES
- text: exact visible text, verbatim (keep symbols like the rupee sign), "" if none
- label: for elements without text, a short description (e.g. "back arrow", "pizza photo"), else ""
- interactive: true if tappable
- BOX_KEY: BOX_DESC, integers normalized 0-1000 relative to image size, tight around the element
Order elements top-to-bottom, left-to-right."""

# EXPERIMENTAL (opt-in, untested): same task with short keys for fewer output tokens.
# A JSON schema is NOT used: structured outputs for gemini-2.5-flash-lite via OpenRouter
# returned empty {} objects, and a flat array-per-element format was ignored by the model.
PROMPT_COMPACT = """You are a UI parser. Detect EVERY visible UI element in this mobile screenshot and read its text.

Include: buttons, icon buttons (back, menu, share, bookmark...), text fields, tabs, chips/filters,
checkboxes, toggles, links, images, standalone icons, every text line/label, cards, list items.
Include both a card AND the separate elements inside it. List each element once.

Return ONLY JSON: {"e": [{"box_2d": [...], "t": ..., "s": ..., "k": ...}, ...]}
- box_2d: [ymin, xmin, ymax, xmax], integers normalized 0-1000, tight around the element
- t: element type, one of TYPES
- s: exact visible text, verbatim (keep symbols like the rupee sign); for an element without
  text, a short description in parentheses instead, e.g. "(back arrow)", "(pizza photo)"
- k: 1 if tappable, else 0
Order elements top-to-bottom, left-to-right."""

# Box formats each model family was trained on; asking in the native format gives the best boxes.
BOX_FORMATS = {
    "gemini": ("box_2d", "[ymin, xmin, ymax, xmax]", lambda b: [b[1], b[0], b[3], b[2]]),
    "default": ("bbox", "[x1, y1, x2, y2]", lambda b: b),
}
# Skip Google's flex tier (cheap but slow, queued) and prefer the highest-throughput endpoint.
FAST_PROVIDER = {"sort": "throughput", "ignore": ["google-ai-studio/flex"]}
# A good full reply is ~3k tokens; a cap well above that ends runaway repetition loops early.
MAX_TOKENS = 6000


def load_key():
    for line in (HERE / ".env").read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        if k.strip() in ("API_KEY", "OPENROUTER_API_KEY"):
            return v.strip().strip('"\'')
    raise SystemExit("API_KEY not found in .env")


def encode_image(img, max_side, quality):
    """Downscale so the long side is <= max_side (0 = keep) and JPEG-encode in memory.
    Boxes come back normalized 0-1000, so they map onto the original size unchanged."""
    h, w = img.shape[:2]
    if max_side and max(h, w) > max_side:
        f = max_side / max(h, w)
        img = cv2.resize(img, (round(w * f), round(h * f)), interpolation=cv2.INTER_AREA)
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf.tobytes()).decode()


def call_model(image_b64, model, prompt, provider=None):
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"},
        "usage": {"include": True},
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": prompt},
        ]}],
    }
    if provider:
        body["provider"] = provider
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {load_key()}", "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code}: {e.read().decode()[:500]}")
    if "error" in resp:
        raise SystemExit(f"API error: {resp['error']}")
    return resp, time.time() - t0


def normalize(r):
    """Map compact {box_2d, t, s, k} (or keyed) element -> keyed element dict."""
    if "s" not in r and "t" not in r:
        return r
    text = str(r.get("s", ""))
    label = text[1:-1] if text.startswith("(") and text.endswith(")") else ""
    return {"box_2d": r.get("box_2d"), "type": r.get("t", ""), "text": "" if label else text,
            "label": label, "interactive": bool(r.get("k", 0))}


def parse_json(content):
    """Returns (element dicts, complete). Salvages whole elements from truncated output."""
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            data = data.get("elements", data.get("e", []))
        return [normalize(r) for r in data if isinstance(r, dict)], True
    except json.JSONDecodeError:
        pieces = re.findall(r'\{[^{}]*\}', content)
        out = []
        for p in pieces:
            try:
                out.append(normalize(json.loads(p)))
            except json.JSONDecodeError:
                pass
        return out, False


def region(cx, cy, w, h):
    row = ["top", "middle", "bottom"][min(int(3 * cy / h), 2)]
    col = ["left", "center", "right"][min(int(3 * cx / w), 2)]
    return f"{row}-{col}"


def contains(outer, inner, frac=0.9):
    ix1, iy1 = max(outer[0], inner[0]), max(outer[1], inner[1])
    ix2, iy2 = min(outer[2], inner[2]), min(outer[3], inner[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return area > 0 and inter / area >= frac


def to_elements(raw, w, h, to_xyxy):
    out = []
    for r in raw:
        box = r.get("bbox") or r.get("box_2d")
        if not isinstance(box, list) or len(box) != 4:
            continue
        try:
            x1, y1, x2, y2 = to_xyxy([float(v) for v in box])
        except (TypeError, ValueError):
            continue
        x1, x2 = sorted((max(0, min(1000, x1)), max(0, min(1000, x2))))
        y1, y2 = sorted((max(0, min(1000, y1)), max(0, min(1000, y2))))
        bbox = [round(x1 * w / 1000), round(y1 * h / 1000), round(x2 * w / 1000), round(y2 * h / 1000)]
        if bbox[2] - bbox[0] < 2 or bbox[3] - bbox[1] < 2:
            continue
        cx, cy = (bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2
        out.append({
            "type": r.get("type", ""), "text": r.get("text", "") or "", "label": r.get("label", "") or "",
            "interactive": bool(r.get("interactive", False)),
            "bbox": bbox, "center": [cx, cy], "size": [bbox[2] - bbox[0], bbox[3] - bbox[1]],
            "bbox_norm": [round(x1 / 1000, 4), round(y1 / 1000, 4), round(x2 / 1000, 4), round(y2 / 1000, 4)],
            "center_norm": [round(cx / w, 4), round(cy / h, 4)],
            "area_pct": round(100 * (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) / (w * h), 2),
            "region": region(cx, cy, w, h), "source": "llm",
        })
    # Drop exact duplicates, then renumber in reading order.
    seen, uniq = set(), []
    for e in out:
        key = (tuple(e["bbox"]), e["text"])
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    uniq.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
    for i, e in enumerate(uniq):
        e["id"], e["parent"], e["children"] = i, None, []
    # Parent = smallest larger element that contains >= 90% of this one.
    for e in uniq:
        area = e["size"][0] * e["size"][1]
        cands = [p for p in uniq if p is not e and p["size"][0] * p["size"][1] > area
                 and contains(p["bbox"], e["bbox"])]
        if cands:
            p = min(cands, key=lambda p: p["size"][0] * p["size"][1])
            e["parent"] = p["id"]
            p["children"].append(e["id"])
    return uniq


def annotate(img, elements, out_path):
    img = img.copy()
    scale = max(1, img.shape[1] // 700)
    for e in elements:
        color = (0, 0, 255) if e["interactive"] else ((0, 150, 0) if e["type"] == "text" else (200, 100, 0))
        x1, y1, x2, y2 = e["bbox"]
        cv2.rectangle(img, (x1, y1), (x2, y2), color, scale)
    for e in elements:
        x1, y1 = e["bbox"][:2]
        label = str(e["id"])
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, scale)
        cv2.rectangle(img, (x1, y1), (x1 + tw + 4, y1 + th + 6), (0, 0, 0), -1)
        cv2.putText(img, label, (x1 + 2, y1 + th + 3), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale,
                    (255, 255, 255), scale)
    _, buf = cv2.imencode(".png", img)
    buf.tofile(str(out_path))


def segment(img, model=DEFAULT_MODEL, max_side=0, quality=85, compact=False, fast_provider=True,
            retries=2):
    """Run the single-call segmentation on a BGR image. Returns (elements, info dict)."""
    h, w = img.shape[:2]
    is_gemini = "gemini" in model
    box_key, box_desc, to_xyxy = BOX_FORMATS["gemini" if is_gemini else "default"]
    if compact:  # compact format always uses [ymin, xmin, ymax, xmax]
        prompt, to_xyxy = PROMPT_COMPACT, BOX_FORMATS["gemini"][2]
    else:
        prompt = PROMPT.replace("BOX_KEY", box_key).replace("BOX_DESC", box_desc)
    prompt = prompt.replace("TYPES", ", ".join(TYPES))
    t0 = time.time()
    image_b64 = encode_image(img, max_side, quality)
    encode_s = time.time() - t0
    provider = FAST_PROVIDER if fast_provider and is_gemini else None

    # Upstream providers often end a stream with finish_reason=error (sometimes unbilled, zero
    # usage). Such a reply is cut off even if part of it parses, so never accept it; retry.
    for attempt in range(1, retries + 2):
        resp, secs = call_model(image_b64, model, prompt, provider)
        choice = resp["choices"][0]
        finish = choice.get("finish_reason")
        content = choice["message"].get("content") or ""
        raw, complete = parse_json(content)
        elements = to_elements(raw, w, h, to_xyxy)
        upstream_failed = finish == "error" or not resp.get("usage", {}).get("completion_tokens")
        if complete and elements and not upstream_failed:
            break
        why = "upstream error" if upstream_failed else ("hit max_tokens" if finish == "length" else "bad JSON")
        print(f"attempt {attempt} via {resp.get('provider')}: {why}, {len(elements)} elements "
              f"(finish_reason={finish}, {len(content)} chars)"
              + (", retrying..." if attempt <= retries else ", giving up; returning partial result"))
    info = {"model": model, "provider": resp.get("provider"), "latency_s": round(secs, 2),
            "encode_s": round(encode_s, 3), "upload_kb": len(image_b64) // 1024,
            "attempts": attempt, "usage": resp.get("usage", {}), "raw": content}
    return elements, info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("-m", "--model", default=DEFAULT_MODEL)
    ap.add_argument("-d", "--dir", default=str(HERE / "out"))
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--max-side", type=int, default=0,
                    help="downscale long side before upload (0 = full res; 1280 cut recall to ~0.75)")
    ap.add_argument("--quality", type=int, default=85, help="JPEG quality of the uploaded image")
    ap.add_argument("--compact", action="store_true", help="experimental short-key output (untested)")
    ap.add_argument("--any-provider", action="store_true", help="let OpenRouter route freely (may hit flex tier)")
    args = ap.parse_args()

    image_path = Path(args.image)
    img = cv2.imdecode(np.fromfile(str(image_path), dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    elements, info = segment(img, args.model, args.max_side, args.quality, args.compact,
                             not args.any_provider, args.retries)
    usage = info["usage"]

    d = Path(args.dir)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{image_path.stem}__{args.model.replace('/', '_')}"
    (d / f"{stem}_raw.txt").write_text(info.pop("raw"), encoding="utf-8")
    (d / f"{stem}.json").write_text(json.dumps({
        "image": str(image_path), "width": w, "height": h, **info, "elements": elements,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    annotate(img, elements, d / f"{stem}_annotated.png")

    print(f"{'id':>3}  {'type':<12} {'tap':<3} {'bbox':<24} text / label")
    for e in elements:
        print(f"{e['id']:>3}  {e['type']:<12} {'y' if e['interactive'] else '':<3} {str(e['bbox']):<24} "
              f"{(e['text'] or '(' + e['label'] + ')')[:60]}")
    print(f"\n{args.model} via {info['provider']}: {len(elements)} elements in {info['latency_s']}s "
          f"(upload {info['upload_kb']} KB), tokens in/out {usage.get('prompt_tokens')}/"
          f"{usage.get('completion_tokens')}, cost ${usage.get('cost', 0):.5f} -> {d.resolve()}")


if __name__ == "__main__":
    main()
