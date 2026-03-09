"""LiveKit Voice Agent for healthcare front-office.

Run: python src/agent.py dev
Requires: MCP server running on http://localhost:8000/sse
"""

import json
import logging
import os
import sys
import time

import httpx
from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentSession,
    AgentServer,
    JobContext,
    RunContext,
    cli,
    function_tool,
    mcp,
    metrics,
    MetricsCollectedEvent,
)
from livekit.agents import inference
from livekit.plugins import openai, silero

# Add src to path for local imports
sys.path.insert(0, os.path.dirname(__file__))
from audit import AuditTrail
from telemetry import setup_langfuse
import db

load_dotenv()

# ── Telemetry + DB init ──────────────────────────────────────────────────────
setup_langfuse()
db.init_db()  # creates tables + seeds reference data on first run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("frontoffice-agent")

# ── Load prompt template (once at startup) ────────────────────────────────
PROMPT_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts", "receptionist", "latest")
with open(os.path.join(PROMPT_DIR, "system.md")) as f:
    _prompt_template = f.read()
with open(os.path.join(PROMPT_DIR, "meta.json")) as f:
    _prompt_meta = json.load(f)

PROMPT_VERSION = _prompt_meta["version"]

PRACTICE_NAME = os.getenv("PRACTICE_NAME", "Smile Dental Care")
PRACTICE_TYPE = os.getenv("PRACTICE_TYPE", "dental")
_db_context = db.build_prompt_context()
SYSTEM_PROMPT = _prompt_template.format(
    practice_name=PRACTICE_NAME,
    practice_type=PRACTICE_TYPE,
    **_db_context,
)

logger.info("prompt_loaded id=%s version=%s practice=%s type=%s", _prompt_meta["id"], PROMPT_VERSION, PRACTICE_NAME, PRACTICE_TYPE)

# ── MCP server URL ──────────────────────────────────────────────────────────
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000/sse")


# ── Agent definition ────────────────────────────────────────────────────────

class FrontOfficeAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=SYSTEM_PROMPT,
        )

    async def on_enter(self) -> None:
        self.session.generate_reply()


# ── Server setup ────────────────────────────────────────────────────────────

server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    audit = AuditTrail()
    call_start = time.monotonic()

    ctx.log_context_fields = {
        "call_id": audit.call_id,
        "room_name": ctx.room.name,
        "prompt_version": PROMPT_VERSION,
    }

    logger.info("call_started call_id=%s room=%s", audit.call_id, ctx.room.name)

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=inference.STT(model="deepgram/nova-3", language="en"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(),
        turn_detection="vad",
        mcp_servers=[
            mcp.MCPServerHTTP(url=MCP_SERVER_URL),
        ],
    )

    # ── Transcript capture ──────────────────────────────────────────────
    @session.on("conversation_item_added")
    def on_conversation_item(ev) -> None:
        item = ev.item
        text = item.text_content or ""
        if not text:
            return
        if item.role == "user":
            audit.add_user_turn(text)
            logger.info("user_turn text=%s", text[:80])
        elif item.role == "assistant":
            audit.add_agent_turn(text)
            logger.info("agent_turn text=%s", text[:80])

    # ── Metrics capture ─────────────────────────────────────────────────
    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def on_metrics(ev: MetricsCollectedEvent) -> None:
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)


    async def _log_usage() -> None:
        summary = usage_collector.get_summary()
        logger.info("usage_summary call_id=%s summary=%s", audit.call_id, summary)

    ctx.add_shutdown_callback(_log_usage)

    # ── Session lifecycle ───────────────────────────────────────────────
    async def _save_session_report() -> None:
        try:
            report = ctx.make_session_report()
            report_dict = report.to_dict()
            report_dir = os.path.join(os.path.dirname(__file__), "..", "session_reports")
            os.makedirs(report_dir, exist_ok=True)
            report_path = os.path.join(report_dir, f"{audit.call_id}.json")
            with open(report_path, "w") as f:
                json.dump(report_dict, f, indent=2)
            logger.info("session_report_saved call_id=%s path=%s", audit.call_id, report_path)
        except Exception as e:
            logger.warning("session_report_failed call_id=%s reason=%s", audit.call_id, e)

    async def _on_close_async() -> None:
        elapsed = time.monotonic() - call_start
        logger.info("call_ending call_id=%s duration=%.1fs", audit.call_id, elapsed)


        # Retrieve tool trace from MCP server
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            async with sse_client(MCP_SERVER_URL) as streams:
                async with ClientSession(*streams) as mcp_session:
                    await mcp_session.initialize()
                    result = await mcp_session.call_tool("get_tool_trace", {})
                    trace_text = result.content[0].text if result.content else "[]"
                    trace = json.loads(trace_text)
                    audit.set_tool_trace(trace)
        except Exception as e:
            logger.error("Failed to retrieve tool trace: %s", e)

        # Derive outcome from tool trace
        outcome = _derive_outcome(audit.tool_trace)
        audit.set_outcome(outcome)

        # Derive slots from transcript context
        slots = _derive_slots(audit.transcript, audit.tool_trace)
        audit.set_slots(slots)

        # Save audit artifact
        path = audit.save()
        logger.info("audit_saved call_id=%s path=%s", audit.call_id, path)

        # Save LiveKit session report
        await _save_session_report()

    @session.on("close")
    def on_close(*args) -> None:
        import asyncio
        asyncio.create_task(_on_close_async())

    await session.start(agent=FrontOfficeAgent(), room=ctx.room)


def _derive_outcome(tool_trace: list[dict]) -> dict:
    """Derive call outcome from tool trace."""
    outcome: dict = {"booked": False, "next_steps": "none"}

    for entry in tool_trace:
        if entry["tool"] == "book_appointment" and entry["ok"]:
            output = entry["output"]
            if isinstance(output, str):
                output = json.loads(output)
            if output.get("status") == "booked":
                outcome["booked"] = True
                outcome["confirmation_id"] = output.get("confirmation_id")

        if entry["tool"] == "send_sms" and entry["ok"]:
            outcome["next_steps"] = "SMS sent"

        if entry["tool"] == "check_insurance_coverage" and entry["ok"]:
            output = entry["output"]
            if isinstance(output, str):
                output = json.loads(output)
            if not output.get("covered"):
                outcome["coverage_denied"] = True

    return outcome


def _derive_slots(transcript: list[dict], tool_trace: list[dict]) -> dict:
    """Derive validated slots from tool trace inputs."""
    slots: dict = {}

    for entry in tool_trace:
        inp = entry.get("input", {})

        if entry["tool"] == "book_appointment":
            patient = inp.get("patient", {})
            slots["patient_first"] = patient.get("first", "")
            slots["patient_last"] = patient.get("last", "")
            slots["phone"] = patient.get("phone", "")
            slots["appointment_type"] = inp.get("appointment_type", "")
            slots["location_id"] = inp.get("location_id", "")
            slots["provider_id"] = inp.get("provider_id", "")
            slot_info = inp.get("slot", {})
            slots["time_pref"] = slot_info.get("start", "")

        if entry["tool"] == "check_insurance_coverage":
            slots["payer"] = inp.get("payer", "")
            slots["plan"] = inp.get("plan", "")

    return slots


if __name__ == "__main__":
    cli.run_app(server)
