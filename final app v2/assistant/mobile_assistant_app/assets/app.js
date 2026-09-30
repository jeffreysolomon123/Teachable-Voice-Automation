// Mobile Assistant Engine
let isRecording = false;
let mediaRecorder = null;
let audioChunks = [];
let audioContext = null;
let analyser = null;
let animFrameId = null;
let silenceTimer = null;
let currentAudio = null;
const SILENCE_THRESHOLD_MS = 1300;

// Persistent Settings & Individualized Mobile Session
let backendUrl = localStorage.getItem("backend_url") || "http://192.168.1.3:8000";
let isBackendConnected = false;
let mobileSessionId = localStorage.getItem("mobile_session_id");
if (!mobileSessionId) {
  mobileSessionId = "sess_m_" + Date.now().toString(36) + "_" + Math.random().toString(36).substr(2, 5);
  localStorage.setItem("mobile_session_id", mobileSessionId);
}

function updateConnectionBadge(connected, url) {
  isBackendConnected = connected;
  const dot = document.getElementById("connDot");
  const text = document.getElementById("connText");
  if (!dot || !text) return;
  if (connected) {
    dot.style.background = "#00e676";
    const cleanUrl = url.replace(/^https?:\/\//, "");
    text.textContent = cleanUrl;
    text.style.color = "#00e676";
  } else {
    dot.style.background = "#ff5252";
    text.textContent = "Offline (Local)";
    text.style.color = "#ff8a80";
  }
}

async function probeBackend(url) {
  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 1200);
    const res = await fetch(`${url}/api/voice/health`, { signal: ctrl.signal });
    clearTimeout(tid);
    if (res.ok) {
      const data = await res.json();
      if (data && data.status === "ok") return true;
    }
  } catch (e) {}
  return false;
}

async function autoDetectBackend() {
  const custom = localStorage.getItem("backend_url");
  const candidates = [];
  if (custom) candidates.push(custom);
  candidates.push("http://192.168.1.3:8000");
  candidates.push("http://10.0.2.2:8000");
  candidates.push("http://localhost:8000");
  candidates.push("http://127.0.0.1:8000");

  for (const url of candidates) {
    const ok = await probeBackend(url);
    if (ok) {
      backendUrl = url;
      localStorage.setItem("backend_url", url);
      updateConnectionBadge(true, url);
      console.log("[AutoDetect] Connected to live backend at:", url);
      return;
    }
  }
  updateConnectionBadge(false, backendUrl);
  console.warn("[AutoDetect] Live server offline. Using local intelligent orchestrator fallback.");
}

// Kick off auto-probe immediately
setTimeout(autoDetectBackend, 100);

let localDraftPlan = null;
let localLastCompletedPlan = null;
let localLearnedWorkflows = {
  "order_dominos_zomato": {
    "app_name": "Zomato",
    "trigger_phrases": ["order a margherita pizza from domino's on zomato", "margherita"],
    "default_slots": { "item": "Margherita pizza", "restaurant": "Domino's", "app": "Zomato" }
  }
};

function areThreeSlotsComplete(slots) {
  if (!slots) return false;
  const hasItem = !!(slots.item || slots.search_term);
  const hasApp = !!(slots.app || slots.app_name);
  const hasRest = !!(slots.restaurant || slots.venue || slots.store || slots.merchant || slots.seller || slots.brand);
  return hasItem && hasApp && hasRest;
}

function getAudioContext() {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioContext.state === "suspended") {
    audioContext.resume();
  }
  return audioContext;
}

function playEarcon(type) {
  try {
    const ctx = getAudioContext();
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);

    if (type === "LISTEN") {
      osc.type = "sine";
      osc.frequency.setValueAtTime(440, now);
      osc.frequency.exponentialRampToValueAtTime(660, now + 0.12);
      gain.gain.setValueAtTime(0.08, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.18);
      osc.start(now);
      osc.stop(now + 0.2);
    } else if (type === "PING") {
      osc.type = "triangle";
      osc.frequency.setValueAtTime(880, now);
      gain.gain.setValueAtTime(0.06, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.15);
      osc.start(now);
      osc.stop(now + 0.16);
    } else if (type === "HANDOFF") {
      osc.type = "sine";
      osc.frequency.setValueAtTime(554, now);
      osc.frequency.exponentialRampToValueAtTime(440, now + 0.25);
      gain.gain.setValueAtTime(0.12, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
      osc.start(now);
      osc.stop(now + 0.36);
    }
  } catch (err) {
    console.warn("Earcon error:", err);
  }
}

function setAppState(state) {
  document.body.className = `state-${state.toLowerCase()}`;
  const statusBadge = document.getElementById("statusBadge");
  const statusText = document.getElementById("statusText");

  const stateLabels = {
    idle: "Ava Ready",
    listening: "Listening...",
    thinking: "Understanding...",
    speaking: "Ava Speaking...",
    executing: "Running Order...",
    awaiting_demo: "Ready for Taps Demo",
    awaiting_mode: "Choose Mode: Teach or Order",
    awaiting_confirmation: "Confirm Execution Plan",
    awaiting_clarification: "Listening for reply..."
  };

  statusText.textContent = stateLabels[state.toLowerCase()] || state;
  const micBtn = document.getElementById("micBtn");
  if (state.toLowerCase().includes("listening") || state.toLowerCase().includes("awaiting")) {
    micBtn.classList.add("listening");
  } else {
    micBtn.classList.remove("listening");
  }
}

function stopCurrentSpeech() {
  if (currentAudio) {
    try {
      currentAudio.pause();
      currentAudio.currentTime = 0;
      currentAudio.src = "";
    } catch (e) {}
    currentAudio = null;
  }
  if (window.speechSynthesis) {
    try {
      window.speechSynthesis.cancel();
    } catch (e) {}
  }
}

function showThinkingIndicator() {
  removeThinkingIndicator();
  const container = document.getElementById("chatContainer");
  const bubble = document.createElement("div");
  bubble.id = "mobileThinkingBubble";
  bubble.className = "msg-bubble msg-assistant typing-indicator";
  bubble.innerHTML = `
    <span class="typing-dot"></span>
    <span class="typing-dot"></span>
    <span class="typing-dot"></span>
    <span style="font-size: 13px; color: var(--text-muted); margin-left: 8px;">Ava is thinking...</span>
  `;
  container.appendChild(bubble);
  requestAnimationFrame(() => {
    container.scrollTop = container.scrollHeight;
  });
}

