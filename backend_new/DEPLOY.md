# Deploying backend_new to Render

Step-by-step guide to put the tap-to-flow backend (`backend_new/`) online on Render and check
that it works. Takes about 15 minutes, most of it waiting for the first build.

---

## What you need

- A **GitHub** account with access to the repo
  `jeffreysolomon123/Teachable-Voice-Automation`.
- A **Render** account (https://render.com). Sign up with GitHub so Render can see the repo.
- Your **OpenRouter API key** (the one in `test_ui_segment_api/.env`).
- No payment method is needed: `render.yaml` uses the **free** plan (see step 3).

---

## Step 1 — Check it runs locally (optional, 5 min)

Skip this if you already ran it.

```bash
cd backend_new
python -m venv .venv
.venv\Scripts\activate                 # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                 # macOS/Linux: cp .env.example .env
```

Open `.env` and set `OPENROUTER_API_KEY=<your key>`. Then:

```bash
uvicorn app.main:app --port 8000
```

Open http://localhost:8000/health. You should see `"openrouter_key_configured": true`.
Stop the server with Ctrl+C.

---

## Step 2 — Push the code to GitHub

Render deploys from GitHub, so `backend_new/` must be pushed. `.env`, `.venv/` and `runs/`
are gitignored, so **your API key is not uploaded**.

```bash
cd C:\Jeffrey\Projects\teachable-agentic-ai
git status                          # backend_new/ should be listed as new
git check-ignore backend_new/.env   # must print the path (= ignored). If it prints nothing, STOP.
git add backend_new
git commit -m "Add backend_new: tap-to-flow pipeline API with OpenRouter UI segmentation"
git push origin main
```

On GitHub, confirm that `backend_new/` is there and that there is **no** `backend_new/.env`.

---

## Step 3 — Pick a plan

`render.yaml` uses `plan: free`. Options:

| Plan | RAM | Notes |
|---|---|---|
| `free` | 512 MB | **Current setting.** Sleeps after 15 min idle (first request then takes ~1 min to wake). |
| `starter` | 512 MB | Always on, paid. |
| `standard` | 2 GB | Use this if jobs crash with out-of-memory (stage 1 decodes the whole video). |

To change it, edit `plan:` in `backend_new/render.yaml`, then commit and push again (step 2).

---

## Step 4 — Create the service from the Blueprint

1. Log in to https://dashboard.render.com.
2. Click **New +** → **Blueprint**.
3. **Connect** the repository `Teachable-Voice-Automation`. If it isn't listed, click
   *Configure account* and give Render access to it on GitHub.
4. Fill in:
   - **Blueprint Name:** `tap-to-flow-backend` (any name).
   - **Branch:** `main`.
   - **Blueprint Path:** `backend_new/render.yaml`
     ⚠️ Don't leave the default `render.yaml`: that's the **old** `backend/` service.
5. Render shows one service to create: **tap-to-flow-backend**.
6. It asks for the secret values:
   - **`OPENROUTER_API_KEY`**: paste your OpenRouter key.
   - `SERVICE_API_KEY` is generated automatically; nothing to enter.
7. Click **Apply** / **Deploy Blueprint**.

---

## Step 5 — Wait for the build

1. Open the service **tap-to-flow-backend** in the dashboard → **Logs**.
2. The first build installs OpenCV, PyAV, FastAPI and so on; it takes about 3–6 minutes.
3. It's live when the logs show:
   ```
   Uvicorn running on http://0.0.0.0:10000
   ```
   and the status at the top turns **Live**.
4. Copy the service URL from the top of the page, e.g.
   `https://tap-to-flow-backend.onrender.com`.

If the build fails, see **Troubleshooting** below.

---

## Step 6 — Get the API key clients must send

Every endpoint except `/health` needs the header `X-API-Key`.

1. Service → **Environment**.
2. Find **`SERVICE_API_KEY`** and click the eye icon to reveal the value.
3. Save it; below it is written as `<SERVICE_KEY>`.

---

## Step 7 — Verify the deployment

Replace `<URL>` and `<SERVICE_KEY>` with your values. Run the commands from the repo root
(Git Bash, or any terminal with `curl`).

**a) Health check (no key needed):**

```bash
curl <URL>/health
```

Expected output:

```json
{"status":"ok","segmentation_model":"google/gemini-2.5-flash-lite","openrouter_key_configured":true}
```

If `openrouter_key_configured` is `false`, set the key in **Environment** (step 8).

**b) Segment one screenshot (about 10–25 s):**

```bash
curl -H "X-API-Key: <SERVICE_KEY>" \
     -F "image=@tap-to-flow-pipeline/runs/zomato_new_test_v3/taps/tap_017_before.png" \
     <URL>/segment
```

Expected: JSON with `"width": 1220`, `"height": 2712` and about 50 `elements`.

**c) Full pipeline on the sample recording (about 2 min):**

