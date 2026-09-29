// State variables
let isRecording = false;
let mediaRecorder = null;
let audioChunks = [];
let audioContext = null;
let analyser = null;
let animFrameId = null;
let speechRecognizer = null;
let speechTranscriptBuffer = "";
let currentAudio = null;

// Individualized Session Management
let currentSessionId = sessionStorage.getItem("voice_session_id");
if (!currentSessionId) {
  currentSessionId = "sess_" + Date.now().toString(36) + "_" + Math.random().toString(36).substr(2, 5);
  sessionStorage.setItem("voice_session_id", currentSessionId);
}

// Dynamic VAD & Turn-Taking Timers
let silenceTimer = null;
let initialTimeoutTimer = null;
let hasSpokenInTurn = false;
const SILENCE_THRESHOLD_MS = 1300; // 1.3s natural pause before turn completion
const INITIAL_SILENCE_TIMEOUT_MS = 7000; // 7s without any speech automatically sleeps mic

// Synthetic Earcons using WebAudio API
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
      // Soft rising two-tone chime (440Hz -> 660Hz)
      osc.type = "sine";
      osc.frequency.setValueAtTime(440, now);
      osc.frequency.exponentialRampToValueAtTime(660, now + 0.12);
      gain.gain.setValueAtTime(0.08, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.18);
      osc.start(now);
      osc.stop(now + 0.2);
    } else if (type === "PING") {
      // Processing acceptance ping (880Hz)
      osc.type = "triangle";
      osc.frequency.setValueAtTime(880, now);
      gain.gain.setValueAtTime(0.06, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.15);
      osc.start(now);
      osc.stop(now + 0.16);
    } else if (type === "HANDOFF" || type === "HANDOFF_ALERT") {
      // Gentle warning chime (554Hz -> 440Hz)
      osc.type = "sine";
      osc.frequency.setValueAtTime(554, now);
      osc.frequency.exponentialRampToValueAtTime(440, now + 0.25);
      gain.gain.setValueAtTime(0.12, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
      osc.start(now);
      osc.stop(now + 0.36);
    }
  } catch (err) {
    console.warn("Could not play earcon:", err);
  }
}

// UI State Management
function setAppState(state) {
  document.body.className = `state-${state.toLowerCase()}`;
  const statusBadge = document.getElementById("statusBadge");
  const statusText = document.getElementById("statusText");

  const stateLabels = {
    idle: "Assistant Ready",
    listening: "Listening...",
    thinking: "Understanding Intent...",
    speaking: "Speaking...",
    executing: "Active Workflow Running",
    awaiting_demo: "Ready for Taps Demo",
    awaiting_mode: "Choose Mode: Teach or Order",
    awaiting_confirmation: "Confirm Execution Plan",
    awaiting_clarification: "Listening for your answer...",
    handoff: "Hand-off: Your Turn!"
  };

  statusText.textContent = stateLabels[state.toLowerCase()] || state;
  if (state.toLowerCase() === "idle") {
    statusBadge.classList.remove("active");
  } else {
    statusBadge.classList.add("active");
  }

  const micBtn = document.getElementById("micBtn");
  if (state.toLowerCase() === "listening" || 
      state.toLowerCase() === "awaiting_clarification" ||
      state.toLowerCase() === "awaiting_mode" ||
      state.toLowerCase() === "awaiting_confirmation") {
    micBtn.classList.add("listening");
  } else {
    micBtn.classList.remove("listening");
  }
}

function getSelectedVoice() {
  const select = document.getElementById("voiceSelect");
  return select ? select.value : "en-US-AvaMultilingualNeural";
}

// Stop current speech playback if user interrupts (Barge-in)
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

// Microphone Toggle & Recording
async function toggleMicrophone() {
  if (isRecording) {
    stopRecording();
  } else {
    await startRecording();
  }
}

