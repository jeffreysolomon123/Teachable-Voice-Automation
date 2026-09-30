# Teachable Assistant: local demo guide

Everything runs on **this PC** and **the phone**. The only cloud calls are LLM calls, all through
one OpenRouter key: speech-to-text, the chat, screen reading (UI segmentation) and replay decisions.

```
Phone (Teachable Assistant app)                 PC (FastAPI server, port 8000)
  voice/chat UI (WebView)  ───────────────────►  /api/voice/*  assistant: STT + chat (OpenRouter)
  TEACH: screen video + tap log + plan  ──────►  /teach/recording  → tap-to-flow pipeline
        ◄──── learned flow + grounded_flow.json   (stage 3 = OpenRouter UI segmentation)
        saved on the phone (and on the PC)        flow saved in backend/local_flows/
  ORDER: learned flows synced ────────────────►  /flows, /flows/match, /replay/start
        screenshot each step  ────────────────►  /replay/step → /segment-style reading → TAP x,y
        ◄──── tap / type / swipe / back / ask
  Any screenshot  ────────────────────────────►  /segment  (elements + text + tap centers)
```

## 1. Start the server on the PC (every time)

Double-click `backend\start_server.bat` (or run it from a terminal). The first run creates a
Python environment and installs packages (a few minutes). It prints the PC's address, e.g.
`http://192.168.1.10:8000`. Keep the window open.

`backend\.env` must contain `OPENROUTER_API_KEY=...` (already set on this PC; template in
`backend\.env.example`).

## 2. Install and set up the app (once)

1. Copy `dist\TeachableAssistant-demo.apk` to the phone and install it (allow "install unknown
   apps"), or: `adb install -r dist\TeachableAssistant-demo.apk`.
2. Phone and PC on the **same Wi-Fi**.
3. Open **Teachable Assistant** → ⚙ → **Device setup**:
   - **Backend URL**: `http://192.168.1.10:8000` (default; change it if the PC's IP changed) → Save.
   - **Grant overlay permission**, **Allow microphone**.
   - **Enable accessibility service** → *Teachable Assistant visual automation* → On.
     (After reinstalling the app, turn it off and on again.)
   - **Show taps**: Settings → About phone → tap *Build number* 7× → Developer options →
     **Show taps** On. TEACH needs it: the server finds your taps by the touch circle in the video.
     Optional, over USB, lets the app switch it on/off by itself:
     `adb shell pm grant com.example.flowlaunchertest android.permission.WRITE_SECURE_SETTINGS`

## 3. TEACH (record once)

1. Say or type: **"Teach me how to order chicken tikka pizza from Pizza Hut on Zomato"**.
2. Pick **Teach Mode**, then **Confirm & Proceed**.
3. Allow screen recording (**Entire screen**). A 3-second countdown starts, then the app opens
   Zomato and a red **● REC** pill appears.
4. Do the task normally: search, open the restaurant, add the item… Stop before paying.
5. Tap **■ Stop** on the red pill. The assistant comes back and shows progress: uploading, finding
   your taps, reading every screen, writing the workflow (about 2 minutes for a 1-minute recording).
6. It says **"Learned … N steps … saved on this phone"**. The learned flow is stored:
   - on the phone: app files `flows/<flow_id>.json` (flow + grounded_flow.json), listed in Device setup;
   - on the PC: `backend/local_flows/<flow_id>.json`, and the full run in `backend/teach_runs/<job_id>/`
     (video, frames, `grounded_flow.json`, annotated segmentation images).

The items you named (e.g. restaurant, item) become placeholders, so one demonstration covers
other orders too.

## 4. ORDER (automate)

1. Say: **"Order a paneer pizza from Domino's on Zomato"** → **Order Mode** → **Confirm**.
2. The app sends its learned flows to the PC, picks the matching one, opens Zomato and repeats
   the steps with your new values. Each step it takes a screenshot, the server reads the screen and
   returns where to tap. The red/blue pill shows each step.
3. Before paying or placing an order it asks you (Confirm / Stop).

## 5. The standalone segmentation endpoint

`POST http://<PC>:8000/segment` with a screenshot (multipart field `file`) returns every UI element
with `type`, `text`, `label` (for icons), `interactive`, `bbox` and a tap `center`, from one
OpenRouter call. Try it in the browser at `http://<PC>:8000/docs`.

## Troubleshooting

| Problem | Fix |
|---|---|
| App shows "Can't reach the assistant server" | Server window open? Same Wi-Fi? URL in Device setup = the address the .bat printed. Windows may ask to allow Python on the network: allow it. |
| TEACH says "I couldn't learn… no taps were found" | Turn on **Show taps** and record again. |
| TEACH fails at stage 1 | The recording must come from the phone the calibration was made for (`backend/pipeline_stages/tap_extractor/calibration`). For a different phone, re-run `tap-to-flow-pipeline/1_tap_extractor/calibrate.py` and copy its output there. |
| "I don't have an executable workflow for that yet" | Teach it first (Device setup lists what the phone has learned). |
| Screen recording only showed the assistant | Choose **Entire screen** when Android asks. |
| Typing does nothing during ORDER | Text entry needs Android 13+. |
