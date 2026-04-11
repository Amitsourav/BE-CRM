import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional


class CallState:
    """Stores state for a single active call."""

    def __init__(
        self,
        call_id: str,
        agent_id: str,
        lead_id: str,
        company_id: str,
        lead_name: str = "there",
    ):
        self.call_id = call_id
        self.agent_id = agent_id
        self.lead_id = lead_id
        self.company_id = company_id
        self.lead_name = lead_name
        self.welcome_audio: bytes = b""
        self.welcome_audio_b64: str = ""  # pre-encoded mulaw+base64 for instant play
        self.welcome_ready: "asyncio.Event | None" = None
        # Cached AIAgent ORM instance so the WS handler can skip the
        # second Supabase lookup. Populated by /voice/outbound.
        self.agent = None
        self.conversation_history: list = []
        self.transcript_segments: list = []
        self.current_language = "en"
        self.started_at = datetime.utcnow()
        self.is_active = True
        self.is_agent_speaking = False
        self.total_turns = 0

    def add_turn(self, user_text: str, agent_text: str, language: str):
        self.conversation_history.append(
            {"role": "user", "content": user_text}
        )
        self.conversation_history.append(
            {"role": "assistant", "content": agent_text}
        )
        self.transcript_segments.append(
            {
                "turn": self.total_turns + 1,
                "user": user_text,
                "agent": agent_text,
                "language": language,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        self.current_language = language
        self.total_turns += 1

    def get_full_transcript(self) -> str:
        lines = []
        for seg in self.transcript_segments:
            lines.append(f"User: {seg['user']}")
            lines.append(f"Agent: {seg['agent']}")
        return "\n".join(lines)

    def get_duration_seconds(self) -> int:
        delta = datetime.utcnow() - self.started_at
        return int(delta.total_seconds())


class CallStateManager:
    """Manages all active calls in memory."""

    def __init__(self):
        self._calls: Dict[str, CallState] = {}

    def create(
        self,
        call_id: str,
        agent_id: str,
        lead_id: str,
        company_id: str,
        lead_name: str = "there",
        welcome_audio: bytes = b"",
    ) -> CallState:
        state = CallState(
            call_id=call_id,
            agent_id=agent_id,
            lead_id=lead_id,
            company_id=company_id,
            lead_name=lead_name,
        )
        state.welcome_audio = welcome_audio
        self._calls[call_id] = state
        return state

    def get(self, call_id: str) -> Optional[CallState]:
        return self._calls.get(call_id)

    def remove(self, call_id: str):
        if call_id in self._calls:
            del self._calls[call_id]

    def cleanup_stale(self, max_age_minutes: int = 30) -> int:
        """Remove call states older than max_age_minutes. Returns count removed.

        Guards against orphaned state when WebSocket errors out before /hangup.
        """
        cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
        stale_ids = [
            cid for cid, s in self._calls.items() if s.started_at < cutoff
        ]
        for cid in stale_ids:
            del self._calls[cid]
        return len(stale_ids)

    def get_all_active(self, company_id: Optional[str] = None) -> list:
        return [
            {
                "call_id": s.call_id,
                "lead_id": s.lead_id,
                "duration": s.get_duration_seconds(),
                "turns": s.total_turns,
            }
            for s in self._calls.values()
            if s.is_active and (company_id is None or s.company_id == company_id)
        ]


call_state_manager = CallStateManager()