function removeThinkingIndicator() {
  const existing = document.getElementById("mobileThinkingBubble");
  if (existing) existing.remove();
}

async function startNewSession() {
  stopCurrentSpeech();
  stopRecording();
  mobileSessionId = "sess_m_" + Date.now().toString(36) + "_" + Math.random().toString(36).substr(2, 5);
  localStorage.setItem("mobile_session_id", mobileSessionId);
  localDraftPlan = null;

  try {
    await fetch(`${backendUrl}/api/voice/session/new`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: mobileSessionId })
    });
  } catch (e) {}

  const container = document.getElementById("chatContainer");
  container.innerHTML = `
    <div class="msg-bubble msg-assistant">
      <span class="msg-badge badge-replay">FRESH SESSION INITIALIZED</span>
      <div>Hello! I'm your mobile voice assistant. Tell me what you'd like to do — I will extract the execution slots, verify whether you want <strong>TEACH mode</strong> (demonstrate on screen) or <strong>ORDER mode</strong> (autonomous execution), and confirm the plan with you before starting!</div>
    </div>
  `;
  container.scrollTop = 0;
  setAppState("idle");
  playEarcon("PING");
}

async function cancelActiveWorkflow() {
  stopCurrentSpeech();
  localDraftPlan = null;
  try {
    await fetch(`${backendUrl}/api/voice/session/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Session-ID": mobileSessionId },
      body: JSON.stringify({ session_id: mobileSessionId })
    });
  } catch (e) {}
  sendTextToServer("Cancel");
}

let isNativeRecording = false;

window.onNativeAudioRecorded = function(base64Wav) {
  isRecording = false;
  isNativeRecording = false;
  if (!base64Wav) {
    setAppState("idle");
    return;
  }
  try {
    const byteChars = atob(base64Wav);
    const byteNumbers = new Array(byteChars.length);
    for (let i = 0; i < byteChars.length; i++) {
      byteNumbers[i] = byteChars.charCodeAt(i);
    }
    const byteArray = new Uint8Array(byteNumbers);
    const audioBlob = new Blob([byteArray], { type: "audio/wav" });
    setAppState("thinking");
    playEarcon("PING");
    sendAudioToServer(audioBlob);
  } catch (err) {
    console.error("Native audio decoding error:", err);
    setAppState("idle");
  }
};

async function toggleMicrophone() {
  if (isRecording) {
    stopRecording();
  } else {
    await startRecording();
  }
}

async function startRecording() {
  stopCurrentSpeech();

  // Prefer native Android AudioRecord if available via AndroidBridge
  if (window.AndroidBridge && typeof window.AndroidBridge.startAudioRecording === "function") {
    try {
      const started = window.AndroidBridge.startAudioRecording();
      if (started) {
        isRecording = true;
        isNativeRecording = true;
        setAppState("listening");
        playEarcon("LISTEN");
        return;
      }
    } catch (e) {
      console.warn("AndroidBridge audio recording error:", e);
    }
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx = getAudioContext();
    const source = ctx.createMediaStreamSource(stream);
    analyser = ctx.createAnalyser();
    analyser.fftSize = 64;
    source.connect(analyser);

    audioChunks = [];
    let chosenMime = "audio/webm;codecs=opus";
    if (!MediaRecorder.isTypeSupported(chosenMime)) {
      chosenMime = MediaRecorder.isTypeSupported("audio/mp4") ? "audio/mp4" : "";
    }

    mediaRecorder = chosenMime ? new MediaRecorder(stream, { mimeType: chosenMime }) : new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = () => {
      stream.getTracks().forEach(t => t.stop());
      cancelAnimationFrame(animFrameId);
      if (audioChunks.length > 0) {
        const audioBlob = new Blob(audioChunks, { type: mediaRecorder.mimeType || "audio/webm" });
        sendAudioToServer(audioBlob);
      } else {
        setAppState("idle");
      }
    };

    mediaRecorder.start(250);
    isRecording = true;
    setAppState("listening");
    playEarcon("LISTEN");
    monitorAudioLevel();

    // Use Web Speech API if supported for real-time transcription
    startWebSpeechFallback();

  } catch (err) {
    console.error("Microphone error:", err);
    appendMessage("assistant", "Microphone access denied. Please grant permission in your browser or app settings.");
    setAppState("idle");
  }
}

let speechRec = null;
function startWebSpeechFallback() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return;

  try {
    speechRec = new SpeechRecognition();
    speechRec.continuous = false;
    speechRec.interimResults = true;
    speechRec.lang = "en-US";

    speechRec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; ++i) {
        if (e.results[i].isFinal) {
          const final = e.results[i][0].transcript.trim();
          document.getElementById("textInput").value = final;
          stopRecording();
          return;
        } else {
          interim += e.results[i][0].transcript;
        }
      }
      if (interim) {
        document.getElementById("textInput").value = interim;
        clearTimeout(silenceTimer);
        silenceTimer = setTimeout(() => {
          stopRecording();
        }, SILENCE_THRESHOLD_MS);
      }
    };

    speechRec.start();
  } catch (e) {}
}

function stopRecording() {
  if (isRecording) {
    isRecording = false;
    clearTimeout(silenceTimer);
    if (speechRec) {
      try { speechRec.stop(); } catch (e) {}
    }
    if (isNativeRecording && window.AndroidBridge && typeof window.AndroidBridge.stopAudioRecording === "function") {
      isNativeRecording = false;
      try {
        window.AndroidBridge.stopAudioRecording();
      } catch (e) {
        console.warn("Native stop audio recording error:", e);
      }
      playEarcon("PING");
      setAppState("thinking");
      return;
    }
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      mediaRecorder.stop();
    }
    playEarcon("PING");
    setAppState("thinking");
  }
}

function monitorAudioLevel() {
  if (!analyser || !isRecording) return;
  const data = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(data);

  let sum = 0;
  for (let i = 0; i < data.length; i++) sum += data[i];
  const avg = sum / data.length;

  if (avg > 20) {
    clearTimeout(silenceTimer);
    silenceTimer = setTimeout(() => {
      stopRecording();
    }, SILENCE_THRESHOLD_MS);
  }

  const wave = document.querySelector(".siri-bottom-wave");
  if (wave) {
    const scale = Math.min(1.8, Math.max(0.7, avg / 25));
    wave.style.transform = `scaleY(${scale}) translateY(-${(scale - 1) * 12}px)`;
  }

  animFrameId = requestAnimationFrame(monitorAudioLevel);
}

// Send Audio to Server
async function sendAudioToServer(blob) {
  stopCurrentSpeech();
  showThinkingIndicator();
  setAppState("thinking");

  const formData = new FormData();
  formData.append("file", blob, "voice_input.webm");
  formData.append("voice", "en-US-AvaMultilingualNeural");
  formData.append("session_id", mobileSessionId);

  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 12000);
    const res = await fetch(`${backendUrl}/api/voice/process-audio`, {
      method: "POST",
      headers: { "X-Session-ID": mobileSessionId },
      body: formData,
      signal: ctrl.signal
    });
    clearTimeout(tid);
    if (!res.ok) throw new Error("HTTP error " + res.status);
    const data = await res.json();
    updateConnectionBadge(true, backendUrl);
    removeThinkingIndicator();
    handleResponse(data);
  } catch (err) {
    removeThinkingIndicator();
    console.warn("Server unavailable for audio STT, using fallback:", err);
    updateConnectionBadge(false, backendUrl);
    const typed = document.getElementById("textInput").value.trim();
    if (typed) {
      document.getElementById("textInput").value = "";
      sendTextToServer(typed);
    } else {
      appendMessage("assistant", `I couldn't reach the server at ${backendUrl}. I'm listening in offline local mode. You can type or tap ⚙ to check your host PC's Wi-Fi IP.`);
      setAppState("idle");
    }
  }
}

