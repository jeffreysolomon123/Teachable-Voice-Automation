# Visual UI agent backend

FastAPI service that drives Android apps **from screenshots only**. The Android client uploads a
screenshot; the backend segments it (OmniParser + EasyOCR), matches the current flow step's
semantic target against the detected elements, and returns one coordinate action. No
accessibility tree is used anywhere in element resolution.

```
Android ──screenshot──> /replay/step ──> segmentation provider ──> elements
                                           │
                        deterministic matcher ──ambiguous──> text LLM ──unsure──> vision LLM (set-of-marks)
                                           │
Android <──TAP x,y / TYPE / SWIPE / BACK / WAIT / ASK_USER / STOP──┘
```

## Voice assistant (merged)

The voice assistant in `assistant/voice_assistant_app` (Groq Whisper STT → orchestrator →
Edge TTS) is hosted by this service. `app/voice_integration.py` imports it at startup and:

- serves its API at `/api/voice/*`, its mobile UI at `/mobile/`, and its desktop UI at `/`
- registers every stored semantic flow in the assistant's workflow memory, so the assistant
  knows what the phone can actually execute
- writes replay outcomes (SUCCESS / PAUSED_AT_PAYMENT / STUCK_IN_EXECUTION / BLOCKED / FAILED /
  STOPPED) into the assistant's "last run" memory, so "did my order go through?" gets a real answer

End to end on the phone (`final_app`, "Teachable Assistant"):

```
speak -> WebView (<backend>/mobile/) -> /api/voice/process-audio -> REPLAY decision + plan
      -> AndroidBridge.startReplay(plan) -> /flows/match -> /replay/start -> step loop
      -> overlay + spoken progress; ASK_USER (payment gate etc.) -> Confirm / Stop
```

Set `VOICE_ASSISTANT_ENABLED=false` to run without it. `VOICE_DATA_DIR` moves the assistant's
memory out of the repository (tests set it to a temp dir).

**TEACH from voice is not wired up yet.** The assistant's TEACH mode only records the
conversation. Recording a demonstration on the phone is not built into the app. Flows still come
from the tap-to-flow pipeline (TapScreenRecorder recording → `grounded_flow.json` →
`POST /flows/teach`) or from `POST /flows`.

## Layout

| Path | What |
|---|---|
| `app/main.py` | `create_app()`, CORS, error handlers. Run with `uvicorn app.main:app` |
| `app/config.py` | Typed settings (env vars / `.env`) |
| `app/errors.py` | `{"error": {"code", "message", "retryable"}}` for every failure |
| `app/api/routes.py` | All endpoints (thin) |
| `app/models/` | Pydantic models: slots, flow, segment/element, action, replay, teach |
| `app/services/segmentation/` | Pluggable providers: `hf_space` (default), `local`, `http`; `normalize()` |
| `app/services/matching_service.py` | Tier 0 deterministic matcher |
| `app/services/llm_service.py` | Groq client + `disambiguate_element`, `vision_resolve_element`, `match_flow`, `screen_readiness`, `parse_slots`, `describe_teach_steps` (all schema-validated) |
| `app/services/resolver.py` | Tier escalation; coordinates always from a visual source |
| `app/services/safety_service.py` | Payment/final-order gate, blocker detection |
| `app/services/replay_service.py` | Explicit replay state machine (`TRANSITIONS` table) |
| `app/services/teach_service.py` | `grounded_flow.json` → semantic flow (vision evidence only) |
| `app/storage/flow_store.py` | One JSON file per flow in `FLOWS_DIR` |
| `flows/` | Flows shipped with the service (`order_food_zomato.json`) |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness (`{"status": "ok"}`) |
| GET | `/health/dependencies` | Provider/LLM configuration status (no secrets, no network calls) |
| POST | `/slots/validate` | Validate Slot JSON; missing / low-confidence slots; semantic task |
| POST | `/slots/parse` | Raw request → slots (text LLM) |
| POST | `/flows` | Store a flow (`?overwrite=true` to replace) |
| GET | `/flows`, `/flows/{id}` | List / fetch flows |
| POST | `/flows/match` | Slot JSON → best stored flow (deterministic, LLM if ambiguous) |
| POST | `/flows/teach` | `grounded_flow.json` + teach-time slots → semantic flow |
| POST | `/segment` | multipart `file` → normalized elements |
| POST | `/match` | target + elements → element index + center |
| POST | `/replay/start` | `{flow_id, slots, session_id?}` → first action |
| POST | `/replay/step` | multipart `session_id`, `screenshot`, `last_action_result` → next action |
| POST | `/replay/confirm` | `{session_id, confirmed, confirmation_id}` answers an ASK_USER |
| POST | `/replay/stop` | End a session |

Action coordinates are pixels of the uploaded screenshot; each coordinate action carries
`screen_width`/`screen_height` so the client can scale to its display.

