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
        
        # Step 1: teach the flow
        await tm.process_user_text("Order a Margherita pizza from Domino's on Zomato")
        await tm.process_user_text("Teach mode")
        await tm.process_user_text("Yes, proceed")
        
        # Test T4
        res_t4 = await tm.process_user_text("Order a Farmhouse pizza from Domino's on Zomato")
        print("\n--- TEST T4 ---")
        print("Intent:", res_t4["decision"]["intent"])
        print("Matched TC:", res_t4["matched_testcase"])
        print("Slot overrides:", res_t4["decision"].get("slot_overrides"))

if __name__ == "__main__":
    asyncio.run(test())