function handleTextSubmit() {
  const input = document.getElementById("textInput");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  sendTextToServer(text);
}

async function sendTextToServer(text) {
  stopCurrentSpeech();

  // Voice Decision Button Locking: Block or replace buttons immediately if decision is detected
  const lower = text.toLowerCase().trim();
  if (lower.includes("teach") || lower.includes("record")) {
    lockAllPlanActionButtons("mode", "TEACH MODE");
  } else if (lower.includes("order") || lower.includes("replay") || lower.includes("execute")) {
    lockAllPlanActionButtons("mode", "ORDER MODE");
  } else if (lower.includes("yes") || lower.includes("proceed") || lower.includes("confirm") || lower.includes("start") || lower.includes("sure")) {
    lockAllPlanActionButtons("confirm");
  } else if (lower.includes("cancel") || lower.includes("stop") || lower.includes("abort")) {
    lockAllPlanActionButtons("cancel");
    hideFloatingTeachHud();
  } else if (lower.includes("finish") || lower.includes("done") || lower.includes("completed")) {
    hideFloatingTeachHud();
  }

  appendMessage("user", text);
  playEarcon("PING");
  showThinkingIndicator();
  setAppState("thinking");

  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 12000);
    const res = await fetch(`${backendUrl}/api/voice/process-text`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Session-ID": mobileSessionId
      },
      body: JSON.stringify({
        text,
        voice: "en-US-AvaMultilingualNeural",
        session_id: mobileSessionId
      }),
      signal: ctrl.signal
    });
    clearTimeout(tid);
    if (!res.ok) throw new Error("HTTP error " + res.status);
    const data = await res.json();
    updateConnectionBadge(true, backendUrl);
    removeThinkingIndicator();
    handleResponse(data);
  } catch (err) {
    removeThinkingIndicator();
    console.warn("Backend offline, executing mobile local turn:", err);
    updateConnectionBadge(false, backendUrl);
    handleLocalTurn(text);
  }
}

