import asyncio
import tempfile
from pathlib import Path
from voice_assistant_app.workflow_memory import WorkflowMemory
from voice_assistant_app.session_memory import SessionMemory
from voice_assistant_app.orchestrator import VoiceOrchestrator
from voice_assistant_app.turn_manager import TurnManager

async def test():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        mem = WorkflowMemory(json_path=tmp_path / "workflows.json", txt_path=tmp_path / "workflows.txt")
        sess = SessionMemory(json_path=tmp_path / "session.json", txt_path=tmp_path / "session.txt")
        orch = VoiceOrchestrator(memory=mem, session_memory=sess)
        tm = TurnManager(orchestrator=orch, memory=mem, session_memory=sess)
        
        print("Registered workflows count:", len(mem.get_all_workflows()))
        
        # Turn 1
        res1 = await tm.process_user_text("Order a Margherita pizza from Domino's on Zomato")
        print("\n--- TURN 1 ---")
        print("Decision Intent:", res1["decision"]["intent"])
        print("Matched TC:", res1["matched_testcase"])
        print("State:", res1["state"])
        print("Spoken Text:", res1["spoken_text"])
        print("Extracted Plan:", res1["extracted_plan"])
        
        # Turn 2
        res2 = await tm.process_user_text("Teach mode")
        print("\n--- TURN 2 ---")
        print("Decision Intent:", res2["decision"]["intent"])
        print("Matched TC:", res2["matched_testcase"])
        print("State:", res2["state"])
        print("Spoken Text:", res2["spoken_text"])
        print("Extracted Plan:", res2["extracted_plan"])
        
        # Turn 3
        res3 = await tm.process_user_text("Yes, proceed")
        print("\n--- TURN 3 ---")
        print("Decision Intent:", res3["decision"]["intent"])
        print("Matched TC:", res3["matched_testcase"])
        print("State:", res3["state"])
        print("Spoken Text:", res3["spoken_text"])
        print("Extracted Plan:", res3["extracted_plan"])
        print("Saved flow:", mem.get_workflow(res3["decision"]["flow_id"]))

if __name__ == "__main__":
    asyncio.run(test())
