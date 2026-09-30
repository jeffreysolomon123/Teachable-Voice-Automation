"""Stage 3 (replacement): UI segmentation + OCR of a screenshot in ONE vision-LLM call via OpenRouter.

Replaces OmniParser YOLO + EasyOCR + combine_results. Ported from test_ui_segment_api/segment_api.py
with its tested defaults: full resolution, JPEG q85 upload, keyed-JSON output, Google's slow
"flex" tier skipped, max_tokens 6000, and retries on cut-off / errored upstream responses.

``write_stage3_outputs`` writes the two files stage 4 (ground_flow.py) reads from its cache, so
stage 4 runs unchanged:
  segment_out/<stem>_combined.json   elements (id, type, text, bbox, center, parent, ...)
  segment_out/<stem>_ocr.json        {"texts": [{"text", "bbox", "confidence"}]} in reading order
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .settings import settings

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SEGMENT_OUT = "segment_out"
MAX_TOKENS = 6000  # a good reply is ~3k tokens; the cap ends runaway repetition loops early
RETRYABLE_HTTP = {408, 409, 425, 429, 500, 502, 503, 504}
TYPES = ["button", "icon_button", "text_field", "tab", "chip", "checkbox", "toggle", "link",
         "image", "icon", "text", "card", "list_item", "nav_bar", "status_bar", "container"]
# Skip Google's flex tier (cheap but slow, queued) and prefer the highest-throughput endpoint.
FAST_PROVIDER = {"sort": "throughput", "ignore": ["google-ai-studio/flex"]}

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

# Box format each model family was trained on; asking in the native format gives the best boxes.
BOX_FORMATS = {
    "gemini": ("box_2d", "[ymin, xmin, ymax, xmax]", lambda b: [b[1], b[0], b[3], b[2]]),
    "default": ("bbox", "[x1, y1, x2, y2]", lambda b: b),
}


class SegmentationError(Exception):
    """The model call failed (after retries) or returned nothing usable."""


# --------------------------------------------------------------------------- model call

def encode_image(img: np.ndarray, max_side: int, quality: int) -> str:
    """Downscale so the long side is <= max_side (0 = keep) and JPEG-encode to base64.
    Boxes come back normalized 0-1000, so they map onto the original size unchanged."""
    h, w = img.shape[:2]
    if max_side and max(h, w) > max_side:
        f = max_side / max(h, w)
        img = cv2.resize(img, (round(w * f), round(h * f)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise SegmentationError("JPEG encoding failed")
    return base64.b64encode(buf.tobytes()).decode()


def _post(body: dict[str, Any]) -> dict[str, Any]:
    if not settings.openrouter_api_key:
        raise SegmentationError("OPENROUTER_API_KEY is not set")
    req = urllib.request.Request(
        OPENROUTER_URL, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {settings.openrouter_api_key}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def parse_json(content: str) -> tuple[list[dict[str, Any]], bool]:
    """Returns (element dicts, complete). Salvages whole elements from truncated output."""
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            data = data.get("elements", [])
        return [r for r in data if isinstance(r, dict)], True
    except json.JSONDecodeError:
        out = []
        for piece in re.findall(r"\{[^{}]*\}", content):
            try:
                out.append(json.loads(piece))
            except json.JSONDecodeError:
                pass
        return out, False


def _region(cx: int, cy: int, w: int, h: int) -> str:
    row = ["top", "middle", "bottom"][min(int(3 * cy / h), 2)]
    col = ["left", "center", "right"][min(int(3 * cx / w), 2)]
    return f"{row}-{col}"


def _contains(outer: list[int], inner: list[int], frac: float = 0.9) -> bool:
    ix1, iy1 = max(outer[0], inner[0]), max(outer[1], inner[1])
    ix2, iy2 = min(outer[2], inner[2]), min(outer[3], inner[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return area > 0 and inter / area >= frac


def to_elements(raw: list[dict[str, Any]], w: int, h: int, to_xyxy) -> list[dict[str, Any]]:
    """Model boxes (0-1000) -> pixel elements with the ui-segmentation *_combined.json fields."""
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
            "type": str(r.get("type", "")), "text": str(r.get("text") or ""),
            "label": str(r.get("label") or ""), "interactive": bool(r.get("interactive", False)),
            "bbox": bbox, "center": [cx, cy], "size": [bbox[2] - bbox[0], bbox[3] - bbox[1]],
            "bbox_norm": [round(x1 / 1000, 4), round(y1 / 1000, 4), round(x2 / 1000, 4), round(y2 / 1000, 4)],
            "center_norm": [round(cx / w, 4), round(cy / h, 4)],
            "area_pct": round(100 * (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) / (w * h), 2),
            "region": _region(cx, cy, w, h), "source": "llm",
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
                 and _contains(p["bbox"], e["bbox"])]
        if cands:
            p = min(cands, key=lambda p: p["size"][0] * p["size"][1])
            e["parent"] = p["id"]
            p["children"].append(e["id"])
    return uniq


def segment(img: np.ndarray, model: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Segment a BGR screenshot in one model call. Returns (elements, info).

    Retries network errors, retryable HTTP codes, and replies that are cut off
    (finish_reason=error, zero output tokens, bad JSON, or max_tokens hit).
    Raises SegmentationError when every attempt fails.
    """
    model = model or settings.seg_model
    h, w = img.shape[:2]
    is_gemini = "gemini" in model
    box_key, box_desc, to_xyxy = BOX_FORMATS["gemini" if is_gemini else "default"]
    prompt = (PROMPT.replace("BOX_KEY", box_key).replace("BOX_DESC", box_desc)
              .replace("TYPES", ", ".join(TYPES)))
    image_b64 = encode_image(img, settings.seg_max_side, settings.seg_jpeg_quality)
    body = {
        "model": model, "temperature": 0, "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"}, "usage": {"include": True},
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": prompt},
        ]}],
    }
    if is_gemini:
        body["provider"] = FAST_PROVIDER

    attempts = settings.seg_retries + 1
    last_error = "no attempt made"
    for attempt in range(1, attempts + 1):
        t0 = time.time()
        try:
            resp = _post(body)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            last_error = f"HTTP {e.code}: {detail}"
            if e.code not in RETRYABLE_HTTP:
                raise SegmentationError(last_error) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_error = f"network error: {e}"
        else:
            if "error" in resp:
                last_error = f"API error: {resp['error']}"
            else:
                choice = resp["choices"][0]
                finish = choice.get("finish_reason")
                content = choice["message"].get("content") or ""
                raw, complete = parse_json(content)
                elements = to_elements(raw, w, h, to_xyxy)
                usage = resp.get("usage") or {}
                # finish_reason=error / zero usage = stream cut off upstream; never accept it.
                upstream_failed = finish == "error" or not usage.get("completion_tokens")
                if complete and elements and not upstream_failed:
                    return elements, {
                        "model": model, "provider": resp.get("provider"),
                        "latency_s": round(time.time() - t0, 2), "attempts": attempt,
                        "upload_kb": len(image_b64) // 1024, "usage": usage,
                    }
                last_error = ("upstream error" if upstream_failed else
                              "hit max_tokens" if finish == "length" else "bad JSON") + \
                             f" (finish_reason={finish}, {len(elements)} elements)"
        log.warning("segment attempt %d/%d failed: %s", attempt, attempts, last_error)
        if attempt < attempts:
            time.sleep(min(2 * attempt, 6))
    raise SegmentationError(f"all {attempts} attempts failed; last: {last_error}")