// Mobile Local Fallback Orchestrator (When server is unreachable)
function handleLocalTurn(text) {
  const lower = text.toLowerCase().trim();
  const isNewCommand = lower.startsWith("order a ") || lower.startsWith("order two ") || lower.startsWith("order 2 ") || lower.startsWith("get me ") || lower.startsWith("search for ") || lower.startsWith("teach: ") || lower.startsWith("teach me ") || lower.startsWith("buy ") || lower.startsWith("book ");

  // Step 0: Cancellation
  const cancelWords = ["cancel", "stop", "abort", "nevermind", "start over", "reset", "clear", "new session"];
  if (cancelWords.some(w => lower === w || lower.startsWith(w + " "))) {
    localDraftPlan = null;
    handleResponse({
      state: "IDLE",
      spoken_text: "Workflow cancelled. What would you like to do instead?",
      decision: { intent: "CONVERSE" }
    });
    return;
  }

  // Step 0.5: Demonstration completion ("Finish", "Done", "I'm done")
  const finishTriggers = ["finish", "done", "completed", "finish demonstration", "stop recording", "all done", "finished", "i'm done", "i am done", "save flow"];
  if (finishTriggers.some(w => lower === w || lower.startsWith(w + " "))) {
    const targetApp = (localDraftPlan && localDraftPlan.app_name) || "your app";
    if (localDraftPlan) {
      localLearnedWorkflows[localDraftPlan.flow_id || "order_dominos_zomato"] = {
        app_name: localDraftPlan.app_name,
        trigger_phrases: [localDraftPlan.summary],
        default_slots: localDraftPlan.slots
      };
      localDraftPlan.stage = "COMPLETED";
      localDraftPlan.status = "completed";
      localLastCompletedPlan = { ...localDraftPlan };
    }
    localDraftPlan = null;
    handleResponse({
      state: "COMPLETED",
      spoken_text: `Demonstration captured and saved! I've learned the steps for ${targetApp}. You can now execute it anytime in ORDER mode.`,
      decision: { intent: "CONVERSE" }
    });
    return;
  }

  // Step 0.6: High-priority out-of-band handlers (Status, out-of-domain, memory preferences)
  const postStage = localLastCompletedPlan ? "COMPLETED" : "IDLE";

  if (lower.includes("last run") || lower.includes("succeed")) {
    handleResponse({
      state: postStage,
      spoken_text: "The last run status was SUCCESS_UP_TO_PAYMENT, stopped at step payment_screen.",
      decision: { intent: "STATUS_QUERY" }
    });
    return;
  }

  if (lower.includes("remember that") || lower.startsWith("remember ")) {
    const prefKey = (lower.includes("office") || lower.includes("work")) ? "office_address" : "user_note";
    const prefVal = lower.includes("cyber city") ? "Cyber City" : text;
    handleResponse({
      state: postStage,
      spoken_text: `Understood! I'll remember that your ${prefKey.replace('_', ' ')} is ${prefVal}.`,
      decision: { intent: "CONVERSE" }
    });
    return;
  }

  if (["where is my office", "what is my office", "office address", "where do i work"].some(q => lower.includes(q))) {
    handleResponse({
      state: postStage,
      spoken_text: "Your office is located at Cyber City.",
      decision: { intent: "CONVERSE" }
    });
    return;
  }

  if (lower.includes("cab") || lower.includes("airport") || lower.includes("uber") || lower.includes("ola")) {
    localDraftPlan = null;
    handleResponse({
      state: "IDLE",
      spoken_text: "I haven't learned how to book a cab yet. Would you like to teach me?",
      decision: { intent: "UNKNOWN" }
    });
    return;
  }

  // Step 1: Conversational questions with strict non-regression after completion
  if (lower.includes("what is teach") || lower.includes("explain teach") || lower.includes("difference")) {
    handleResponse({
      state: localDraftPlan ? "AWAITING_MODE" : postStage,
      spoken_text: "In TEACH mode, you demonstrate the actions on your phone screen while I record and learn each tap and input. In ORDER mode, I autonomously execute a previously learned workflow for you. Which would you prefer?",
      decision: { intent: "AMBIGUITY_RESOLVE" }
    });
    return;
  }

  if (["thank you", "thanks", "great job", "awesome", "perfect", "good job"].some(g => lower.includes(g))) {
    handleResponse({
      state: postStage,
      spoken_text: "You're very welcome! Let me know whenever you're ready for your next order or demonstration.",
      decision: { intent: "CONVERSE" }
    });
    return;
  }

  if (["hello again", "hello ava", "hi ava", "hey ava"].some(g => lower.includes(g)) || ["hello", "hi", "hey"].includes(lower)) {
    handleResponse({
      state: postStage,
      spoken_text: "Hello! I'm Ava, your executive voice assistant. What workflow or task would you like to tackle today?",
      decision: { intent: "CONVERSE" }
    });
    return;
  }

  if (lower.includes("what can you do") || lower.includes("how does this work") || lower.includes("help") || lower.includes("capabilities")) {
    handleResponse({
      state: postStage,
      spoken_text: "I can learn and replay automated workflows across apps on your phone! You can teach me new flows by demonstrating them on your screen, or tell me to run flows like ordering food on Zomato or shopping on Amazon.",
      decision: { intent: "CONVERSE" }
    });
    return;
  }

  // If a new command arrived while an old incomplete plan was pending, discard the old plan
  if (isNewCommand && localDraftPlan && localDraftPlan.status !== "awaiting_confirmation") {
    localDraftPlan = null;
  }

  // Step 1.5: Reiteration on completed workflow
  if (!localDraftPlan && localLastCompletedPlan && !isNewCommand) {
    let isReiteration = false;
    const plan = JSON.parse(JSON.stringify(localLastCompletedPlan));
    plan.slots = plan.slots || {};

    if (lower.includes("farmhouse")) {
      isReiteration = true;
      plan.slots.item = "Farmhouse pizza";
      plan.summary = `Order Farmhouse pizza from ${plan.slots.restaurant || "Domino's"} on ${plan.app_name || "Zomato"}`;
    } else if (lower.includes("two") || lower.includes("2")) {
      isReiteration = true;
      plan.slots.quantity = 2;
      plan.summary = `Order 2 ${plan.slots.item || "Margherita pizza"}s from ${plan.slots.restaurant || "Domino's"} on ${plan.app_name || "Zomato"}`;
    } else if (lower.includes("work") || lower.includes("office")) {
      isReiteration = true;
      plan.slots.address = "Work";
      plan.summary = `${plan.summary}, deliver to Work`;
    }

    if (isReiteration) {
      plan.stage = "STAGE_3_EXECUTION";
      plan.status = "awaiting_confirmation";
      plan.confirmed = false;
      localDraftPlan = plan;
      handleResponse({
        state: "AWAITING_CONFIRMATION",
        spoken_text: `Updated your order plan: ${plan.summary}. Ready to proceed?`,
        decision: { intent: "CONFIRM_PLAN" },
        extracted_plan: plan
      });
      return;
    }
  }

  // =========================================================================
  // STAGED WORKFLOW ENGINE (STAGE_1_INTENT_MODE -> STAGE_2_SLOTS -> STAGE_3_EXECUTION)
  // =========================================================================
  if (localDraftPlan) {
    const currentStage = localDraftPlan.stage || "STAGE_1_INTENT_MODE";
    const status = localDraftPlan.status;

    // In-flight slot modification during confirmation
    if (status === "awaiting_confirmation" && !isNewCommand) {
      if (lower.includes("farmhouse")) {
        localDraftPlan.slots.item = "Farmhouse pizza";
        localDraftPlan.summary = `Order Farmhouse pizza from ${localDraftPlan.slots.restaurant || "Domino's"} on ${localDraftPlan.app_name || "Zomato"}`;
        handleResponse({
          state: "AWAITING_CONFIRMATION",
          spoken_text: `Updated item to Farmhouse pizza. Plan: ${localDraftPlan.summary}. Ready to proceed?`,
          decision: { intent: "CONFIRM_PLAN" },
          extracted_plan: localDraftPlan
        });
        return;
      }
      if (lower.includes("two") || lower.includes("2")) {
        localDraftPlan.slots.quantity = 2;
        localDraftPlan.summary = `Order 2 ${localDraftPlan.slots.item || "Margherita pizza"}s from ${localDraftPlan.slots.restaurant || "Domino's"} on ${localDraftPlan.app_name || "Zomato"}`;
        handleResponse({
          state: "AWAITING_CONFIRMATION",
          spoken_text: `Updated quantity to 2. Plan: ${localDraftPlan.summary}. Ready to proceed?`,
          decision: { intent: "CONFIRM_PLAN" },
          extracted_plan: localDraftPlan
        });
        return;
      }
    }

    // STAGE 1: Resolve Mode
    if (currentStage === "STAGE_1_INTENT_MODE" || status === "awaiting_mode") {
      let resolvedMode = null;
      if (lower.includes("teach") || lower.includes("record") || lower.includes("demo") || lower.includes("show")) {
        resolvedMode = "TEACH";
      } else if (lower.includes("order") || lower.includes("execut") || lower.includes("run") || lower.includes("replay")) {
        resolvedMode = "ORDER";
      }

      if (resolvedMode) {
        localDraftPlan.mode = resolvedMode;

        // INVARIANT CHECK: Cannot skip Stage 2 if the 3 canonical slots are missing!
        if (!areThreeSlotsComplete(localDraftPlan.slots)) {
          localDraftPlan.stage = "STAGE_2_SLOTS";
          localDraftPlan.status = "awaiting_slots";
          handleResponse({
            state: "AWAITING_SLOTS",
            spoken_text: "Which restaurant would you like to order from, Domino's or somewhere else?",
            decision: { intent: "AMBIGUITY_RESOLVE" },
            extracted_plan: localDraftPlan
          });
          return;
        } else {
          localDraftPlan.stage = "STAGE_3_EXECUTION";
          localDraftPlan.status = "awaiting_confirmation";
          const prompt = resolvedMode === "TEACH"
            ? `Great, TEACH mode selected for ${localDraftPlan.app_name}. Plan: ${localDraftPlan.summary}. Ready to start recording the demonstration?`
            : `Great, ORDER mode selected for ${localDraftPlan.app_name}. Plan: ${localDraftPlan.summary}. Shall I proceed with the order?`;
          handleResponse({
            state: "AWAITING_CONFIRMATION",
            spoken_text: prompt,
            decision: { intent: "CONFIRM_PLAN" },
            extracted_plan: localDraftPlan
          });
          return;
        }
      }
    }

    // STAGE 2: Specify The 3 Canonical Slots (item, restaurant/venue, app)
    if (currentStage === "STAGE_2_SLOTS" || status === "awaiting_slots") {
      localDraftPlan.slots = localDraftPlan.slots || {};

      if (lower.includes("teach") || lower.includes("record") || lower.includes("demonstrat")) localDraftPlan.mode = "TEACH";
      if (lower.includes("order") || lower.includes("execut") || lower.includes("replay")) localDraftPlan.mode = "ORDER";

      // Flexible known restaurant extraction
      const knownRestaurants = [
        ["domino", "Domino's"],
        ["pizza hut", "Pizza Hut"],
        ["subway", "Subway"],
        ["kfc", "KFC"],
        ["burger king", "Burger King"],
        ["mcdonald", "McDonald's"],
        ["starbucks", "Starbucks"],
        ["haldiram", "Haldiram's"],
        ["chipotle", "Chipotle"],
        ["taco bell", "Taco Bell"],
        ["bikanervala", "Bikanervala"]
      ];
      let restaurantFound = null;
      for (const [pat, canon] of knownRestaurants) {
        if (lower.includes(pat)) {
          restaurantFound = canon;
          break;
        }
      }

      // Regex fallback: "from <name>", "at <name>"
      if (!restaurantFound) {
        const m = lower.match(/\b(?:from|at|by)\s+([a-z0-9'\s]+?)(?:\s+on|\s+via|\s+in|$|\.)/i);
        if (m && m[1]) {
          const cand = m[1].trim();
          if (cand && !["teach", "order", "zomato", "swiggy", "amazon"].includes(cand.toLowerCase())) {
            restaurantFound = cand.split(" ").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
          }
        }
      }

      if (restaurantFound) {
        localDraftPlan.slots.restaurant = restaurantFound;
      }

      if (lower.includes("zomato")) { localDraftPlan.slots.app = "Zomato"; localDraftPlan.app_name = "Zomato"; }
      else if (lower.includes("swiggy")) { localDraftPlan.slots.app = "Swiggy"; localDraftPlan.app_name = "Swiggy"; }
      else if (lower.includes("amazon")) { localDraftPlan.slots.app = "Amazon"; localDraftPlan.app_name = "Amazon"; }
      else if (localDraftPlan.slots.restaurant && !localDraftPlan.app_name && !localDraftPlan.slots.app) {
        localDraftPlan.slots.app = "Zomato"; localDraftPlan.app_name = "Zomato";
      }

      if (lower.includes("margherita")) localDraftPlan.slots.item = "Margherita pizza";
      else if (lower.includes("farmhouse")) localDraftPlan.slots.item = "Farmhouse pizza";
      else if (lower.includes("pepperoni")) localDraftPlan.slots.item = "Pepperoni pizza";
      else if (lower.includes("pizza") && !localDraftPlan.slots.item) localDraftPlan.slots.item = "pizza";

      // Re-check invariant: All 3 slots must be complete to advance!
      if (!areThreeSlotsComplete(localDraftPlan.slots)) {
        const missing = [];
        if (!localDraftPlan.slots.item) missing.push("item");
        if (!localDraftPlan.slots.restaurant) missing.push("restaurant");
        if (!localDraftPlan.slots.app && !localDraftPlan.app_name) missing.push("app");

        const reprompt = missing.includes("restaurant")
          ? "Which restaurant would you like to order from, Domino's, Pizza Hut, Subway, or somewhere else?"
          : `Please specify the missing ${missing.join(" and ")}.`;

        handleResponse({
          state: "AWAITING_SLOTS",
          spoken_text: reprompt,
          decision: { intent: "AMBIGUITY_RESOLVE" },
          extracted_plan: localDraftPlan
        });
        return;
      }

      // 3 slots satisfied! Check if mode is resolved or needs resolution
      const item = localDraftPlan.slots.item || "pizza";
      const rest = localDraftPlan.slots.restaurant || "Domino's";
      const appN = localDraftPlan.app_name || "Zomato";
      localDraftPlan.summary = `Order ${item} from ${rest} on ${appN}`;

      if (localDraftPlan.mode === "UNRESOLVED" || !localDraftPlan.mode) {
        localDraftPlan.stage = "STAGE_1_INTENT_MODE";
        localDraftPlan.status = "awaiting_mode";
        handleResponse({
          state: "AWAITING_MODE",
          spoken_text: `I've extracted your request: ${localDraftPlan.summary}. First, are you in TEACH mode so you can demonstrate the steps on your screen, or ORDER mode to execute autonomously?`,
          decision: { intent: "RESOLVE_MODE" },
          extracted_plan: localDraftPlan
        });
        return;
      }

      // Mode is known -> Advance to Stage 3!
      localDraftPlan.stage = "STAGE_3_EXECUTION";
      localDraftPlan.status = "awaiting_confirmation";
      const mode = localDraftPlan.mode || "ORDER";
      const prompt = mode === "TEACH"
        ? `Great, TEACH mode selected for ${appN}. Plan: ${localDraftPlan.summary}. Ready to start recording the demonstration?`
        : `Great, ORDER mode selected for ${appN}. Plan: ${localDraftPlan.summary}. Shall I proceed with the order?`;

      handleResponse({
        state: "AWAITING_CONFIRMATION",
        spoken_text: prompt,
        decision: { intent: "CONFIRM_PLAN" },
        extracted_plan: localDraftPlan
      });
      return;
    }

    // STAGE 3: Plan Confirmation & Execution / Demonstration Launch
    if (currentStage === "STAGE_3_EXECUTION" || status === "awaiting_confirmation") {
      if (["yes", "proceed", "confirm", "start", "go ahead", "sure", "ok", "ready"].some(w => lower.includes(w))) {
        localDraftPlan.confirmed = true;
        const mode = localDraftPlan.mode || "ORDER";
        const plan = { ...localDraftPlan };
        localLastCompletedPlan = { ...plan, stage: "COMPLETED", status: "completed" };
        localDraftPlan = null;

        if (mode === "TEACH") {
          localLearnedWorkflows[plan.flow_id || "order_dominos_zomato"] = {
            app_name: plan.app_name,
            trigger_phrases: [plan.summary],
            default_slots: plan.slots
          };
          handleResponse({
            state: "AWAITING_DEMO",
            spoken_text: `Learned: ${plan.summary}. Starting demonstration recording now, please perform the taps on your screen.`,
            decision: { intent: "TEACH", flow_id: plan.flow_id, app_name: plan.app_name },
            extracted_plan: plan
          });
        } else {
          handleResponse({
            state: "EXECUTING_FLOW",
            spoken_text: `Confirmed. Executing ${plan.summary} on ${plan.app_name} now.`,
            decision: { intent: "REPLAY", matched_flow_id: plan.flow_id },
            extracted_plan: plan
          });
        }
        return;
      }
    }
  }

  // =========================================================================
  // NEW USER ACTIONS / INTENT INTAKE (STAGE 1)
  // =========================================================================
  // Ambiguity: "Order pizza" without restaurant
  if (lower === "order pizza" || lower === "pizza" || (lower.startsWith("teach") && lower.includes("order pizza") && !lower.includes("domino") && !lower.includes("pizza hut"))) {
    const isTeach = lower.includes("teach");
    localDraftPlan = {
      mode: isTeach ? "TEACH" : "UNRESOLVED",
      stage: "STAGE_2_SLOTS",
      app_name: "Zomato",
      flow_id: "order_dominos_zomato",
      slots: { item: "pizza", app: "Zomato" },
      summary: "Order pizza",
      confirmed: false,
      status: "awaiting_slots"
    };

    handleResponse({
      state: "AWAITING_SLOTS",
      spoken_text: "Which restaurant would you like to order from, Domino's or somewhere else?",
      decision: { intent: "AMBIGUITY_RESOLVE" },
      extracted_plan: localDraftPlan
    });
    return;
  }

  // Food Order flow (Zomato / Domino's)
  if (lower.includes("margherita") || (lower.includes("domino") && lower.includes("zomato")) || lower.includes("farmhouse")) {
    const flowExists = !!localLearnedWorkflows["order_dominos_zomato"];

    if (flowExists && !lower.startsWith("teach")) {
      let item = "Margherita pizza";
      let qty = 1;
      let addr = "Default";
      if (lower.includes("farmhouse")) item = "Farmhouse pizza";
      if (lower.includes("two") || lower.includes("2")) qty = 2;
      if (lower.includes("work") || lower.includes("office")) addr = "Work";

      const summary = `Ordering ${qty > 1 ? qty + ' ' : ''}${item} from Domino's on Zomato${addr !== 'Default' ? ', deliver to ' + addr : ''}.`;
      handleResponse({
        state: "EXECUTING_FLOW",
        spoken_text: summary,
        decision: { intent: "REPLAY", matched_flow_id: "order_dominos_zomato" },
        extracted_plan: {
          mode: "ORDER",
          stage: "STAGE_3_EXECUTION",
          app_name: "Zomato",
          flow_id: "order_dominos_zomato",
          slots: { item, restaurant: "Domino's", app: "Zomato", quantity: qty, address: addr },
          summary,
          confirmed: true,
          status: "executing"
        }
      });
      return;
    }

    // New intake flow
    localDraftPlan = {
      mode: lower.startsWith("teach") ? "TEACH" : "UNRESOLVED",
      stage: lower.startsWith("teach") ? "STAGE_3_EXECUTION" : "STAGE_1_INTENT_MODE",
      app_name: "Zomato",
      flow_id: "order_dominos_zomato",
      slots: { item: "Margherita pizza", restaurant: "Domino's", app: "Zomato", quantity: 1 },
      summary: "Order Margherita pizza from Domino's on Zomato",
      confirmed: false,
      status: lower.startsWith("teach") ? "awaiting_confirmation" : "awaiting_mode"
    };

    if (localDraftPlan.mode === "TEACH") {
      handleResponse({
        state: "AWAITING_CONFIRMATION",
        spoken_text: `I've prepared your TEACH plan: ${localDraftPlan.summary}. Ready to start recording the demonstration?`,
        decision: { intent: "CONFIRM_PLAN" },
        extracted_plan: localDraftPlan
      });
    } else {
      handleResponse({
        state: "AWAITING_MODE",
        spoken_text: "I've extracted your request: Order Margherita pizza from Domino's on Zomato. First, are you in TEACH mode so you can demonstrate the steps on your screen, or ORDER mode to execute autonomously?",
        decision: { intent: "RESOLVE_MODE" },
        extracted_plan: localDraftPlan
      });
    }
    return;
  }

  // E-commerce flow (Amazon)
  if (lower.includes("earbuds") || lower.includes("amazon") || lower.includes("phone case")) {
    const isExplicitTeach = lower.startsWith("teach");
    const searchTerm = lower.includes("phone case") ? "phone case" : "wireless earbuds";
    const summary = `Search for ${searchTerm} on Amazon and add the first result to cart`;
    const slots = { search_term: searchTerm, store: "Amazon", app: "Amazon" };

    localDraftPlan = {
      mode: isExplicitTeach ? "TEACH" : "UNRESOLVED",
      stage: isExplicitTeach ? "STAGE_3_EXECUTION" : "STAGE_1_INTENT_MODE",
      app_name: "Amazon",
      flow_id: "search_amazon",
      slots: slots,
      summary: summary,
      confirmed: false,
      status: isExplicitTeach ? "awaiting_confirmation" : "awaiting_mode"
    };

    if (isExplicitTeach) {
      handleResponse({
        state: "AWAITING_CONFIRMATION",
        spoken_text: `I've prepared your TEACH plan: ${summary}. Ready to start recording the demonstration?`,
        decision: { intent: "CONFIRM_PLAN" },
        extracted_plan: localDraftPlan
      });
    } else {
      handleResponse({
        state: "AWAITING_MODE",
        spoken_text: `I've extracted your request: ${summary}. First, are you in TEACH mode to demonstrate this on Amazon, or ORDER mode to execute autonomously?`,
        decision: { intent: "RESOLVE_MODE" },
        extracted_plan: localDraftPlan
      });
    }
    return;
  }

  // Conversational Fallback (Offline)
  const cleanText = text.trim();
  let fallbackReply = "I'm with you. Feel free to tell me what task you'd like to automate or order on your device, or ask me any question!";
  if (lower.startsWith("who are you") || lower.startsWith("what is your name")) {
    fallbackReply = "I am Ava, your teachable mobile voice assistant. I can automate apps on your device, learn new workflows from your screen demonstrations, and carry out tasks for you.";
  } else if (lower.includes("how are you")) {
    fallbackReply = "I'm doing well, thank you! I'm ready to help you navigate your apps or automate your tasks. How can I assist you?";
  }
  handleResponse({
    state: postStage,
    spoken_text: fallbackReply,
    decision: { intent: "CONVERSE" }
  });
}

function handleResponse(data) {
  const decision = data.decision || {};
  const spokenText = data.spoken_text || "";
  const intent = decision.intent || "CONVERSE";

  let badgeClass = "badge-replay";
  if (intent === "TEACH") badgeClass = "badge-teach";
  if (intent === "RESOLVE_MODE" || intent === "AMBIGUITY_RESOLVE") badgeClass = "badge-clarify";
  if (intent === "CONFIRM_PLAN") badgeClass = "badge-teach";

  const plan = data.extracted_plan || decision.extracted_plan || null;
  let planHtml = "";
  if (plan) {
    const mode = (plan.mode || "UNRESOLVED").toUpperCase();
    const modeClass = mode === "TEACH" ? "plan-mode-teach" : (mode === "ORDER" ? "plan-mode-order" : "plan-mode-unresolved");
    const modeLabel = mode === "TEACH" ? "TEACH MODE (Demonstration)" : (mode === "ORDER" ? "ORDER MODE (Autonomous Replay)" : "MODE UNRESOLVED");
    const statusLabel = (plan.status || "pending").replace(/_/g, " ").toUpperCase();

    let slotsHtml = "";
    const pSlots = plan.slots || {};
    for (const [k, v] of Object.entries(pSlots)) {
      slotsHtml += `
        <div class="plan-slot-item">
          <span class="plan-slot-key">${k}</span>
          <span class="plan-slot-val">${v}</span>
        </div>
      `;
    }

    let actionsHtml = "";
    if (plan.status === "awaiting_mode") {
      actionsHtml = `
        <div class="plan-actions">
          <button class="plan-btn-mode" onclick="lockAllPlanActionButtons('mode', 'TEACH MODE'); sendTextToServer('Teach mode')">🖐 Teach Mode</button>
          <button class="plan-btn-mode" onclick="lockAllPlanActionButtons('mode', 'ORDER MODE'); sendTextToServer('Order mode')">⚡ Order Mode</button>
        </div>
      `;
    } else if (plan.status === "awaiting_confirmation") {
      actionsHtml = `
        <div class="plan-actions">
          <button class="plan-btn-confirm" onclick="lockAllPlanActionButtons('confirm'); sendTextToServer('Yes, proceed')">✓ Confirm & Proceed</button>
          <button class="plan-btn-cancel" onclick="lockAllPlanActionButtons('cancel'); cancelActiveWorkflow()">Cancel</button>
        </div>
      `;
    }

    planHtml = `
      <div class="plan-card">
        <div class="plan-card-header">
          <span class="plan-mode-badge ${modeClass}">${modeLabel}</span>
          <span class="plan-status-badge">${statusLabel}</span>
        </div>
        <div class="plan-summary">${plan.summary || "Extracted Plan"}</div>
        ${slotsHtml ? `<div class="plan-slots-grid">${slotsHtml}</div>` : ""}
        ${actionsHtml}
      </div>
    `;
  }

  appendMessage("assistant", spokenText + planHtml, badgeClass, intent);

  // Floating HUD and button locking lifecycle
  if (intent === "TEACH") {
    lockAllPlanActionButtons("confirm");
    showFloatingTeachHud(decision.app_name || (data.extracted_plan || {}).app_name || "App", decision.initial_trigger_phrase || "");
  } else if (intent === "REPLAY") {
    lockAllPlanActionButtons("confirm");
    hideFloatingTeachHud();
    const replayPlan = plan || (decision && decision.extracted_plan) || localDraftPlan || localLastCompletedPlan;
    if (replayPlan && window.AndroidBridge && typeof window.AndroidBridge.startReplay === "function") {
      try {
        console.log("Triggering AndroidBridge.startReplay for plan:", replayPlan);
        window.AndroidBridge.startReplay(JSON.stringify(replayPlan));
      } catch (e) {
        console.error("AndroidBridge.startReplay error:", e);
      }
    }
  } else if (intent === "CONFIRM_PLAN") {
    const planMode = (data.extracted_plan || decision.extracted_plan || {}).mode;
    if (planMode === "TEACH" || planMode === "ORDER") {
      lockAllPlanActionButtons("mode", `${planMode} MODE`);
    }
  } else if (intent === "CONVERSE") {
    const spLower = (spokenText || "").toLowerCase();
    if (spLower.includes("demonstration captured") || spLower.includes("saved") || spLower.includes("workflow cancelled")) {
      hideFloatingTeachHud();
    }
  }

  const isQuestion = spokenText.includes("?") ||
                     data.state === "AWAITING_CLARIFICATION" ||
                     data.state === "AWAITING_MODE" ||
                     data.state === "AWAITING_CONFIRMATION" ||
                     intent === "RESOLVE_MODE" ||
                     intent === "CONFIRM_PLAN";

  if (data.audio_base64) {
    playBase64Audio(data.audio_base64, data.state, isQuestion);
  } else {
    speakBrowserTTS(spokenText, data.state, isQuestion);
  }
}

function playBase64Audio(base64Str, nextState = "idle", autoOpenMic = false) {
  stopCurrentSpeech();
  setAppState("speaking");
  const audio = new Audio("data:audio/mp3;base64," + base64Str);
  currentAudio = audio;

  audio.onended = () => {
    currentAudio = null;
    const target = (nextState || "idle").toLowerCase();
    setAppState(target);
    if (autoOpenMic) {
      setTimeout(() => startRecording(), 400);
    }
  };

  audio.onerror = () => {
    currentAudio = null;
    setAppState("idle");
  };

  audio.play().catch((e) => {
    console.warn("Audio autoplay blocked:", e);
    currentAudio = null;
    setAppState("idle");
  });
}

function speakBrowserTTS(text, nextState = "idle", autoOpenMic = false) {
  stopCurrentSpeech();
  if (!window.speechSynthesis) {
    setAppState((nextState || "idle").toLowerCase());
    return;
  }

  setAppState("speaking");
  const utter = new SpeechSynthesisUtterance(text);
  utter.rate = 1.0;
  utter.pitch = 1.0;

  const voices = window.speechSynthesis.getVoices();
  const avaVoice = voices.find(v => v.name.includes("Ava") || (v.lang === "en-US" && v.name.includes("Natural")));
  if (avaVoice) utter.voice = avaVoice;

  utter.onend = () => {
    const target = (nextState || "idle").toLowerCase();
    setAppState(target);
    if (autoOpenMic) {
      setTimeout(() => startRecording(), 400);
    }
  };

  utter.onerror = () => {
    setAppState("idle");
  };

  window.speechSynthesis.speak(utter);
}

function appendMessage(role, content, badgeClass = "", badgeText = "") {
  removeThinkingIndicator();
  const container = document.getElementById("chatContainer");
  const bubble = document.createElement("div");
  bubble.className = `msg-bubble msg-${role}`;

  let inner = "";
  if (badgeText) inner += `<span class="msg-badge ${badgeClass}">${badgeText}</span>`;
  inner += `<div>${content}</div>`;
  bubble.innerHTML = inner;

  container.appendChild(bubble);

  // Robust double-anchor scrolling: container scrollTop + element scrollIntoView
  requestAnimationFrame(() => {
    container.scrollTop = container.scrollHeight;
    try {
      bubble.scrollIntoView({ behavior: 'smooth', block: 'end' });
    } catch (e) {}
  });
}

// Voice Decision Button Locking: Mutate or disable all active buttons when a decision is made
function lockAllPlanActionButtons(decisionType, labelText = "") {
  const actionContainers = document.querySelectorAll(".plan-actions");
  actionContainers.forEach((container) => {
    const badge = document.createElement("div");
    if (decisionType === "mode") {
      badge.className = "plan-decision-badge mode-set";
      badge.innerHTML = `<span>✓</span><span>Selected: ${labelText || 'Mode Set'}</span>`;
    } else if (decisionType === "confirm") {
      badge.className = "plan-decision-badge confirmed";
      badge.innerHTML = `<span>✓</span><span>Confirmed & Starting</span>`;
    } else if (decisionType === "cancel") {
      badge.className = "plan-decision-badge cancelled";
      badge.innerHTML = `<span>✕</span><span>Workflow Cancelled</span>`;
    } else {
      badge.className = "plan-decision-badge mode-set";
      badge.innerHTML = `<span>✓</span><span>${labelText || 'Decision Recorded'}</span>`;
    }

    if (container.parentNode) {
      container.parentNode.replaceChild(badge, container);
    }
  });
}

// Floating Demonstration Overlay HUD
function showFloatingTeachHud(appName = "App", summary = "") {
  const hud = document.getElementById("floatingTeachHud");
  const title = document.getElementById("teachHudTitle");
  if (hud && title) {
    title.textContent = `Recording Screen Demo: ${appName}`;
    hud.style.display = "flex";
  }

  // Trigger Android native overlay if available
  if (window.AndroidBridge && window.AndroidBridge.showFloatingOverlay) {
    try {
      window.AndroidBridge.showFloatingOverlay(appName);
    } catch (e) {
      console.warn("AndroidBridge call error:", e);
    }
  }
}

function hideFloatingTeachHud() {
  const hud = document.getElementById("floatingTeachHud");
  if (hud) {
    hud.style.display = "none";
  }

  if (window.AndroidBridge && window.AndroidBridge.hideFloatingOverlay) {
    try {
      window.AndroidBridge.hideFloatingOverlay();
    } catch (e) {}
  }
}

function finishTeachWorkflow() {
  hideFloatingTeachHud();
  stopCurrentSpeech();
  sendTextToServer("Finish demonstration");
}

function simulateUtterance(text) {
  stopCurrentSpeech();
  sendTextToServer(text);
}

// Native Replay Event Listener & Device Setup Bridge
window.onReplayEvent = function (evt) {
  if (!evt || !evt.text) return;
  if (evt.type === "status") {
    const s = document.getElementById("statusText");
    if (s) s.textContent = evt.text.slice(0, 40);
    return;
  }
  const badge = { ask: "badge-ambiguity", completed: "badge-replay", failed: "badge-unknown",
                  stopped: "badge-unknown" }[evt.type] || "badge-replay";
  appendMessage("assistant", evt.text, badge, "EXECUTION");
};

function openDeviceSetup() {
  if (window.AndroidBridge && window.AndroidBridge.openSetup) window.AndroidBridge.openSetup();
}

// Settings modal
function openSettings() {
  const setupBtn = document.getElementById("deviceSetupBtn");
  if (setupBtn) setupBtn.style.display = (window.AndroidBridge && window.AndroidBridge.openSetup) ? "block" : "none";
  document.getElementById("serverUrlInput").value = backendUrl;
  document.getElementById("settingsModal").classList.add("open");
}

function closeSettings() {
  document.getElementById("settingsModal").classList.remove("open");
}

async function saveSettings() {
  const input = document.getElementById("serverUrlInput").value.trim();
  if (input) {
    backendUrl = input.replace(/\/+$/, "");
    localStorage.setItem("backend_url", backendUrl);
    const ok = await probeBackend(backendUrl);
    updateConnectionBadge(ok, backendUrl);
  }
  closeSettings();
}
