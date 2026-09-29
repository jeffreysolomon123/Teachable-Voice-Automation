import time
import os
from dotenv import load_dotenv
load_dotenv('voice_assistant_app/.env')
from voice_assistant_app.orchestrator import VoiceOrchestrator, SYSTEM_PROMPT

orch = VoiceOrchestrator()

def groq_first_call_llm(user_prompt: str, recent_history, transcript: str) -> str:
    if orch.groq_client:
        for model_name in ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]:
            try:
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                for h in recent_history:
                    messages.append({"role": h["role"], "content": h["content"]})
                messages.append({"role": "user", "content": user_prompt})

                completion = orch.groq_client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=0.1,
                    response_format={"type": "json_object"}
                )
                if completion and completion.choices:
                    return completion.choices[0].message.content
            except Exception as e:
                print(f"[Orchestrator] Groq ({model_name}) error: {e}")

    if orch.gemini_client:
        for model_name in ["gemini-2.5-flash", "gemini-flash-latest"]:
            try:
                response = orch.gemini_client.models.generate_content(
                    model=model_name,
                    contents=f"{SYSTEM_PROMPT}\n\n{user_prompt}",
                    config={"response_mime_type": "application/json"}
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                print(f"[Orchestrator] Gemini ({model_name}) error: {e}")

    return orch._local_rule_fallback(transcript)

orch._call_llm = groq_first_call_llm

queries = [
    "Hello Ava, how are you today?",
    "What is teach mode?",
    "What can you do?",
    "Order a Margherita pizza from Domino's on Zomato"
]

for q in queries:
    t0 = time.time()
    prompt = f"User Current Utterance:\n{q}\n\nRespond with ONLY valid JSON adhering strictly to the schema."
    res = orch._call_llm(prompt, [], q)
    print(f"Query: {q}")
    print(f"Time: {time.time()-t0:.2f}s")
    print(f"Output: {res}")
    print("-" * 50)