```bash
curl -H "X-API-Key: <SERVICE_KEY>" \
     -F "video=@tap-to-flow-pipeline/zomato_new_test.mp4" \
     -F "tap_log=@tap-to-flow-pipeline/zomato_new_test.json" \
     <URL>/pipeline
```

Response: `{"job_id": "3c2ff923ad62", "status": "running", ...}`. Poll it every few seconds:

```bash
curl -H "X-API-Key: <SERVICE_KEY>" "<URL>/pipeline/<job_id>?include_result=false"
```

`stage` goes `stage1_tap_extraction` → `stage3_segmentation` → `done`, and `status` ends
at `succeeded`. Then fetch the result:

```bash
curl -H "X-API-Key: <SERVICE_KEY>" <URL>/pipeline/<job_id> -o result.json
```

Check that `result.summary` shows `"tap_steps": 14` and `"frames_segmented": 28`.

**d) Look at an annotated frame in the browser.** The browser can't send the header, so use curl:

```bash
curl -H "X-API-Key: <SERVICE_KEY>" -o frame.png \
  "<URL>/pipeline/<job_id>/files/flow_frames/segment_out/step_004_before_combined_annotated.png"
```

The interactive API docs are at `<URL>/docs`.

---

## Step 8 — Change settings later

Service → **Environment** → edit or add a variable → **Save Changes**. Render redeploys
automatically. Useful ones:

| Variable | Default | When to change |
|---|---|---|
| `OPENROUTER_API_KEY` | — | rotate the key |
| `SEG_MODEL` | `google/gemini-2.5-flash-lite` | try another OpenRouter vision model |
| `SEG_CONCURRENCY` | 8 | lower it if you hit OpenRouter rate limits |
| `SEG_RETRIES` | 4 | raise it if jobs fail with `finish_reason=error` |
| `MAX_CONCURRENT_JOBS` | 1 | keep 1 on 512 MB plans |
| `MAX_UPLOAD_MB` | 300 | larger recordings |
| `SERVICE_API_KEY` | generated | rotate the client key (clients must update too) |

---

## Step 9 — Deploy updates

Any push to `main` that changes files in `backend_new/` redeploys automatically:

```bash
git add backend_new
git commit -m "..."
git push origin main
```

Watch progress under **Events** / **Logs**. To turn this off: Service → **Settings** →
**Auto-Deploy** → *No*, then use **Manual Deploy** → *Deploy latest commit*.

---

## Step 10 — (Optional) Keep job results across restarts

By default, results live on the instance's disk, which is **wiped on every redeploy or
restart**, and in-memory jobs are lost if the process restarts. For clients that fetch results
right after a job finishes this doesn't matter. To keep them:

1. Service → **Disks** → **Add Disk** (paid plans only). Mount path: `/var/data`, size 1 GB+.
2. Service → **Environment** → add `RUNS_DIR=/var/data/runs`.
3. Finished jobs can then be read after restarts (`GET /pipeline/<job_id>` reads `job.json`
   from disk). Run folders older than `JOB_TTL_HOURS` (24) are deleted automatically.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Build fails on `pip install` | Check that the logs show Python **3.11.9** (`PYTHON_VERSION` env var). Re-run with **Manual Deploy → Clear build cache & deploy**. |
| Deploy says the health check failed | Look in Logs for a Python traceback at startup. Most often a typo in an env var value (e.g. a non-number in `SEG_CONCURRENCY`). |
| `401 missing or invalid X-API-Key` | Send `-H "X-API-Key: <SERVICE_KEY>"` with the value from step 6. |
| `/health` shows `openrouter_key_configured: false` | Set `OPENROUTER_API_KEY` in Environment and save. |
| Job `failed`: `stage3_segmentation failed ... upstream error` | OpenRouter/Google cut responses off. Resubmit the job, or raise `SEG_RETRIES` to 6. |
| Job `failed`: `stage3_segmentation ... HTTP 401/402` | Bad OpenRouter key (401) or no credit left (402). Check https://openrouter.ai/credits. |
| Job `failed`: `stage1_tap_extraction failed` | The video and log don't match, or the phone differs from the calibration. Re-run `tap-to-flow-pipeline/1_tap_extractor/calibrate.py` for that phone and replace `backend_new/pipeline_stages/tap_extractor/calibration/`. |
| Job stuck at `running`, then `404 job not found` | The instance restarted mid-job (usually out of memory: Logs show "Out of memory" or an instance restart). Move to a bigger plan (step 3). |
| `413 ... exceeds 300 MB` | Raise `MAX_UPLOAD_MB`. |
| First request takes ~1 min | Free plan waking up from sleep. Use `starter` to avoid it. |

---

## Cost reference

- **Render:** depends on the plan (see step 3).
- **OpenRouter:** about **$0.0011 per frame**, i.e. about **$0.03 for a 14-tap recording**
  (28 frames). Each job's summary reports its own cost in `segmentation_cost_usd`.
