"""Segment many screenshots on a cloud GPU (the ui-segmentation Gradio Space), in parallel.

Drop-in for segment_batch.py: writes the same <stem>.json, <stem>_ocr.json and
<stem>_combined.json to out_dir, so stage 4 reads them unchanged. The Space runs the same
OmniParser + EasyOCR + containment merge (../../cloud_gpu_ui_segmentation/hf_space/app.py). It
always does full segmentation, so --ocr-only is accepted but still writes all files.

Frames are uploaded as full-resolution JPEG (--jpeg-quality 0 sends the original files), which
cuts upload size ~4x without changing pixel coordinates. If the Space has the /segment_batch
endpoint, frames are split into --concurrency batches, each segmented in a single GPU call, all
sent at once. Otherwise each frame is its own /segment_ui request (and gets an _annotated.png).

SPACE is a Space ID ("user/name") or its URL. IDs are called through their direct
https://<user>-<name>.hf.space URL, which works even where huggingface.co itself is blocked.
A private Space needs the HF_TOKEN environment variable (never pass tokens on the command line).

Usage:  python cloud_segment.py a.png b.png ... --space user/name [-o out_dir] [--concurrency 4]
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from gradio_client import Client, handle_file
from PIL import Image

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8", errors="replace")

ATTEMPTS = 3


def space_url(space: str) -> str:
    if space.startswith(("http://", "https://")):
        return space.rstrip("/")
    space = space.removeprefix("https://huggingface.co/spaces/")
    return f"https://{re.sub(r'[/_.]', '-', space.lower())}.hf.space"


def to_upload(image: Path, tmp: Path, quality: int) -> Path:
    if quality <= 0:
        return image
    out = tmp / f"{image.stem}.jpg"
    Image.open(image).convert("RGB").save(out, "JPEG", quality=quality)
    return out


def write_outputs(image: Path, out_dir: Path, data: dict, annotated: str | None = None) -> int:
    stem, w, h = image.stem, data["width"], data["height"]
    for suffix, key, out_key in (("", "elements", "elements"), ("_ocr", "texts", "texts"),
                                 ("_combined", "combined", "elements")):
        (out_dir / f"{stem}{suffix}.json").write_text(
            json.dumps({"image": str(image), "width": w, "height": h, out_key: data[key]},
                       indent=2, ensure_ascii=False), encoding="utf-8")
    if annotated and Path(annotated).is_file():
        shutil.copyfile(annotated, out_dir / f"{stem}_annotated.png")
    return len(data["combined"])


def with_retries(label: str, fn):
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return fn()
        except Exception as exc:
            # Out of ZeroGPU quota: retrying only burns time until the daily reset.
            if attempt == ATTEMPTS or "quota" in str(exc).lower():
                raise RuntimeError(f"{label}: {exc}") from exc
            print(f"  retry {attempt}/{ATTEMPTS - 1} for {label}: {exc}", file=sys.stderr, flush=True)
            time.sleep(2 * attempt)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("--space", required=True, help="Space ID (user/name) or URL")
    ap.add_argument("-o", "--out-dir", default="segment_out")
    ap.add_argument("--concurrency", type=int, default=4, help="requests in flight at once")
    ap.add_argument("--jpeg-quality", type=int, default=90, help="0 = upload the original files")
    ap.add_argument("--conf", type=float, default=0.05)
    ap.add_argument("--iou", type=float, default=0.1)
    ap.add_argument("--min-conf", type=float, default=0.3)
    ap.add_argument("--contain", type=float, default=0.6)
    ap.add_argument("--ocr-only", action="store_true", help="accepted for segment_batch.py compatibility")
    ap.add_argument("--crops", action="store_true", help="accepted for segment_batch.py compatibility; no crops written")
    args = ap.parse_args()

    url, token = space_url(args.space), os.environ.get("HF_TOKEN") or None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    params = dict(conf=args.conf, iou=args.iou, min_conf=args.min_conf, contain=args.contain)
    images = [Path(p).resolve() for p in args.images]
    workers = max(1, min(args.concurrency, len(images)))

    t0 = time.perf_counter()
    client = Client(url, token=token, verbose=False)
    batch_api = "/segment_batch" in client.view_api(print_info=False, return_format="dict")["named_endpoints"]
    print(f"connected to {url} in {time.perf_counter() - t0:.1f}s "
          f"({'batch' if batch_api else 'per-image'} mode)", flush=True)

    failed = 0
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            uploads = dict(zip(images, pool.map(lambda im: to_upload(im, tmp, args.jpeg_quality), images)))

            if batch_api:
                chunks = [images[i::workers] for i in range(workers)]

                def run_chunk(chunk: list[Path]) -> tuple[list[Path], list[dict], float]:
                    t = time.perf_counter()
                    res = with_retries(f"batch of {len(chunk)}", lambda: client.predict(
                        files=[handle_file(str(uploads[im])) for im in chunk], **params,
                        api_name="/segment_batch"))
                    res = json.loads(res) if isinstance(res, str) else res
                    if len(res) != len(chunk):
                        raise RuntimeError(f"Space returned {len(res)} results for {len(chunk)} frames")
                    return chunk, res, time.perf_counter() - t

                futures = [pool.submit(run_chunk, c) for c in chunks]
                done = 0
                for fut in as_completed(futures):
                    try:
                        chunk, results, dt = fut.result()
                    except Exception as exc:
                        failed += len(chunks[futures.index(fut)])
                        print(f"[{done}/{len(images)}] FAILED {exc}", flush=True)
                        continue
                    for im, data in zip(chunk, results):
                        done += 1
                        n = write_outputs(im, out_dir, data)
                        print(f"[{done}/{len(images)}] {im.name}: {n} elements (batch of {len(chunk)} in {dt:.1f}s)", flush=True)
            else:
                local = threading.local()

                def run_one(im: Path) -> tuple[int, float]:
                    if not hasattr(local, "c"):
                        local.c = Client(url, token=token, verbose=False)
                    t = time.perf_counter()
                    data, annotated = with_retries(im.name, lambda: local.c.predict(
                        image=handle_file(str(uploads[im])), **params, api_name="/segment_ui"))
                    data = json.loads(data) if isinstance(data, str) else data
                    if not data:
                        raise RuntimeError(f"{im.name}: Space returned an empty result")
                    return write_outputs(im, out_dir, data, annotated), time.perf_counter() - t

                futures = {pool.submit(run_one, im): im for im in images}
                for i, fut in enumerate(as_completed(futures), start=1):
                    try:
                        n, dt = fut.result()
                        print(f"[{i}/{len(images)}] {futures[fut].name}: {n} elements in {dt:.1f}s", flush=True)
                    except Exception as exc:
                        failed += 1
                        print(f"[{i}/{len(images)}] FAILED {exc}", flush=True)

    print(f"{len(images) - failed}/{len(images)} frames in {time.perf_counter() - t0:.1f}s", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
