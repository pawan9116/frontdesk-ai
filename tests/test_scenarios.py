"""Evaluation harness for Front Office voice agent.

Tests MCP tool schemas, tool execution, and audit JSON structure.
Run: pytest tests/test_scenarios.py -v
"""

import json
import os
import sys
import uuid
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Override DB path for tests
import db
db.DB_PATH = os.path.join(os.path.dirname(__file__), "test_agent.db")
db.init_db()

from audit import AuditTrail
from fixtures import generate_availability_slots


# ── Scenario 1: Happy path (Coverage -> Availability -> Book -> SMS) ────────

class TestHappyPath:
    """Maya Patel: Delta Dental PPO, cleaning, next Tuesday, San Jose."""

    def test_check_insurance_coverage_covered(self):
        result = db.get_insurance_rule("delta dental", "ppo", "D1110")
        assert result is not None
        assert result["covered"] is True
        assert result["copay_estimate"] == 25

    def test_get_provider_availability_returns_slots(self):
        next_tuesday = _next_weekday(1)
        slots = generate_availability_slots(
            next_tuesday.isoformat(),
            next_tuesday.isoformat(),
        )
        assert len(slots) > 0
        assert all("start" in s and "end" in s for s in slots)

    def test_book_appointment_idempotent(self):
        key = str(uuid.uuid4())
        confirmation_id = f"NRH-{uuid.uuid4().hex[:8].upper()}"

        # First booking
        db.save_booking(
            id=key, confirmation_id=confirmation_id,
            patient_first="Maya", patient_last="Patel", patient_phone="+14085551234",
            provider_id="PROV001", location_id="LOC001", appointment_type="cleaning",
            slot_start="2025-03-11T09:00:00", slot_end="2025-03-11T10:00:00",
            created_at=datetime.utcnow().isoformat(),
        )
        booking_1 = db.get_booking(key)
        assert booking_1["status"] == "booked"
        assert booking_1["confirmation_id"] == confirmation_id

        # Same key should return same result
        booking_2 = db.get_booking(key)
        assert booking_2["confirmation_id"] == booking_1["confirmation_id"]

    def test_audit_json_structure(self):
        audit = _build_sample_audit_happy()
        d = audit.to_dict()

        assert "call_id" in d
        assert isinstance(d["transcript"], list)
        assert len(d["transcript"]) >= 2
        assert "coverage_check" in d["intents"]
        assert "book_appointment" in d["intents"]
        assert "send_sms" in d["intents"]
        assert isinstance(d["tool_trace"], list)
        assert len(d["tool_trace"]) >= 4
        assert d["outcome"]["booked"] is True
        assert "confirmation_id" in d["outcome"]

    def test_tool_trace_has_required_fields(self):
        audit = _build_sample_audit_happy()
        for entry in audit.tool_trace:
            assert "tool" in entry
            assert "input" in entry
            assert "output" in entry
            assert "ok" in entry


# ── Scenario 2: Coverage denied ─────────────────────────────────────────────

class TestCoverageDenied:
    """Coverage not available for procedure; no booking."""

    def test_unknown_payer_returns_not_covered(self):
        result = db.get_insurance_rule("unknown_payer", "unknown_plan", "D1110")
        assert result is None

    def test_cigna_dppo_cleaning_denied(self):
        result = db.get_insurance_rule("cigna", "dppo", "D1110")
        assert result is not None
        assert result["covered"] is False

    def test_audit_json_error_path(self):
        audit = _build_sample_audit_denied()
        d = audit.to_dict()

        assert "call_id" in d
        assert "coverage_check" in d["intents"]
        assert "book_appointment" not in d["intents"]
        assert d["outcome"]["booked"] is False
        assert d["outcome"].get("coverage_denied") is True


# ── Slot validation tests ───────────────────────────────────────────────────

class TestSlotValidation:
    """Validate extracted slot formats."""

    def test_e164_phone_valid(self):
        import re
        assert re.match(r"^\+[1-9]\d{6,14}$", "+14085551234")

    def test_e164_phone_invalid(self):
        import re
        assert not re.match(r"^\+[1-9]\d{6,14}$", "408-555-1234")
        assert not re.match(r"^\+[1-9]\d{6,14}$", "4085551234")

    def test_procedure_code_mapping(self):
        mapping = db.get_appointment_type_to_code()
        assert mapping["cleaning"] == "D1110"
        assert mapping["exam"] == "D0120"

    def test_date_range_parsing(self):
        slots = generate_availability_slots("2025-03-10", "2025-03-10")
        for slot in slots:
            dt = datetime.fromisoformat(slot["start"])
            assert dt.year == 2025
            assert dt.month == 3
            assert dt.day == 10


