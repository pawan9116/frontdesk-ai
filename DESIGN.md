# Design Document

## Architecture Overview

```
                    PSTN
                      |
               +------v------+
               |    Twilio    |
               |  Prog Voice  |
               +------+------+
                      | SIP INVITE
               +------v------+
               | LiveKit      |
               | SIP Trunk    |
               +------+------+
                      |
               +------v------+
               | LiveKit Room |
               |  (audio)     |
               +------+------+
                      |
               +------v------+
               | Agent        |
               | (agent.py)   |
               | STT->LLM->TTS|
               +------+------+
                      | MCP (SSE)
               +------v------+
               | MCP Server   |
               | (mcp_server) |
               +--------------+
```

## Twilio-to-LiveKit Bridging (SIP via TwiML)

Twilio Programmable Voice receives the inbound PSTN call and executes a TwiML Bin containing `<Dial><Sip>`. This dials the LiveKit SIP endpoint directly (e.g. `sip:+15014762841@xxxxx.sip.livekit.cloud`). LiveKit's SIP trunk then handles:
- Audio codec transcoding (PCMU/PCMA to Opus)
- RTP media transport
- Room participant lifecycle

This approach works with Twilio trial accounts (no Elastic SIP Trunking required) and provides production-grade reliability with no custom bridge code.

## Agent Pipeline

```
Caller Audio -> VAD (Silero) -> STT (OpenAI Whisper) -> LLM (GPT-4o-mini) -> TTS (OpenAI) -> Agent Audio
                                                          |
                                                   MCP Tool Calls
```

**Barge-in:** Silero VAD detects user speech during TTS playback. The AgentSession cancels current TTS output immediately, achieving natural barge-in without custom logic.

**Turn-taking:** VAD + end-of-utterance detection prevents talk-over. The agent waits for a pause before responding.

## MCP Tool Architecture

All four business tools are exposed via a FastMCP server (SSE transport). The agent discovers tools at session startup via MCP protocol and presents them to the LLM as function calls.

**Schema validation:** Pydantic models validate all inputs on the MCP server side. Invalid inputs (e.g., non-E.164 phone) return typed error responses, not free-text.

**Idempotency:** `book_appointment` uses `idempotency_key` as a dictionary key. Duplicate keys return the original booking result without creating a new one.

**Audit trace:** Each tool call logs its name, input, output, and success status to an in-memory list. The `get_tool_trace` tool returns and clears this list at conversation end.

## Latency Budget

| Segment | Target | Notes |
|---------|--------|-------|
| SIP setup | ~200ms | One-time per call |
| VAD detection | ~100ms | Silero runs locally |
| STT | ~300ms | OpenAI Whisper streaming |
| LLM (TTFB) | ~200ms | GPT-4o-mini, fast inference |
| TTS (TTFB) | ~150ms | OpenAI TTS streaming |
| **Total TTFB** | **~750ms** | Under 900ms target |

**Measurement:** LiveKit agent metrics (`metrics_collected` event) capture per-segment latency. Logged as structured JSON.

## Observability (OpenTelemetry + Jaeger)

```
Agent (agent.py)           Jaeger UI
    │                      (localhost:16686)
    │ OTel spans                ▲
    ▼                           │
BatchSpanProcessor ──OTLP──▶ Jaeger Collector
                            (localhost:4318)
```

LiveKit Agents SDK generates OpenTelemetry spans internally for every pipeline stage (VAD, STT, LLM, TTS, tool calls). We configure a `TracerProvider` with an OTLP exporter that sends these spans to Jaeger.

**What you see in Jaeger UI:**
- Per-call trace waterfall showing STT → LLM → TTS timing
- Tool call spans with input/output
- Total turn latency (EOU delay + LLM TTFB + TTS TTFB)

**Additionally**, the agent uses LiveKit's `UsageCollector` to aggregate per-session token counts and costs, logged at session end.

**Setup:** `docker run -d -p 16686:16686 -p 4318:4318 jaegertracing/jaeger:latest`

## Scaling to 1k Concurrent Calls

1. **LiveKit:** Horizontal scaling with multiple SFU nodes. LiveKit Cloud auto-scales.
2. **Agent workers:** Stateless; run N replicas behind LiveKit's job dispatch. Each worker handles ~50 concurrent sessions (CPU-bound on VAD).
3. **MCP server:** Stateless per-request. Deploy behind a load balancer. Move fixture data to Redis/Postgres for shared state.
4. **Audit:** Replace in-memory trace with a message queue (e.g., SQS/Kafka). Write audit artifacts to S3/GCS.
5. **Twilio SIP:** LiveKit SIP scales with SFU nodes. Twilio handles PSTN scaling.

## Multi-Tenant Plan

- **Tenant isolation:** Room names prefixed with tenant ID (`tenant-123-call-xyz`).
- **Agent config:** Per-tenant prompt templates and tool configurations loaded from a config store.
- **MCP:** Per-tenant MCP server instances or a shared server with tenant-scoped data access.
- **Audit:** Tenant-scoped storage paths and access controls.

## HIPAA Readiness Outline

1. **PHI in transit:** TLS everywhere (WSS for LiveKit/Twilio, HTTPS for MCP).
2. **PHI at rest:** Encrypt audit artifacts and recordings. Use KMS-managed keys.
3. **PII masking:** Phone numbers masked in logs (last 4 digits shown). Patient names not logged.
4. **Access controls:** Role-based access to audit data. No PHI in application logs.
5. **BAAs:** Required with Twilio, LiveKit Cloud, OpenAI (or use self-hosted LLM).
6. **Audit logging:** Immutable audit trail with tamper-evident checksums.
7. **Data retention:** Configurable retention policies per tenant. Auto-purge after period.
8. **Self-hosted option:** For strictest compliance, run LiveKit server, LLM, and STT/TTS on-premise.
