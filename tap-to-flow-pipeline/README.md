# Tap-to-flow pipeline

Turns an Android screen recording (made with *Show taps* on, plus the TapScreenRecorder
tap log) into an ordered, vision-grounded list of UI steps.

```
recording.mp4 + recording.json (tap log)
   │
   ▼ 1_tap_extractor     extract_taps.py --log     -> taps.json, events_report.json, taps/*.png
   ▼ 2_flow_builder      flow_builder_cli.py       -> flow.json, flow_frames/*.png
   ▼ 3_ui_segmentation   (called by stage 4)       -> flow_frames/segment_out/*_combined.json
   ▼ 4_ground_flow       ground_flow_cli.py        -> grounded_flow.json
```

| Stage | Folder | What it does |
|---|---|---|
| 1 | `1_tap_extractor/` | Finds each tap's exact touch frame and finger point in the video, using the log to know when and where to look. Writes before/touch/after frames. See its `README.md` and `ARCHITECTURE.md`. |
| 2 | `2_flow_builder/` | Merges runs of keystrokes into `type_text` steps (and adds back keystrokes the video missed, using the log). Other taps become `tap` steps. Marks `window_change` taps `element_trustworthy: false`. Copies each step's frames into `flow_frames/`. |
| 3 | `3_ui_segmentation/` | Screenshot → UI elements (YOLO/OmniParser boxes + EasyOCR text + containment hierarchy). See its `README.md`. `segment_batch.py` runs all three scripts on many frames with the models loaded once; stage 4 uses it. |
| 4 | `4_ground_flow/` | For each `tap` step, segments its `before_frame` and picks the element under the tap point (deepest, then smallest box containing it, or the nearest center as a fallback). Grounding is vision-only: the tap log's accessibility bounds (`element.bounds`) are not used to pick the element. Adds the result as `grounded_element` next to the original `element`, and flags steps where the two texts disagree. Every tap step also gets `before_text` / `after_text`: the OCR'd text lines of its before/after frame (after frames are only OCR'd, via `segment_batch.py --ocr-only`). |

All stages are plain data/CV transforms: no LLM calls, no network (apart from the
one-time download of the OmniParser weights from Hugging Face in stage 3).

## Install

```
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## Run (from this folder)

```
# 1. taps (calibration/ matches the phone the sample recordings came from;
#    re-run 1_tap_extractor/calibrate.py for a different phone or resolution)
python 1_tap_extractor/extract_taps.py rec.mp4 --log rec.json --out runs/rec --calibration 1_tap_extractor/calibration

# 2. steps
python 2_flow_builder/flow_builder_cli.py runs/rec/taps.json runs/rec/events_report.json --out runs/rec/flow.json

# 3 + 4. grounding (runs stage 3 itself in one batch; ~15 s per frame on CPU, results cached)
python 4_ground_flow/ground_flow_cli.py runs/rec/flow.json --out runs/rec/grounded_flow.json --segmentation-dir 3_ui_segmentation
```

Stage 4 options: `--only-untrustworthy` grounds only `element_trustworthy == false` steps,
`--force-rerun` ignores cached segmentation, and `--python PATH` picks the interpreter that runs
stage 3 (it needs ultralytics and easyocr; the default is the current one).

### Cloud GPU segmentation (optional)

`--cloud SPACE` runs stage 3 on a Hugging Face ZeroGPU Space (see
`../cloud_gpu_ui_segmentation/hf_space/`) via `3_ui_segmentation/cloud_segment.py` instead of
locally, and writes the same `segment_out/` files, so everything downstream is unchanged. Needs
only `gradio_client`. Frames are uploaded as full-resolution JPEG q90 (~4x smaller than PNG; tap
grounding was identical in testing). If the Space has the `/segment_batch` endpoint, before and
after frames are split into `--cloud-concurrency` (default 4) batches, one GPU call each, all in
flight at once; older Spaces fall back to one `/segment_ui` request per frame.

```
$env:HF_TOKEN = "hf_..."     # bash: export HF_TOKEN=hf_...   (never put it on the command line)
python 4_ground_flow/ground_flow_cli.py runs/rec/flow.json --out runs/rec/grounded_flow.json --segmentation-dir 3_ui_segmentation --cloud user/ui-segmentation
```

Zomato recording, 14 tap steps: 100 s on the cloud (14 before + 14 after frames) vs 215 s locally
on CPU. Per request it's ~7-9 s warm (upload + GPU queue), so the gain comes from parallelism.
A free HF account's daily ZeroGPU quota is only a few minutes of GPU time, which is roughly
one run like this; after that, requests fail with "exceeded your free ZeroGPU quota" until it
resets. Space IDs are called through `https://<user>-<name>.hf.space`, which also works where
`huggingface.co` itself is blocked. Frames are uploaded to whoever owns the Space.

## Outputs

- **`flow.json`**: list of steps, each with `step_index` and `type`:
  - `type_text`: `text`, `start_ms`/`end_ms`, `before_frame`/`after_frame`,
    `source_tap_ids`, `deleted_existing_chars`, `field_text_before`/`field_text_after`,
    `characters[]` (each with `action`: type/delete/set_field, and `reconstructed_from_log`).
  - `tap`: `tap_x`/`tap_y`, `touch_ms`/`lift_ms`, `before_frame`/`touch_frame`/`after_frame`,
    `source_tap_id`, `source`, `element` (from the log), `element_trustworthy`.

  The full schema is in `1_tap_extractor/README.md`, section 4.
- **`grounded_flow.json`**: `flow.json` plus, on each grounded `tap` step:
  `grounded_element: {id, type, text, bbox, center, grounded_confidence: "contained" | "fallback_nearest"}`.
  Frame paths are relative to the JSON file. `runs/` is git-ignored.

## Origin

Copied from `show-taps-screen-recorder-script/`, `ui-segmentation/` and `ground-flow/`.
Those folders still exist. Edit here from now on, or the copies will drift apart.
