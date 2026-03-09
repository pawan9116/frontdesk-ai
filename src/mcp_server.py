"""MCP Server exposing front-office tools for healthcare practices.

Run: python src/mcp_server.py
Connects via SSE on http://localhost:8000/sse
"""

import json
import logging
import re
import uuid
from datetime import datetime

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator

import db
from fixtures import generate_availability_slots

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("mcp-server")

# Initialize DB (creates tables + seeds reference data on first run)
db.init_db()

app = FastMCP("Front Office Tools")

# ── Audit trace accumulator ─────────────────────────────────────────────────
TOOL_TRACES: list[dict] = []


def _log_trace(tool: str, input_data: dict, output_data: dict, ok: bool) -> None:
    entry = {"tool": tool, "input": input_data, "output": output_data, "ok": ok, "ts": datetime.utcnow().isoformat()}
    TOOL_TRACES.append(entry)
    logger.info("tool_call tool=%s ok=%s", tool, ok)


# ── Input models (JSON Schema validation via Pydantic) ──────────────────────

class DateRange(BaseModel):
    start: str = Field(description="ISO date string, e.g. 2025-03-10")
    end: str = Field(description="ISO date string, e.g. 2025-03-11")


class PatientInfo(BaseModel):
    first: str = Field(description="Patient first name")
    last: str = Field(description="Patient last name")
    phone: str = Field(description="Patient phone in E.164 format, e.g. +14085551234")

    @field_validator("phone")
    @classmethod
    def validate_e164(cls, v: str) -> str:
        if not re.match(r"^\+[1-9]\d{6,14}$", v):
            raise ValueError(f"Phone must be E.164 format, got: {v}")
        return v


class SlotInfo(BaseModel):
    start: str = Field(description="ISO datetime for slot start")
    end: str = Field(description="ISO datetime for slot end")


# ── Tools ────────────────────────────────────────────────────────────────────

@app.tool()
def check_insurance_coverage(
    payer: str,
    plan: str,
    procedure_code: str,
    dob: str | None = None,
) -> str:
    """Check insurance coverage for a dental procedure.

    Args:
        payer: Insurance company name, e.g. 'Delta Dental'
        plan: Plan type, e.g. 'PPO', 'HMO'
        procedure_code: ADA procedure code, e.g. 'D1110' for cleaning
        dob: Optional patient date of birth in YYYY-MM-DD format
    """
    input_data = {"payer": payer, "plan": plan, "procedure_code": procedure_code, "dob": dob}

    result = db.get_insurance_rule(payer, plan, procedure_code)

    if result:
        output = dict(result)
    else:
        procedures = db.get_procedure_codes()
        proc = procedures.get(procedure_code.upper())
        cash_price = proc["cash_price"] if proc else 150
        output = {
            "covered": False,
            "copay_estimate": 0,
            "notes": f"No coverage found for {payer} {plan} / {procedure_code}. Cash-pay estimate: ${cash_price}.",
        }

    _log_trace("check_insurance_coverage", input_data, output, True)
    return json.dumps(output)


@app.tool()
def get_provider_availability(
    location_id: str,
    provider_id: str,
    date_range_start: str,
    date_range_end: str,
    appointment_type: str,
) -> str:
    """Get available appointment slots for a provider.

    Args:
        location_id: Location identifier, e.g. 'LOC001'
        provider_id: Provider identifier, e.g. 'PROV001'
        date_range_start: Start date in ISO format, e.g. '2025-03-10'
        date_range_end: End date in ISO format, e.g. '2025-03-11'
        appointment_type: Type of appointment, e.g. 'cleaning', 'exam'
    """
    input_data = {
        "location_id": location_id,
        "provider_id": provider_id,
        "date_range": {"start": date_range_start, "end": date_range_end},
        "appointment_type": appointment_type,
    }

    providers = db.get_providers()
    locations = db.get_locations()

    provider = providers.get(provider_id)
    if not provider:
        output = {"slots": [], "error": f"Unknown provider: {provider_id}"}
        _log_trace("get_provider_availability", input_data, output, False)
        return json.dumps(output)

    if location_id not in locations:
        output = {"slots": [], "error": f"Unknown location: {location_id}"}
        _log_trace("get_provider_availability", input_data, output, False)
        return json.dumps(output)

    if location_id not in provider["locations"]:
        output = {"slots": [], "error": f"Provider {provider_id} not available at {location_id}"}
        _log_trace("get_provider_availability", input_data, output, False)
        return json.dumps(output)

    slots = generate_availability_slots(date_range_start, date_range_end)
    output = {"slots": slots}
    _log_trace("get_provider_availability", input_data, output, True)
    return json.dumps(output)