# --------------------------------------------------------------------------- stage 3 outputs

def ocr_lines(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Text lines for *_ocr.json: leaf elements with text, in reading order.
    Containers/cards are skipped because their text repeats their children's."""
    return [{"text": e["text"], "bbox": e["bbox"], "confidence": 1.0}
            for e in elements if e["text"] and not e["children"]]


def annotate(img: np.ndarray, elements: list[dict[str, Any]], out_path: Path) -> None:
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


def read_image(path: Path) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SegmentationError(f"cannot decode image {path}")
    return img


def write_stage3_outputs(frame: Path, annotated: bool = True) -> dict[str, Any]:
    """Segment one frame and write segment_out/<stem>_combined.json + _ocr.json next to it."""
    img = read_image(frame)
    h, w = img.shape[:2]
    elements, info = segment(img)
    out_dir = frame.parent / SEGMENT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    header = {"image": str(frame), "width": w, "height": h}
    (out_dir / f"{frame.stem}_combined.json").write_text(json.dumps(
        {**header, **info, "elements": elements}, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / f"{frame.stem}_ocr.json").write_text(json.dumps(
        {**header, "texts": ocr_lines(elements)}, indent=2, ensure_ascii=False), encoding="utf-8")
    if annotated:
        annotate(img, elements, out_dir / f"{frame.stem}_combined_annotated.png")
    return {"frame": frame.name, "elements": len(elements), **info}


def segment_frames(frames: list[Path], concurrency: int | None = None) -> tuple[list[dict], list[str]]:
    """Run stage 3 on many frames in parallel. Returns (per-frame stats, error messages)."""
    stats, errors = [], []

    def one(f: Path):
        try:
            return write_stage3_outputs(f), None
        except Exception as exc:  # noqa: BLE001 - report every frame's failure, keep the others
            return None, f"{f.name}: {exc}"

    workers = max(1, min(concurrency or settings.seg_concurrency, len(frames) or 1))
    failed: list[Path] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for f, (stat, err) in zip(frames, pool.map(one, frames)):
            if err:
                failed.append(f)
            else:
                stats.append(stat)
    # Upstream cut-offs come in bursts under parallel load: give failed frames one more
    # full round of retries, one at a time, before giving up on them.
    for f in failed:
        log.warning("second pass for %s", f.name)
        stat, err = one(f)
        (errors.append(err) if err else stats.append(stat))
    return stats, errors
