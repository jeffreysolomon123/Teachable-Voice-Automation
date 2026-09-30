import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from voice_assistant_app.config import DATA_DIR

SESSION_JSON_PATH = DATA_DIR / "session_memory.json"
USER_CONTEXT_TXT_PATH = DATA_DIR / "user_context.txt"

class SessionMemory:
    """Maintains multi-turn conversation history and context for an individualized session."""

    def __init__(
        self,
        session_id: str = "default",
        json_path: Optional[Path] = None,
        txt_path: Optional[Path] = None
    ):
        self.session_id = session_id or "default"
        
        if json_path:
            self.json_path = json_path
        elif self.session_id == "default":
            self.json_path = SESSION_JSON_PATH
        else:
            sessions_dir = DATA_DIR / "sessions"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            self.json_path = sessions_dir / f"{self.session_id}.json"

        if txt_path:
            self.txt_path = txt_path
        elif self.session_id == "default":
            self.txt_path = USER_CONTEXT_TXT_PATH
        else:
            sessions_dir = DATA_DIR / "sessions"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            self.txt_path = sessions_dir / f"{self.session_id}_context.txt"

        self._ensure_storage()

    def _ensure_storage(self):
        if not self.json_path.exists():
            default_data = {
                "session_id": self.session_id,
                "chat_history": [],
                "user_preferences": {},
                "pending_clarification": None,
                "draft_plan": None,
                "current_topic": None,
                "created_at": datetime.now().isoformat()
            }
            self._save_raw(default_data)
        else:
            self.export_to_txt()

    def _load_raw(self) -> Dict[str, Any]:
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {
                "session_id": self.session_id,
                "chat_history": [],
                "user_preferences": {},
                "pending_clarification": None,
                "draft_plan": None
            }

    def _save_raw(self, data: Dict[str, Any]):
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.export_to_txt()

    def add_message(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None):
        """Add a turn to conversation memory."""
        data = self._load_raw()
        history = data.setdefault("chat_history", [])
        history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        })
        if len(history) > 30:
            history = history[-30:]
        data["chat_history"] = history
        self._save_raw(data)

    def get_recent_history(self, limit: int = 10) -> List[Dict[str, str]]:
        """Get recent dialogue turns formatted for LLM input."""
        data = self._load_raw()
        history = data.get("chat_history", [])
        recent = history[-limit:] if len(history) > limit else history
        return [{"role": item["role"], "content": item["content"]} for item in recent]

    def update_preference(self, key: str, value: Any):
        """Remember a user fact or preference."""
        data = self._load_raw()
        prefs = data.setdefault("user_preferences", {})
        prefs[key] = value
        self._save_raw(data)

    def get_all_preferences(self) -> Dict[str, Any]:
        return self._load_raw().get("user_preferences", {})

    def set_pending_clarification(self, clarification: Optional[Dict[str, Any]]):
        data = self._load_raw()
        data["pending_clarification"] = clarification
        self._save_raw(data)

    def get_pending_clarification(self) -> Optional[Dict[str, Any]]:
        return self._load_raw().get("pending_clarification")

    def set_draft_plan(self, plan: Optional[Dict[str, Any]]):
        """Store or update the in-progress extracted execution/teaching plan."""
        data = self._load_raw()
        data["draft_plan"] = plan
        self._save_raw(data)

    def get_draft_plan(self) -> Optional[Dict[str, Any]]:
        """Retrieve current in-progress extracted plan."""
        return self._load_raw().get("draft_plan")

    def cancel_active_workflow(self):
        """Immediately discard any in-progress draft plan and pending clarifications."""
        data = self._load_raw()
        data["draft_plan"] = None
        data["pending_clarification"] = None
        data["current_topic"] = None
        self._save_raw(data)

    def clear_history(self):
        """Reset conversation turns and draft plan while retaining learned preferences."""
        data = self._load_raw()
        data["chat_history"] = []
        data["pending_clarification"] = None
        data["draft_plan"] = None
        data["current_topic"] = None
        self._save_raw(data)

    def export_to_txt(self):
        """Export current session and user context into a human-readable text document."""
        data = self._load_raw()
        history = data.get("chat_history", [])
        prefs = data.get("user_preferences", {})

        lines = [
            "=" * 70,
            f"   TEACHABLE VOICE ASSISTANT - CONVERSATIONAL MEMORY (SESSION: {self.session_id})",
            "=" * 70,
            f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Stored Dialogue Turns: {len(history)}",
            "",
            "--- [LEARNED USER CONTEXT & PREFERENCES] ---"
        ]

        if not prefs:
            lines.append("  (No specific user preferences saved yet)")
        else:
            for k, v in prefs.items():
                lines.append(f"  • {k}: {v}")

        lines.extend([
            "",
            "--- [RECENT CONVERSATION HISTORY] ---"
        ])

        if not history:
            lines.append("  (No conversation messages yet)")
        else:
            for item in history[-15:]:
                role_label = "USER" if item["role"] == "user" else "ASSISTANT"
                time_str = item.get("timestamp", "").split("T")[-1][:8]
                lines.append(f"  [{time_str}] {role_label}: {item['content']}")

        lines.append("=" * 70)

        with open(self.txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


class SessionManager:
    """Manages active conversational sessions with per-session isolation."""

    def __init__(self, base_dir: Path = DATA_DIR):
        self.base_dir = base_dir
        self.sessions: Dict[str, SessionMemory] = {}

    def get_session(self, session_id: Optional[str] = None) -> SessionMemory:
        sid = (session_id or "default").strip()
        if not sid:
            sid = "default"
        if sid not in self.sessions:
            self.sessions[sid] = SessionMemory(session_id=sid)
        return self.sessions[sid]

    def reset_session(self, session_id: str) -> SessionMemory:
        sess = self.get_session(session_id)
        sess.clear_history()
        return sess

    def cancel_session_workflow(self, session_id: str) -> SessionMemory:
        sess = self.get_session(session_id)
        sess.cancel_active_workflow()
        return sess

    def delete_session(self, session_id: str):
        if session_id in self.sessions:
            self.sessions[session_id].clear_history()
            try:
                self.sessions[session_id].json_path.unlink(missing_ok=True)
                self.sessions[session_id].txt_path.unlink(missing_ok=True)
            except Exception:
                pass
            del self.sessions[session_id]

    def list_active_sessions(self) -> List[str]:
        return list(self.sessions.keys())
