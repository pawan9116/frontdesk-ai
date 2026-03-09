# Front Office AI - Voice Agent

Real-time voice agent for healthcare front-office workflows. Handles insurance verification, appointment booking, and SMS confirmations via phone call. Configurable for any practice type (dental, ophthalmology, primary care, etc.) via environment variables.

## Architecture

- **Twilio** - PSTN phone number for inbound calls
- **LiveKit** - Real-time audio room where the agent runs
- **OpenAI** - LLM (GPT-4o-mini), TTS
- **Deepgram** - STT (Nova-3 via LiveKit Inference)
- **MCP Server** - Tool execution with JSON schema validation
- **SQLite** - Reference data, bookings, SMS logs

See [DESIGN.md](DESIGN.md) for detailed architecture and tradeoffs.

---

## Call Test

| Item | Value |
|------|-------|
| **Phone Number** | `+1 (xxx) xxx-xxxx` *(update after provisioning)* |
| **Passcode** | None (Twilio trial: press any key after the trial message) |
| **Test Window** | Sat-Sun, 10 AM - 6 PM PST |
| **Try saying** | "Hi, I'm Maya Patel. Do you take Delta Dental PPO for a cleaning? Next Tuesday morning in San Jose. My number is 408-555-1234." |

---

## Prerequisites

- Python 3.11+ (3.12 recommended)
- [LiveKit Cloud account](https://cloud.livekit.io) (free tier works) or self-hosted LiveKit server
- [Twilio account](https://www.twilio.com/try-twilio) (trial OK)
- [OpenAI API key](https://platform.openai.com/api-keys)
- [LiveKit CLI](https://docs.livekit.io/home/cli/install/) (`lk`)

## Setup

### 1. Clone and install

```bash
git clone <repo-url> && cd frontdesk-ai
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Set up Twilio + LiveKit SIP

```bash
# Run the setup script to create SIP trunk and dispatch rule
python setup_infra.py
```

This will:
1. Create a LiveKit SIP inbound trunk for your Twilio number
2. Create a dispatch rule to route calls to rooms prefixed `call-`
3. Print the TwiML Bin XML to configure in Twilio

Then in the Twilio console:
1. Go to [TwiML Bins](https://console.twilio.com/us1/develop/twiml-bins) → Create new → paste the printed XML
2. Go to **Phone Numbers** → your number → **Voice Configuration**
3. Set "A call comes in" → **TwiML Bin** → select the one you created

### 4. Run

Terminal 1 - MCP Server:
```bash
python src/mcp_server.py
```

Terminal 2 - Agent:
```bash
python src/agent.py dev
```

### 5. Test

Call your Twilio number. The agent will greet you and guide through the workflow.

## Conversation Scenarios

### Scenario 1: Happy Path (EN)
> "Hi, I'm Maya Patel. Do you take Delta Dental PPO for a cleaning? If yes, next Tuesday morning in San Jose. My number is 408-555-1234."

Expected: Coverage check (covered, $25 copay) -> Availability -> Booking -> SMS confirmation.

### Scenario 2: Coverage Denied
> "Hi, I have Cigna DPPO and need a cleaning."

Expected: Coverage check (denied) -> Cash-pay offer ($150) -> No booking.

## Project Structure

```
src/
  agent.py          # LiveKit voice agent (main entrypoint)
  mcp_server.py     # MCP tool server (4 business tools + audit)
  audit.py          # Audit JSON artifact builder (in-memory)
  db.py             # SQLite for reference data, bookings, SMS logs
  fixtures.py       # Availability slot generator
  telemetry.py      # Langfuse tracing via OpenTelemetry
prompts/
  receptionist/
    v1.3.0/         # Versioned prompt template (system.md + meta.json)
    latest -> v1.3.0
data/
  agent.db          # SQLite database (auto-created)
tests/
  test_scenarios.py # Evaluation harness (16 tests)
sample_outputs/
  success.json      # Sample happy-path audit artifact
  error_coverage_denied.json  # Sample error-path artifact
setup_infra.py      # SIP trunk + dispatch rule setup
DESIGN.md           # Architecture doc
```

## Running Tests

```bash
pytest tests/test_scenarios.py -v
```

## Observability (Langfuse via OpenTelemetry)

Traces are exported to [Langfuse](https://langfuse.com) via OpenTelemetry OTLP. Enable by setting these in `.env`:

```bash
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

Each call produces a trace with spans for STT, LLM, TTS, and tool calls. View traces at your Langfuse dashboard.

The agent also logs per-session usage summaries (token counts, costs) via LiveKit's `UsageCollector` at session end.

## Deployment

Two Dockerfiles for separate services:

- `Dockerfile` — Agent (connects to LiveKit Cloud)
- `Dockerfile.mcp` — MCP Server (tool execution)

CI/CD via GitHub Actions deploys to Railway on push to `main`. See `.github/workflows/deploy.yml`.

```bash
# Local Docker run
docker build -t frontdesk-agent . && docker run --env-file .env frontdesk-agent
docker build -t frontdesk-mcp -f Dockerfile.mcp . && docker run --env-file .env -p 8000:8000 frontdesk-mcp
```

## Performance Notes

- **TTFB target:** <=900ms (measured via Langfuse traces)
- **p95 turn latency:** <=2.5s target
- Metrics logged as structured JSON via `metrics_collected` event
- See `data/` directory for per-call audit artifacts after calls

## Reliability

- MCP tools return typed errors (not free-text) on invalid input
- `book_appointment` is idempotent via `id`
- Agent retries tool calls on transient failures (MCP client handles reconnection)
- PII masked in all logs (phone numbers show last 4 digits only)

## Known Limitations

- Reference data is seeded on first run; delete `data/agent.db` to reset
- Single-session tool trace (no concurrent call isolation in trace buffer)
- Twilio trial accounts play a message before connecting; callers must press a key
- LiveKit SIP requires Cloud or self-hosted with SIP module enabled

## Security

- No secrets committed (`.env` in `.gitignore`)
- PII masked in structured logs
- All inter-service communication over TLS in production
- See DESIGN.md for HIPAA readiness outline