async function startRecording() {
  stopCurrentSpeech(); // Barge-in: interrupt assistant if speaking

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx = getAudioContext();
    const source = ctx.createMediaStreamSource(stream);
    analyser = ctx.createAnalyser();
    analyser.fftSize = 64;
    source.connect(analyser);

    audioChunks = [];
    speechTranscriptBuffer = "";
    hasSpokenInTurn = false;

    // Pick supported container
    let chosenMime = "audio/webm;codecs=opus";
    if (!MediaRecorder.isTypeSupported(chosenMime)) {
      chosenMime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : (MediaRecorder.isTypeSupported("audio/mp4") ? "audio/mp4" : "audio/ogg");
    }

    mediaRecorder = new MediaRecorder(stream, { mimeType: chosenMime });

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      cancelAnimationFrame(animFrameId);
      hideLiveIndicator();
      clearTimeout(silenceTimer);
      clearTimeout(initialTimeoutTimer);

      const textInput = document.getElementById("textInput");
      const submittedText = speechTranscriptBuffer.trim();

      // Immediately clear the input box
      if (textInput) {
        textInput.value = "";
      }

      if (submittedText.length >= 2) {
        console.log("[TurnTaking] Final transcript submitted:", submittedText);
        sendTextToServer(submittedText);
      } else if (audioChunks.length > 0 && hasSpokenInTurn) {
        const blob = new Blob(audioChunks, { type: chosenMime });
        const ext = chosenMime.includes("webm") ? "webm" : (chosenMime.includes("mp4") ? "mp4" : "ogg");
        await sendAudioToServer(blob, `voice_input.${ext}`);
      } else {
        console.log("[TurnTaking] No speech detected, resetting to idle.");
        setAppState("idle");
      }
    };

    mediaRecorder.start(100);
    isRecording = true;
    playEarcon("LISTEN");
    setAppState("listening");
    showLiveIndicator("Listening to your voice...");
    monitorAudioLevel();

    // Reset silence timers
    clearTimeout(silenceTimer);
    clearTimeout(initialTimeoutTimer);

    // Initial timeout if user doesn't say anything
    initialTimeoutTimer = setTimeout(() => {
      if (isRecording && !hasSpokenInTurn) {
        console.log("[TurnTaking] Initial silence timeout. Stopping mic.");
        stopRecording();
      }
    }, INITIAL_SILENCE_TIMEOUT_MS);

    // Start Live Web Speech recognition
    startLiveSpeechRecognition();
  } catch (err) {
    console.error("Microphone access failed:", err);
    alert("Microphone access failed: " + err.message + "\nPlease check browser mic permissions or type your command!");
  }
}

function startLiveSpeechRecognition() {
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRec) return;

  try {
    speechRecognizer = new SpeechRec();
    speechRecognizer.continuous = true;
    speechRecognizer.interimResults = true;
    speechRecognizer.lang = "en-IN";

    speechRecognizer.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; ++i) {
        if (event.results[i].isFinal) {
          speechTranscriptBuffer += event.results[i][0].transcript + " ";
        } else {
          interim += event.results[i][0].transcript;
        }
      }
      const current = (speechTranscriptBuffer + " " + interim).trim();
      if (current) {
        hasSpokenInTurn = true;
        showLiveIndicator(current);
        const input = document.getElementById("textInput");
        if (input) input.value = current;

        // Reset silence endpointing timer on every word spoken
        clearTimeout(silenceTimer);
        silenceTimer = setTimeout(() => {
          console.log("[TurnTaking] Dynamic silence threshold reached after speech. Auto-ending turn.");
          stopRecording();
        }, SILENCE_THRESHOLD_MS);
      }
    };

    speechRecognizer.onerror = (e) => {
      console.warn("Live speech recognition notice:", e.error);
    };

    speechRecognizer.start();
  } catch (e) {
    console.warn("Could not start live speech recognition:", e);
  }
}

function stopRecording() {
  if (isRecording) {
    isRecording = false;
    clearTimeout(silenceTimer);
    clearTimeout(initialTimeoutTimer);

    if (speechRecognizer) {
      try { speechRecognizer.stop(); } catch (e) {}
    }
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      mediaRecorder.stop();
    }
    playEarcon("PING");
    setAppState("thinking");
  }
}

function showLiveIndicator(text) {
  const ind = document.getElementById("liveSpeechIndicator");
  const txt = document.getElementById("liveSpeechText");
  if (ind && txt) {
    ind.style.display = "block";
    txt.textContent = text;
  }
}

function hideLiveIndicator() {
  const ind = document.getElementById("liveSpeechIndicator");
  if (ind) ind.style.display = "none";
}

// Audio visualizer and audio energy VAD
function monitorAudioLevel() {
  if (!analyser || !isRecording) return;
  const dataArray = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(dataArray);

  let sum = 0;
  for (let i = 0; i < dataArray.length; i++) {
    sum += dataArray[i];
  }
  const avg = sum / dataArray.length;
  const intensity = Math.min(1.8, Math.max(0.6, avg / 28));

  // If acoustic energy exceeds voice threshold
  if (avg > 18) {
    hasSpokenInTurn = true;
    clearTimeout(silenceTimer);
    silenceTimer = setTimeout(() => {
      console.log("[TurnTaking] Acoustic silence detected after speech. Auto-ending turn.");
      stopRecording();
    }, SILENCE_THRESHOLD_MS);
  }

  const wave = document.querySelector(".siri-bottom-wave");
  if (wave) {
    wave.style.transform = `scaleY(${intensity}) translateY(-${(intensity - 1) * 15}px)`;
  }

  animFrameId = requestAnimationFrame(monitorAudioLevel);
}