## Replay behaviour

- **Resolution tiers:** deterministic (exact / normalized / substring / token / fuzzy text, role
  keywords, slot values, region hints, anchor proximity for "Add button for {{item}}", wrapped
  two-line titles) → text LLM only when candidates are ambiguous or on a retry → vision LLM
  last. Nothing matching on a first look triggers a cheap re-capture before any LLM call.
- **Vision tier = set-of-marks:** the model gets the screenshot plus the detected boxes and
  answers with a box index, so the tap point is a segmentation box center. Free x/y is a
  validated fallback. (Tested with `qwen/qwen3.8-27b`: it picked the right box for an
  icon-only target, while its raw coordinates were off by 100+ px.)
- **Verification** on the next screenshot: explicit `verify.anchors`, else typed text visible
  (TYPE), else screen changed / tapped label gone / next target visible (TAP).
- **Recovery:** attempt 1 re-capture, 2 re-resolve with the text LLM, 3 with the vision LLM,
  then `ASK_USER` (`MAX_RETRIES`). `optional` steps are skipped instead;
  `scroll_search` targets swipe up to `MAX_SCROLL_ATTEMPTS` times.
- **Blockers** (OCR text): permission dialogs (notification ones declined, location ones asked),
  login walls, payment/PIN screens, errors (tap Retry), unavailable restaurant, update prompts,
  popups (only negative labels such as "Not now"/"Skip" are ever auto-tapped, never "OK").
- **Payment gate (code, not prompt):** a TAP on "Place order"/"Pay…"/"Confirm payment"/…,
  a `place_order` role, a step with `final_confirmation: true`, or a swipe-to-pay swipe becomes
  `ASK_USER` with a `confirmation_id`. Only `/replay/confirm` with that id releases it, once, for
  the same step and the same label (a changed amount asks again), within
  `CONFIRMATION_TTL_SECONDS`.
- **Sessions** live in memory: run a single instance (Render's default).

## Run locally

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt                  # + requirements-local-cv.txt for SEGMENTATION_PROVIDER=local
cp .env.example .env                                 # fill in GROQ_API_KEY, HF_SPACE / provider
uvicorn app.main:app --host 0.0.0.0 --port 8000
pytest
```

Android phone on USB: `adb reverse tcp:8000 tcp:8000`, then use `http://127.0.0.1:8000` as the
backend URL in the app (cleartext is allowed only for 127.0.0.1/localhost/10.0.2.2).

## Segmentation providers

`SEGMENTATION_PROVIDER` selects one; each implements `segment(bytes, w, h) -> RawSegmentation`
in `app/services/segmentation/`. To add another (a different Space, a GPU server, a hosted
API), write a class with `name`, `segment()` and `status()` and register it in `build_provider()`.

| Provider | Needs | Notes |
|---|---|---|
| `hf_space` | `HF_SPACE` (+ `HF_TOKEN` if private) | Default; fits Render's small plans. Free ZeroGPU quota ≈ a few minutes of GPU per day |
| `http` | `SEGMENTATION_HTTP_URL` | Any server with `cloud_gpu_ui_segmentation/server.py`'s `/segment` contract |
| `local` | `requirements-local-cv.txt`, ~2-3 GB RAM | Imports the existing `tap-to-flow-pipeline/3_ui_segmentation` scripts; ~8 s/screenshot on CPU once loaded |

## Deploy to Render

**Blueprint (recommended):** `render.yaml` at the repo root defines a Python web service with
`rootDir: backend`, `pip install -r requirements.txt`, and
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `/health`.

1. Render dashboard → New → Blueprint → pick this repo.
2. Fill the `sync: false` variables: `GROQ_API_KEY`, `TEXT_MODEL`, `VISION_MODEL`, `HF_SPACE`,
   and `HF_TOKEN` if the Space is private.
3. Deploy, then open `https://<service>.onrender.com/health/dependencies`.
4. Put `https://<service>.onrender.com` in the Android app's backend URL field.

**Docker (for `local` segmentation on a ≥2 GB plan):** New → Web Service → Docker, with
Dockerfile path `backend/Dockerfile`, Docker context `.` (repo root), build arg `LOCAL_CV=1`,
and env var `SEGMENTATION_PROVIDER=local`.

Flows created at runtime (`POST /flows`, `/flows/teach?save`) live on the instance's disk,
which Render wipes on deploy unless a persistent disk is mounted at `FLOWS_DIR`. Flows
committed to `backend/flows/` are always present.

## Known limits

- Groq's free tier limits the vision model to about 7k input tokens/min, which is about 2
  vision calls a minute.
- The Space quota and cold starts dominate latency (a warm call takes ~7-9 s per screenshot).
- TEACH: `type_text` values in `grounded_flow.json` still come from the recorder's keyboard
  log. Element identification is vision-only; the log's accessibility bounds are ignored.