@app.tool()
def book_appointment(
    patient_first: str,
    patient_last: str,
    patient_phone: str,
    provider_id: str,
    slot_start: str,
    slot_end: str,
    appointment_type: str,
    location_id: str,
    id: str,
) -> str:
    """Book an appointment. Idempotent: same id returns same result.

    Args:
        patient_first: Patient first name
        patient_last: Patient last name
        patient_phone: Patient phone in E.164 format, e.g. '+14085551234'
        provider_id: Provider identifier, e.g. 'PROV001'
        slot_start: ISO datetime for slot start
        slot_end: ISO datetime for slot end
        appointment_type: Type of appointment, e.g. 'cleaning'
        location_id: Location identifier, e.g. 'LOC001'
        id: Unique key to ensure idempotent booking
    """
    input_data = {
        "patient": {"first": patient_first, "last": patient_last, "phone": patient_phone},
        "provider_id": provider_id,
        "slot": {"start": slot_start, "end": slot_end},
        "appointment_type": appointment_type,
        "location_id": location_id,
        "id": id,
    }

    # Validate E.164 phone
    if not re.match(r"^\+[1-9]\d{6,14}$", patient_phone):
        output = {"confirmation_id": None, "status": "failed", "reason": f"Invalid phone format: {patient_phone}. Must be E.164."}
        _log_trace("book_appointment", input_data, output, False)
        return json.dumps(output)

    # Validate provider
    providers = db.get_providers()
    if provider_id not in providers:
        output = {"confirmation_id": None, "status": "failed", "reason": f"Unknown provider: {provider_id}"}
        _log_trace("book_appointment", input_data, output, False)
        return json.dumps(output)

    # Idempotency check
    existing = db.get_booking(id)
    if existing:
        output = {"confirmation_id": existing["confirmation_id"], "status": existing["status"]}
        _log_trace("book_appointment", input_data, output, True)
        return json.dumps(output)

    # Book it
    confirmation_id = f"NRH-{uuid.uuid4().hex[:8].upper()}"
    db.save_booking(
        id=id,
        confirmation_id=confirmation_id,
        patient_first=patient_first,
        patient_last=patient_last,
        patient_phone=patient_phone,
        provider_id=provider_id,
        location_id=location_id,
        appointment_type=appointment_type,
        slot_start=slot_start,
        slot_end=slot_end,
        created_at=datetime.utcnow().isoformat(),
    )

    output = {"confirmation_id": confirmation_id, "status": "booked"}
    logger.info(
        "booking_created confirmation=%s patient=%s %s provider=%s",
        confirmation_id, patient_first, patient_last[0] + "***", provider_id,
    )
    _log_trace("book_appointment", input_data, output, True)
    return json.dumps(output)


@app.tool()
def send_sms(to: str, message: str) -> str:
    """Send an SMS message to a phone number.

    Args:
        to: Recipient phone number in E.164 format, e.g. '+14085551234'
        message: Message text to send
    """
    input_data = {"to": to, "message": message}

    if not re.match(r"^\+[1-9]\d{6,14}$", to):
        output = {"queued": False, "message_id": None, "error": f"Invalid phone format: {to}"}
        _log_trace("send_sms", input_data, output, False)
        return json.dumps(output)

    message_id = f"SMS-{uuid.uuid4().hex[:8].upper()}"
    masked_to = to[:-4] + "****"
    logger.info("sms_queued to=%s message_id=%s", masked_to, message_id)

    db.save_sms(
        message_id=message_id,
        recipient=masked_to,
        message=message,
        created_at=datetime.utcnow().isoformat(),
    )

    output = {"queued": True, "message_id": message_id}
    _log_trace("send_sms", input_data, output, True)
    return json.dumps(output)


@app.tool()
def get_tool_trace() -> str:
    """Return accumulated tool trace for audit purposes and clear the buffer.
    Call this at the very end of the conversation to retrieve the audit trail.
    """
    trace = list(TOOL_TRACES)
    TOOL_TRACES.clear()
    return json.dumps(trace)


if __name__ == "__main__":
    app.run(transport="sse")