function showThinkingIndicator() {
  removeThinkingIndicator();
  const chatMessages = document.getElementById("chatMessages");
  const bubble = document.createElement("div");
  bubble.id = "thinkingBubble";
  bubble.className = "msg-bubble msg-assistant typing-indicator";
  bubble.innerHTML = `
    <span class="typing-dot"></span>
    <span class="typing-dot"></span>
    <span class="typing-dot"></span>
    <span style="font-size: 13px; color: var(--text-muted); margin-left: 8px;">Ava is thinking...</span>
  `;
  chatMessages.appendChild(bubble);
  requestAnimationFrame(() => {
    chatMessages.scrollTop = chatMessages.scrollHeight;
  });
}

function removeThinkingIndicator() {
  const existing = document.getElementById("thinkingBubble");
  if (existing) existing.remove();
}

async function startNewSession() {
  stopCurrentSpeech();
  stopRecording();
  currentSessionId = "sess_" + Date.now().toString(36) + "_" + Math.random().toString(36).substr(2, 5);
  sessionStorage.setItem("voice_session_id", currentSessionId);

  try {
    await fetch("/api/voice/session/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: currentSessionId })
    });
  } catch (e) {
    console.warn("Session reset notice:", e);
  }

  const chatMessages = document.getElementById("chatMessages");
  chatMessages.innerHTML = `
    <div class="msg-bubble msg-assistant">
      <span class="msg-badge badge-replay">FRESH SESSION INITIALIZED</span>
      <div>Hello! I'm your mobile voice assistant. Tell me what you'd like to do — I will extract the execution slots, verify whether you want <strong>TEACH mode</strong> (demonstrate on screen) or <strong>ORDER mode</strong> (autonomous execution), and confirm the plan with you before starting!</div>
    </div>
  `;
  chatMessages.scrollTop = 0;
  setAppState("idle");
  playEarcon("PING");
  loadWorkflows();
}

async function cancelActiveWorkflow() {
  stopCurrentSpeech();
  try {
    await fetch("/api/voice/session/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Session-ID": currentSessionId },
      body: JSON.stringify({ session_id: currentSessionId })
    });
  } catch (e) {}
  sendTextToServer("Cancel");
}

// Send Audio to Server
async function sendAudioToServer(blob, filename) {
  stopCurrentSpeech();
  showThinkingIndicator();
  setAppState("thinking");

  const formData = new FormData();
  formData.append("file", blob, filename);
  formData.append("voice", getSelectedVoice());
  formData.append("session_id", currentSessionId);

  try {
    const res = await fetch("/api/voice/process-audio", {
      method: "POST",
      headers: { "X-Session-ID": currentSessionId },
      body: formData,
    });
    const data = await res.json();
    removeThinkingIndicator();
    handleAssistantResponse(data);
  } catch (err) {
    removeThinkingIndicator();
    console.error("Audio processing error:", err);
    appendMessage("assistant", "Error transcribing audio. Please try again.", "badge-unknown");
    setAppState("idle");
  }
}

// Send Text to Server
async function handleTextSubmit() {
  const input = document.getElementById("textInput");
  const text = input.value.trim();
  if (!text) return;

  // Immediately clear the input box upon submitting
  input.value = "";
  sendTextToServer(text);
}

async function sendTextToServer(text) {
  stopCurrentSpeech();

  // Ensure input box is clean
  const input = document.getElementById("textInput");
  if (input) input.value = "";

  // Voice Decision Button Locking: Block or replace buttons immediately if decision is detected
  const lower = text.toLowerCase().trim();
  if (lower.includes("teach") || lower.includes("record")) {
    lockAllPlanActionButtons("mode", "TEACH MODE");
  } else if (lower.includes("order") || lower.includes("replay") || lower.includes("execute")) {
    lockAllPlanActionButtons("mode", "ORDER MODE");
  } else if (lower.includes("yes") || lower.includes("proceed") || lower.includes("confirm") || lower.includes("start") || lower.includes("sure") || lower.includes("sounds good")) {
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
    const res = await fetch("/api/voice/process-text", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Session-ID": currentSessionId
      },
      body: JSON.stringify({
        text: text,
        voice: getSelectedVoice(),
        session_id: currentSessionId
      }),
    });
    const data = await res.json();
    removeThinkingIndicator();
    handleAssistantResponse(data);
  } catch (err) {
    removeThinkingIndicator();
    console.error("Text processing error:", err);
    appendMessage("assistant", "Error processing command. Please try again.", "badge-unknown");
    setAppState("idle");
  }
}

