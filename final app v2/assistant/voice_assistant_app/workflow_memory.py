import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from voice_assistant_app.config import WORKFLOWS_JSON_PATH, WORKFLOWS_TXT_PATH

class WorkflowMemory:
    """Manages storage of learned workflows in JSON and human-readable text format."""
    
    def __init__(self, json_path: Path = WORKFLOWS_JSON_PATH, txt_path: Path = WORKFLOWS_TXT_PATH):
        self.json_path = json_path
        self.txt_path = txt_path
        self._ensure_storage()

    def _ensure_storage(self):
        if not self.json_path.exists():
            default_data = {
                "workflows": {},
                "last_run": {
                    "flow_id": None,
                    "status": "NONE",
                    "step_stopped": None,
                    "timestamp": None,
                    "details": "No runs recorded yet"
                }
            }
            self._save_raw(default_data)
        else:
            self.export_to_txt()

    def _load_raw(self) -> Dict[str, Any]:
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"workflows": {}, "last_run": {}}

    def _save_raw(self, data: Dict[str, Any]):
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.export_to_txt()

    def save_workflow(
        self,
        flow_id: str,
        app_name: str,
        trigger_phrases: List[str],
        default_slots: Dict[str, Any],
        description: str = ""
    ) -> Dict[str, Any]:
        """Save or update a learned workflow."""
        data = self._load_raw()
        workflows = data.setdefault("workflows", {})
        
        existing = workflows.get(flow_id, {})
        merged_triggers = list(set(existing.get("trigger_phrases", []) + trigger_phrases))
        
        record = {
            "flow_id": flow_id,
            "app_name": app_name,
            "description": description or f"Learned flow for {app_name}",
            "trigger_phrases": merged_triggers,
            "default_slots": {**existing.get("default_slots", {}), **default_slots},
            "created_at": existing.get("created_at", datetime.now().isoformat()),
            "updated_at": datetime.now().isoformat()
        }
        workflows[flow_id] = record
        self._save_raw(data)
        return record

    def get_all_workflows(self) -> Dict[str, Any]:
        return self._load_raw().get("workflows", {})

    def get_workflow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        return self.get_all_workflows().get(flow_id)

    def record_run_result(
        self,
        flow_id: str,
        status: str,
        step_stopped: str,
        details: str = ""
    ):
        """Record the telemetry of the last executed run (for T14 Reporting)."""
        data = self._load_raw()
        data["last_run"] = {
            "flow_id": flow_id,
            "status": status,
            "step_stopped": step_stopped,
            "timestamp": datetime.now().isoformat(),
            "details": details
        }
        self._save_raw(data)

    def get_last_run_status(self) -> Dict[str, Any]:
        return self._load_raw().get("last_run", {
            "status": "UNKNOWN",
            "details": "No prior execution runs found."
        })

    def export_to_txt(self):
        """Export current memory state into a clean human-readable text file."""
        data = self._load_raw()
        workflows = data.get("workflows", {})
        last_run = data.get("last_run", {})

        lines = [
            "=" * 70,
            "           TEACHABLE VOICE AUTOMATION - WORKFLOW MEMORY",
            "=" * 70,
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Total Learned Workflows: {len(workflows)}",
            "",
            "--- [LEARNED WORKFLOW REGISTRY] ---",
        ]

        if not workflows:
            lines.append("  (No workflows learned yet. Teach a flow by speaking to the assistant.)")
        else:
            for idx, (fid, wf) in enumerate(workflows.items(), 1):
                lines.append(f"\n[{idx}] FLOW ID: {fid}")
                lines.append(f"    App: {wf.get('app_name')}")
                lines.append(f"    Description: {wf.get('description')}")
                lines.append(f"    Trigger Phrases:")
                for tp in wf.get("trigger_phrases", []):
                    lines.append(f"      - \"{tp}\"")
                lines.append(f"    Default Parameters (Slots):")
                for k, v in wf.get("default_slots", {}).items():
                    lines.append(f"      * {k}: {v}")

        lines.extend([
            "",
            "--- [LAST RUN TELEMETRY / STATUS] ---",
            f"  Flow ID:      {last_run.get('flow_id', 'None')}",
            f"  Status:       {last_run.get('status', 'NONE')}",
            f"  Stopped At:   {last_run.get('step_stopped', 'N/A')}",
            f"  Timestamp:    {last_run.get('timestamp', 'N/A')}",
            f"  Details:      {last_run.get('details', 'N/A')}",
            "=" * 70,
        ])

        with open(self.txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
