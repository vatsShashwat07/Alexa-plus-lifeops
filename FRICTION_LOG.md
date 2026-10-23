# Friction Log - Alexa+ LifeOps

> **Hackathon:** Amazon Developer - Build, Ship, Shape 2026
> **Track:** Alexa+ MCP Streamable HTTP & AWS Bedrock
> **Author:** Shashwat Pandey
> **Date:** October 2026
> **Purpose:** Developer Experience & Technical Friction Report

---

## 1. What Worked Well

- **MCP Protocol Specification (2025-11-25)**: The Streamable HTTP transport provides a clean contract over standard HTTP POST. Operating via JSON-RPC 2.0 with discrete initialize, ping, and tool execution methods allows tool serving without complex connection state.
- **AWS Bedrock Converse API**: The Converse API eliminates model-specific invocation payloads. The first-class message roles for `toolUse` and `toolResult` make the multi-turn agentic loop straightforward.
- **FastAPI Integration**: Implementing the MCP JSON-RPC 2.0 endpoint with FastAPI was direct, and allowed hosting both the API and the Echo Show companion interface in a single process.
- **Deterministic Offline Fallback**: Predictable exception handling in boto3 (`NoCredentialsError`, `EndpointConnectionError`) allowed clean degradation to a local clinical rule-based engine when cloud credentials are not active.

---

## 2. Technical Friction Log

The following entries document technical obstacles encountered during implementation of the MCP Streamable HTTP transport and Bedrock Converse tool orchestration:

### Entry 1: MCP Streamable HTTP Chunked Buffering in ASGI
- **Task Attempted**: Stream MCP tool responses incrementally via Streamable HTTP through FastAPI and Uvicorn.
- **Steps Taken**: Implemented streaming responses returning chunked payloads from tool executions. Tested with client fetch requests.
- **Expected vs Actual**:
  - *Expected*: Incremental chunks arrive progressively at the client as tool actions occur.
  - *Actual*: Default ASGI/Uvicorn buffers the chunked stream until request completion before flushing to the client, negating streaming benefits unless custom transport buffering flags are enabled.
- **Severity**: Medium
- **Workaround**: Returned complete, well-formed JSON-RPC payloads in a single response cycle for discrete tool calls, ensuring deterministic delivery.
- **Actionable Suggestion**: Publish reference ASGI configurations and an official streaming test harness for Python MCP servers to validate per-chunk delivery across ASGI servers.

---

### Entry 2: Bedrock Converse Nested Schema Strictness on Optional Fields
- **Task Attempted**: Define `analyze_vitals` toolSpec with optional vital parameters (`sp_o2`, `heart_rate`) alongside required blood pressure fields.
- **Steps Taken**: Configured JSON Schema in `toolSpec.inputSchema.json` with properties and listed only required keys in the `"required"` array.
- **Expected vs Actual**:
  - *Expected*: Bedrock Converse accepts toolUse payloads where the model omits unprovided optional fields.
  - *Actual*: The Converse API returned validation errors when optional properties lacked specific typing or when schema unions (`["number", "null"]`) were attempted.
- **Severity**: High
- **Workaround**: Made spot vital parameters nullable or provided fallback defaults, filtering missing parameters before passing to target functions via `inspect.signature`.
- **Actionable Suggestion**: Expand Bedrock Converse documentation with clear examples of optional tool parameters, and support standard JSON Schema `oneOf` or nullable property definitions.

---

### Entry 3: MCP Inspector Origin & Notification Handling in Streamable HTTP
- **Task Attempted**: Connect the official Model Context Protocol inspector (`@modelcontextprotocol/inspector`) to `http://localhost:8000/mcp`.
- **Steps Taken**: Sent initialization handshake and subsequent notification frames from the inspector.
- **Expected vs Actual**:
  - *Expected*: Inspector connects immediately without transport errors.
  - *Actual*: Streamable HTTP clients reject responses to notifications (requests without an `id` member, such as `notifications/initialized`) if any JSON response body is returned. Furthermore, browser security requires strict validation of the `Origin` header.
- **Severity**: Medium
- **Workaround**: Implemented explicit handling returning `202 Accepted` with an empty body for all notification requests, and added local Origin allowlist validation.
- **Actionable Suggestion**: Provide clearer documentation in the MCP Streamable HTTP transport specification regarding exact HTTP status codes and headers expected for notifications vs requests.

---

## 3. Summary & Takeaways

The combination of Model Context Protocol and AWS Bedrock Converse provides a strong foundation for voice-first agentic health applications. Most friction points occurred around transport subtleties (notification response semantics, ASGI buffering) and strict schema validation. Addressing these in official SDK templates will significantly streamline developer adoption.
