# backend_new — tap-to-flow pipeline as a FastAPI service

Takes an Android screen recording (made with *Show taps* on) plus its TapScreenRecorder tap log,
and returns `grounded_flow.json`: the ordered UI steps, each tap matched to the element under it.

This is `tap-to-flow-pipeline/` with **only stage 3 (UI segmentation) replaced**. The
OmniParser YOLO + EasyOCR + combine step is now one vision-LLM call per frame through
OpenRouter (`google/gemini-2.5-flash-lite`, tested in `test_ui_segment_api/`).

```
video.mp4 + tap_log.json
  ▼ stage 1  tap_extractor    unchanged copy   -> taps.json, events_report.json, taps/*.png
  ▼ stage 2  flow_builder     unchanged copy   -> flow.json, flow_frames/*.png
  ▼ stage 3  app/segmentation NEW (LLM)        -> flow_frames/segment_out/<frame>_combined.json, _ocr.json
  ▼ stage 4  ground_flow      unchanged copy   -> grounded_flow.json
```

Stage 3 writes the same `_combined.json` / `_ocr.json` files stage 4 already reads from its
cache, so stage 4 runs unmodified. All before/after frames of tap steps are segmented in
parallel (`SEG_CONCURRENCY`).

## API

| Method | Path | What |
|---|---|---|
| GET | `/health` | Liveness + whether the OpenRouter key is set |
| POST | `/pipeline` | multipart `video` (.mp4) + `tap_log` (.json). Returns `202 {job_id, status_url}`. Add `?wait=true` to block and get the finished job |
| GET | `/pipeline/{job_id}` | `status` (queued/running/succeeded/failed), `stage`, `error`, and `result` when done (`?include_result=false` for status only) |
| GET | `/pipeline/{job_id}/files/{path}` | Any output file, e.g. `flow_frames/step_004_before.png`, `flow_frames/segment_out/step_004_before_combined_annotated.png`, `grounded_flow.json` |
| POST | `/segment` | multipart `image`: stage 3 on one screenshot (elements + text + boxes) |

Interactive docs at `/docs`. If `SERVICE_API_KEY` is set, send `X-API-Key: <value>` on every
call except `/health`.

`result` = `{"summary": {...}, "grounded_flow": [...steps]}`. Summary has step counts,
grounding counts, review flags (log text vs grounded text), segmentation cost/retries and
per-stage timings. Frame paths in `grounded_flow` are relative to the run folder, i.e. usable
directly with the `/files/` endpoint.

```bash
curl -F video=@rec.mp4 -F tap_log=@rec.json https://<host>/pipeline          # -> {"job_id": "..."}
curl https://<host>/pipeline/<job_id>?include_result=false                   # poll
curl https://<host>/pipeline/<job_id>                                        # result when succeeded
```

## Run locally

```bash
cd backend_new
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # put your OpenRouter key in OPENROUTER_API_KEY
uvicorn app.main:app --reload
```

## Deploy on Render

`render.yaml` here is a Blueprint for this folder (`rootDir: backend_new`). In Render:
**New → Blueprint**, pick the repo, set the Blueprint file path to `backend_new/render.yaml`,
and enter `OPENROUTER_API_KEY` when asked. `SERVICE_API_KEY` is generated for you (see the
service's Environment tab). The repo-root `render.yaml` is the older `backend/` service; the
two are independent.

Notes for Render:
- Run **one worker** (as configured): jobs are tracked in the memory of the process that
  started them. Job state is also written to `runs/<job_id>/job.json`, but Render's disk is
  wiped on redeploy/restart unless you attach a persistent disk and point `RUNS_DIR` at it.
- Stage 1 decodes the whole video. The 55 MB / 1220×2712 Zomato recording worked locally;
  on a 512 MB instance, watch memory and move up a plan if the service restarts mid-job.
- Uploads are capped by `MAX_UPLOAD_MB` (default 300).

## Settings (env vars)

| Var | Default | |
|---|---|---|
| `OPENROUTER_API_KEY` (or `API_KEY`) | — | required |
| `SEG_MODEL` | `google/gemini-2.5-flash-lite` | any OpenRouter vision model |
| `SEG_CONCURRENCY` | 8 | frames segmented at once |
| `SEG_MAX_SIDE` | 0 | 0 = full resolution (1280 px lost ~25% recall in testing) |
| `SEG_JPEG_QUALITY` | 85 | upload encoding |
| `SEG_RETRIES` | 4 | per frame; failed frames also get a second sequential pass |
| `MAX_CONCURRENT_JOBS` | 1 | pipeline jobs at once |
| `MAX_UPLOAD_MB` | 300 | |
| `JOB_TTL_HOURS` | 24 | run folders older than this are deleted |
| `RUNS_DIR` | `./runs` | |
| `CALIBRATION_DIR` | bundled | stage 1 "Show taps" circle calibration (matches the sample phone) |
| `SERVICE_API_KEY` | empty | enables `X-API-Key` auth |

## Tested (Zomato recording, 55 MB, 14 tap steps, 28 frames)

- Succeeded in ~1 min 50 s: stage 1 36 s, stage 2 0.4 s, stage 3 73 s, stage 4 0.2 s.
- Segmentation cost **$0.031** for the whole video; 6 retries absorbed upstream cut-offs.
- Grounding agrees with the old YOLO+EasyOCR pipeline on most steps, with correct `₹` prices
  and real element types (button / text_field / link…). Differences: two taps now land on an
  image with no text (steps 2, 10), where the old pipeline had OCR text.

## Differences from the old stage 3 output

- `type` is the model's element type (button, text_field, chip, image…), not
  `labeled_element` / `icon_or_image` / `container`.
- Elements without text carry a `label` ("back arrow") in `_combined.json`; stage 4 copies only
  `text` into `grounded_element`, so that label is not in `grounded_flow.json`.
- `before_text` / `after_text` are the text of leaf elements (containers skipped), confidence 1.0.
- ~25% of model calls come back cut off upstream (`finish_reason=error`); they are retried
  automatically, which adds time.

## Keeping in sync

`pipeline_stages/` is a copy of stages 1, 2 and 4 from `tap-to-flow-pipeline/` (only the
files the pipeline imports, plus `calibration/`). Changes made there must be copied here.
