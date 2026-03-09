"""Audit trail builder for call artifacts (in-memory only)."""

import json
import os
import uuid
from datetime import datetime, timezone


class AuditTrail:
    """Accumulates call data and produces the final audit JSON artifact."""

    def __init__(self, call_id: str | None = None):
        self.call_id = call_id or str(uuid.uuid4())
        self.transcript: list[dict] = []
        self.intents: list[str] = []
        self.slots: dict = {}
        self.tool_trace: list[dict] = []
        self.outcome: dict = {}
        self._start_time = datetime.now(timezone.utc).isoformat()

    def add_user_turn(self, text: str) -> None:
        self.transcript.append({"role": "user", "text": text, "ts": datetime.now(timezone.utc).isoformat()})

    def add_agent_turn(self, text: str) -> None:
        self.transcript.append({"role": "agent", "text": text, "ts": datetime.now(timezone.utc).isoformat()})

    def add_intent(self, intent: str) -> None:
        if intent not in self.intents:
            self.intents.append(intent)

    def set_slots(self, slots: dict) -> None:
        self.slots.update(slots)

    def set_tool_trace(self, trace: list[dict]) -> None:
        self.tool_trace = trace
        tool_to_intent = {
            "check_insurance_coverage": "coverage_check",
            "get_provider_availability": "check_availability",
            "book_appointment": "book_appointment",
            "send_sms": "send_sms",
        }
        for entry in trace:
            intent = tool_to_intent.get(entry.get("tool", ""))
            if intent:
                self.add_intent(intent)

    def set_outcome(self, outcome: dict) -> None:
        self.outcome = outcome

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "transcript": self.transcript,
            "intents": self.intents,
            "slots": self.slots,
            "tool_trace": self.tool_trace,
            "outcome": self.outcome,
        }

    def save(self, directory: str = "data") -> str:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"audit_{self.call_id}.json")
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        return path
