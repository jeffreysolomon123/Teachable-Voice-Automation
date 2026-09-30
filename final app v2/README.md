# Teachable Voice Automation (v2) - Visual UI Automation System

This directory contains the complete **v2** implementation of the Teachable Voice Automation system, incorporating pure visual UI automation, multi-provider cloud vision segmentation, native Android audio recording, and automated replay execution.

---

## Architecture Overview

```
USER VOICE / TEXT COMMAND
            │
            ▼
    SLOT JSON (Intent Engine)
            │
            ▼
   ANDROID APP (WebView + Native Bridge)
            │ (HTTP REST)
            ▼
     FASTAPI BACKEND
  ┌────────────────────────────────────────────────────────┐
  │ • Slot & Intent Resolution                             │
  │ • Multi-Provider Vision Pool (Groq Vision + Gemini)     │
  │ • Semantic & Normalized Element Matching               │
  │ • LLM Disambiguation (Groq / Gemini)                   │
  │ • Replay State Machine & Coordinate Calculation        │
  └────────────────────────────────────────────────────────┘
            │
            ▼
    COORDINATE ACTIONS (Tap / Swipe / Back / Text)
            │
            ▼
ANDROID ACCESSIBILITY GESTURE DISPATCHER
(Zero Accessibility Tree dependency — coordinate gestures only)
            │
            ▼
    NEW SCREENSHOT CAPTURE ───► FASTAPI (Verification Loop)
```

---

## Key Improvements in v2

1. **Zero Accessibility Tree Dependency for UI Element Resolution**:
   - The UI is resolved entirely visually using screenshots, bounding-box segmentation, and OCR.
   - The `AccessibilityService` is used **only** for low-level coordinate gesture injection (`dispatchGesture` for taps and swipes) without inspecting or reading `AccessibilityNodeInfo`.

2. **High-Throughput Vision Segmentation Pool (`SEGMENTATION_PROVIDER=pool`)**:
   - Replaced heavy local GPU / Hugging Face ZeroGPU cold-start bottlenecks with a resilient multi-provider cloud pool.
   - **Groq Vision LPUs** (`qwen/qwen3.8-27b` @ 30 RPM, <1.2s latency)
   - **Gemini Flash-Lite** (`gemini-3.1-flash-lite` @ 15 RPM)
   - Combined 45 RPM capacity with automatic rate-limit (429) failover.

3. **Native Android Microphone Capture (`AndroidBridge`)**:
   - Bypasses Chromium's insecure HTTP origin restrictions (`navigator.mediaDevices.getUserMedia` blocked on `http://192.168.x.x`).
   - Uses native Android `AudioRecord` (16 kHz mono PCM) converted directly to 44-byte RIFF WAV Base64 delivered to the frontend JavaScript.

4. **Replay Engine Integration**:
   - Automatically executes confirmed slot plans when the voice assistant identifies a replay intent.
   - Streams live progress and execution badges back into the voice assistant chat timeline.

---

## Directory Structure

```
final app v2/
├── app/                  # Android client application source code
│   └── src/main/java/com/example/flowlaunchertest/
│       ├── MainActivity.kt        # Host WebView, JavaScript AndroidBridge, permission handling
│       ├── AudioRecorderHelper.kt # Native 16 kHz PCM -> WAV audio recorder
│       ├── ReplayRunner.kt        # Replay loop: screenshot -> backend -> gesture dispatch
│       ├── TouchService.kt        # Accessibility gesture dispatcher (dispatchGesture)
│       ├── MediaProjectionService.kt # Virtual display screenshot capture
│       └── SetupActivity.kt       # Diagnostic setup & manual test triggers
├── backend/              # FastAPI automation & segmentation server
│   ├── app/
│   │   ├── services/segmentation/ # Pool, Groq Vision, Gemini Vision, HF Space providers
│   │   ├── routers/               # Replay, flows, segmentation, health endpoints
│   │   └── config.py              # Application settings
│   ├── tests/                     # Pytest automated test suite
│   ├── requirements.txt
│   └── .env.example
├── assistant/            # Teachable voice assistant engine & mobile UI
│   ├── mobile_assistant_app/      # Lightweight mobile-optimized frontend
│   └── voice_assistant_app/       # Dialogue manager, slot filler, and workflow registry
├── gradle/               # Gradle distribution wrapper
├── build.gradle.kts      # Project build configuration
└── settings.gradle.kts   # Project module settings
```

---

## Quickstart Guide

### 1. Backend Setup

```bash
cd backend

# Create virtual environment and install dependencies
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt

# Configure environment
cp .env.example .env
```

Edit `.env` and set your API keys:
```env
GROQ_API_KEY=gsk_...
GEMINI_API_KEY=AQ...
SEGMENTATION_PROVIDER=pool
```

Start the backend daemon:
```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Verify backend health:
```bash
curl http://localhost:8000/health
```

---

### 2. Building & Installing the Android APK

#### Option A: Android Studio
1. Open Android Studio.
2. Select **Open** and choose this `final app v2` folder.
3. Wait for Gradle sync to complete.
4. Connect an Android device (or launch an emulator) with USB debugging enabled.
5. Click **Run 'app'** or select **Build > Build Bundle(s) / APK(s) > Build APK(s)**.

#### Option B: Command Line (Gradle)
```bash
# Windows
.\gradlew.bat assembleDebug

# Linux/macOS
./gradlew assembleDebug
```
The compiled APK will be at:
`app/build/outputs/apk/debug/app-debug.apk`

Install to your device via ADB:
```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

---

### 3. Device Setup & Permissions

Once the app is launched on your Android device:
1. **Grant Microphone Permission**: Prompts when tapping the microphone icon.
2. **Grant Screen Capture (MediaProjection)**: Prompt appears when starting replay or demonstration recording. Tap **Start Now** / **Allow**.
3. **Enable Accessibility Service**:
   - Go to Android Settings > Accessibility > Installed Apps / Downloaded Services.
   - Enable **FlowLauncherTest** (allows coordinate gesture dispatch).
4. **Connect to Backend**:
   - Tap the **Gear** icon in the top right of the app.
   - Enter your backend URL:
     - Real device on same Wi-Fi: `http://192.168.x.x:8000`
     - Android Emulator: `http://10.0.2.2:8000`
     - Via ADB reverse: `adb reverse tcp:8000 tcp:8000` -> `http://127.0.0.1:8000`
   - Tap **Save Settings**; the connection indicator should turn green.