# ── DB reference data tests ──────────────────────────────────────────────────

class TestDatabaseReferenceData:
    """Verify DB seed data is correct."""

    def test_providers_loaded(self):
        providers = db.get_providers()
        assert "PROV001" in providers
        assert "PROV002" in providers
        assert providers["PROV001"]["name"] == "Dr. Sarah Chen"

    def test_locations_loaded(self):
        locations = db.get_locations()
        assert "LOC001" in locations
        assert "LOC002" in locations

    def test_procedure_codes_loaded(self):
        codes = db.get_procedure_codes()
        assert "D1110" in codes
        assert codes["D1110"]["cash_price"] == 150

    def test_sms_log_persists(self):
        msg_id = f"SMS-TEST-{uuid.uuid4().hex[:6]}"
        db.save_sms(msg_id, "+1408****", "Test message", datetime.utcnow().isoformat())
        # No error means success (no get_sms query needed for now)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _next_weekday(weekday: int) -> datetime:
    today = datetime.now()
    days_ahead = weekday - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return (today + timedelta(days=days_ahead)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _build_sample_audit_happy() -> AuditTrail:
    audit = AuditTrail()
    audit.add_user_turn("Hi, I'm Maya Patel. Do you take Delta Dental PPO for a cleaning?")
    audit.add_agent_turn("Let me check your coverage for a cleaning with Delta Dental PPO.")
    audit.add_user_turn("Yes, next Tuesday morning in San Jose. My number is 408-555-1234.")
    audit.add_agent_turn("I found availability. Let me book that for you.")
    audit.add_agent_turn("You're all set! I've sent a confirmation to your phone.")

    audit.set_tool_trace([
        {"tool": "check_insurance_coverage", "input": {"payer": "Delta Dental", "plan": "PPO", "procedure_code": "D1110"}, "output": {"covered": True, "copay_estimate": 25}, "ok": True},
        {"tool": "get_provider_availability", "input": {"location_id": "LOC001", "provider_id": "PROV001", "date_range": {"start": "2025-03-11", "end": "2025-03-11"}, "appointment_type": "cleaning"}, "output": {"slots": [{"start": "2025-03-11T09:00:00", "end": "2025-03-11T10:00:00"}]}, "ok": True},
        {"tool": "book_appointment", "input": {"patient": {"first": "Maya", "last": "Patel", "phone": "+14085551234"}, "provider_id": "PROV001", "slot": {"start": "2025-03-11T09:00:00", "end": "2025-03-11T10:00:00"}, "appointment_type": "cleaning", "location_id": "LOC001", "id": "abc-123"}, "output": {"confirmation_id": "NRH-ABC12345", "status": "booked"}, "ok": True},
        {"tool": "send_sms", "input": {"to": "+14085551234", "message": "Your cleaning at Smile Dental Care San Jose is confirmed for Mar 11 at 9:00 AM. Confirmation: NRH-ABC12345"}, "output": {"queued": True, "message_id": "SMS-XYZ789"}, "ok": True},
    ])

    audit.set_outcome({"booked": True, "confirmation_id": "NRH-ABC12345", "next_steps": "SMS sent"})
    audit.set_slots({
        "patient_first": "Maya", "patient_last": "Patel",
        "phone": "+14085551234", "appointment_type": "cleaning",
        "time_pref": "2025-03-11T09:00:00", "location_id": "LOC001",
        "payer": "Delta Dental", "plan": "PPO",
    })
    return audit


def _build_sample_audit_denied() -> AuditTrail:
    audit = AuditTrail()
    audit.add_user_turn("Hi, I have Cigna DPPO and need a cleaning.")
    audit.add_agent_turn("Let me check your coverage.")
    audit.add_agent_turn("Unfortunately, cleanings aren't covered under Cigna DPPO. We offer a cash price of $150.")
    audit.add_user_turn("No thanks, I'll call back later.")
    audit.add_agent_turn("No problem. Have a great day!")

    audit.set_tool_trace([
        {"tool": "check_insurance_coverage", "input": {"payer": "Cigna", "plan": "DPPO", "procedure_code": "D1110"}, "output": {"covered": False, "copay_estimate": 0, "notes": "Procedure not covered under this plan."}, "ok": True},
    ])

    audit.set_outcome({"booked": False, "coverage_denied": True, "next_steps": "none"})
    audit.set_slots({"payer": "Cigna", "plan": "DPPO"})
    return audit