function simulateUtterance(text) {
  stopCurrentSpeech();
  sendTextToServer(text);
}

async function triggerEvent(triggerType, details) {
  stopCurrentSpeech();
  playEarcon("HANDOFF");
  setAppState(triggerType === "credential_boundary" ? "handoff" : "speaking");
  try {
    const res = await fetch("/api/voice/trigger-event", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trigger_type: triggerType, details }),
    });
    const data = await res.json();
    appendMessage("assistant", data.spoken_prompt, "badge-clarify", triggerType.toUpperCase());
    speakAudio(data.spoken_prompt, "awaiting_clarification", true);
  } catch (err) {
    console.error("Trigger event error:", err);
  }
}

// Process Server Decision
function handleAssistantResponse(data) {
  const decision = data.decision || {};
  const spokenText = data.spoken_text || "";
  const intent = decision.intent || "CONVERSE";
  const matchedTestcase = data.matched_testcase || "";

  let badgeClass = "badge-unknown";
  if (intent === "TEACH") badgeClass = "badge-teach";
  if (intent === "REPLAY") badgeClass = "badge-replay";
  if (intent === "AMBIGUITY_RESOLVE") badgeClass = "badge-clarify";
  if (intent === "RESOLVE_MODE") badgeClass = "badge-clarify";
  if (intent === "CONFIRM_PLAN") badgeClass = "badge-teach";
  if (intent === "CONVERSE") badgeClass = "badge-replay";

  // Build slot pills (legacy fallback)
  const slots = decision.slot_overrides || decision.extracted_slots || decision.effective_slots || {};
  let slotHtml = "";
  if (Object.keys(slots).length > 0 && !data.extracted_plan) {
    slotHtml = '<div class="slot-pills">';
    for (const [k, v] of Object.entries(slots)) {
      slotHtml += `<span class="slot-pill">${k}: <strong>${v}</strong></span>`;
    }
    slotHtml += "</div>";
  }

  // Render structured Extracted Plan Card if present
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
        <div class="plan-summary">${plan.summary || "Extracted Execution Plan"}</div>
        ${slotsHtml ? `<div class="plan-slots-grid">${slotsHtml}</div>` : ""}
        ${actionsHtml}
      </div>
    `;
  }

  appendMessage("assistant", spokenText + slotHtml + planHtml, badgeClass, `${intent} ${matchedTestcase ? `(${matchedTestcase})` : ""}`);

  if (matchedTestcase) {
    const tcResult = document.getElementById("lastTestResult");
    tcResult.textContent = `✓ Passed ${matchedTestcase}`;
  }

  if (data.earcon_cue) {
    playEarcon(data.earcon_cue);
  }

  // Check if assistant response is a question requiring user reply
  const isQuestion = spokenText.includes("?") || 
                     data.state === "AWAITING_CLARIFICATION" || 
                     data.state === "AWAITING_MODE" || 
                     data.state === "AWAITING_CONFIRMATION" || 
                     intent === "AMBIGUITY_RESOLVE" ||
                     intent === "RESOLVE_MODE" ||
                     intent === "CONFIRM_PLAN";

  // Play Neural Audio
  if (data.audio_base64) {
    playBase64Audio(data.audio_base64, data.state, isQuestion);
  } else if (spokenText) {
    speakAudio(spokenText, data.state, isQuestion);
  } else {
    setAppState(data.state || "idle");
    if (isQuestion) {
      setTimeout(() => startRecording(), 400);
    }
  }

  // State and HUD lifecycle updates
  if (intent === "TEACH") {
    lockAllPlanActionButtons("confirm");
    showFloatingTeachHud(decision.app_name || (data.extracted_plan || {}).app_name || "App", decision.initial_trigger_phrase || "");
  } else if (intent === "REPLAY") {
    lockAllPlanActionButtons("confirm");
    hideFloatingTeachHud();
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

  loadWorkflows();
}

function playBase64Audio(base64Str, nextState = "idle", autoOpenMic = false) {
  stopCurrentSpeech();
  setAppState("speaking");

  const audio = new Audio("data:audio/mp3;base64," + base64Str);
  currentAudio = audio;

  audio.onended = () => {
    currentAudio = null;
    const targetState = (nextState || "idle").toLowerCase();
    if (autoOpenMic) {
      console.log("[TurnTaking] Interaction prompt by assistant. Automatically opening mic for user reply in state:", targetState);
      setAppState(targetState);
      setTimeout(() => {
        startRecording();
      }, 350);
    } else {
      setAppState(targetState);
    }
  };

  audio.onerror = () => {
    currentAudio = null;
    setAppState("idle");
  };

  audio.play().catch((e) => {
    console.warn("Audio autoplay notice:", e);
    currentAudio = null;
    setAppState("idle");
  });
}

function speakAudio(text, nextState = "idle", autoOpenMic = false) {
  stopCurrentSpeech();
  const voice = getSelectedVoice();
  const url = `/api/voice/tts?text=${encodeURIComponent(text)}&voice=${encodeURIComponent(voice)}`;
  setAppState("speaking");

  const audio = new Audio(url);
  currentAudio = audio;

  audio.onended = () => {
    currentAudio = null;
    const targetState = (nextState || "idle").toLowerCase();
    if (autoOpenMic) {
      console.log("[TurnTaking] Interaction prompt by assistant. Automatically opening mic for user reply in state:", targetState);
      setAppState(targetState);
      setTimeout(() => {
        startRecording();
      }, 350);
    } else {
      setAppState(targetState);
    }
  };

  audio.onerror = () => {
    currentAudio = null;
    setAppState("idle");
  };

  audio.play().catch((e) => {
    console.warn("Audio playback notice:", e);
    currentAudio = null;
    setAppState("idle");
  });
}

function appendMessage(role, content, badgeClass = "", badgeText = "") {
  removeThinkingIndicator();
  const chatMessages = document.getElementById("chatMessages");
  const bubble = document.createElement("div");
  bubble.className = `msg-bubble msg-${role}`;

  let inner = "";
  if (badgeText) {
    inner += `<span class="msg-badge ${badgeClass}">${badgeText}</span>`;
  }
  inner += `<div>${content}</div>`;
  bubble.innerHTML = inner;

  chatMessages.appendChild(bubble);

  // Robust double-anchor scrolling: container scrollTop + element scrollIntoView
  requestAnimationFrame(() => {
    chatMessages.scrollTop = chatMessages.scrollHeight;
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
}

function hideFloatingTeachHud() {
  const hud = document.getElementById("floatingTeachHud");
  if (hud) {
    hud.style.display = "none";
  }
}

function finishTeachWorkflow() {
  hideFloatingTeachHud();
  stopCurrentSpeech();
  sendTextToServer("Finish demonstration");
}

// Memory Inspector Loader
async function loadWorkflows() {
  try {
    const res = await fetch(`/api/voice/workflows?session_id=${encodeURIComponent(currentSessionId)}`, {
      headers: { "X-Session-ID": currentSessionId }
    });
    const data = await res.json();

    // Render Workflows
    const list = document.getElementById("memoryList");
    const workflows = data.workflows || {};

    if (Object.keys(workflows).length === 0) {
      list.innerHTML = `<div class="memory-item"><div class="memory-item-details">No workflows learned yet. Teach a flow to see it stored here!</div></div>`;
    } else {
      let html = "";
      for (const [id, wf] of Object.entries(workflows)) {
        html += `
          <div class="memory-item">
            <div class="memory-item-title">${id}</div>
            <div class="memory-item-details">
              App: <strong>${wf.app_name}</strong><br>
              Triggers: <em>${wf.trigger_phrases ? wf.trigger_phrases.join(", ") : ""}</em>
            </div>
          </div>
        `;
      }
      list.innerHTML = html;
    }

    // Render Remembered Preferences
    const prefList = document.getElementById("preferencesList");
    const prefs = data.preferences || {};
    if (Object.keys(prefs).length === 0) {
      prefList.innerHTML = `<em>None saved yet. Try saying: "Remember that my work address is Cyber City"</em>`;
    } else {
      let prefHtml = "<ul style='padding-left: 16px; margin: 0;'>";
      for (const [k, v] of Object.entries(prefs)) {
        prefHtml += `<li><strong>${k}</strong>: ${v}</li>`;
      }
      prefHtml += "</ul>";
      prefList.innerHTML = prefHtml;
    }
  } catch (err) {
    console.error("Could not load workflows:", err);
  }
}

async function clearMemory() {
  if (confirm("Reset conversation turns?")) {
    await fetch("/api/voice/history", { method: "DELETE" });
    loadWorkflows();
    const chat = document.getElementById("chatMessages");
    chat.innerHTML = `<div class="msg-bubble msg-assistant"><span class="msg-badge badge-replay">MEMORY RESET</span><div>Conversation history reset. User preferences and workflows retained!</div></div>`;
  }
}

// Initial Load
document.addEventListener("DOMContentLoaded", () => {
  loadWorkflows();
});
