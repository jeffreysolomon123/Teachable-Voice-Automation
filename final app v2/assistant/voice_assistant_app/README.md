# Teachable Voice Assistant & Conversational Orchestrator

Standalone Voice Assistant module with:
- **Siri-style Fluid Edge & Bottom Glow UI** (iridescent reactive lighting on screen perimeter)
- **Foolproof Turn-Taking & Orchestrator Harness** (Groq Llama-3.3-70B / Gemini 2.5 Flash with strict Pydantic schemas)
- **Speech-to-Text with Domain Biasing** (Groq Whisper with prompt injection for Indian brands: Zomato, Domino's, Swiggy, Blinkit, Farmhouse pizza, etc.)
- **Natural Neural TTS** (Microsoft Edge-TTS with Indian English voices `en-IN-NeerjaNeural` / `en-IN-PrabhatNeural`)
- **Workflow Memory Component** (`data/workflows.json` & human-readable `data/workflows.txt`)
- **Competition Testcase Evaluator** (T1 to T14)

---

## Quickstart

### 1. Launch the Voice Assistant Server
```bash
python -m voice_assistant_app.run
```
Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** in your browser.

### 2. Run the Benchmark Test Suite
```bash
$env:PYTHONPATH="."
pytest voice_assistant_app/tests/test_scenarios.py -v
```

---

## Architecture & Testcase Mapping

| Testcase | Intent / Scenario | Voice Feedback / Spoken Output |
| :--- | :--- | :--- |
| **T1** | Teach: Food flow (Zomato/Domino's) | *"Learned: order Margherita pizza from Domino's"* |
| **T2** | Exact Replay | *"Ordering Margherita pizza from Domino's on Zomato."* |
| **T3** | Paraphrase Replay | Maps to T1 flow |
| **T4** | Slot: Item change | Overrides `item = Farmhouse pizza` |
| **T5** | Slot: Quantity change | Overrides `quantity = 2` |
| **T6** | Slot: Address change | Overrides `address = Work` |
| **T7** | Screen Popup Change | *"There is a promo pop-up. Shall I close it?"* |
| **T8** | Teach: E-commerce (Amazon) | *"Learned: search for wireless earbuds on Amazon..."* |
| **T9** | Cross-App Slot Replay | Overrides `search_term = phone case` |
| **T10**| Stuck State (Hindi screen) | *"I'm stuck because the screen is in Hindi..."* |
| **T11**| Credential Hand-off | *"Reached payment screen. Your turn!"* |
| **T12**| Negative / Unknown Intent | *"I haven't learned how to do that yet. Want to teach me?"* |
| **T13**| Ambiguity Resolution | *"Which restaurant would you like to order from?"* |
| **T14**| Reporting | *"The last run status was SUCCESS up to payment..."* |
