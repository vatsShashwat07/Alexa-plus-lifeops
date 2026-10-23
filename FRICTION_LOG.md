# 📋 Friction Log — Alexa+ LifeOps

> **Hackathon:** Amazon Developer — Build, Ship, Shape 2026
> **Track:** Alexa+ MCP Streamable HTTP & AWS Bedrock
> **Author:** Shashwat Pandey
> **Date:** October 2026
> **Purpose:** 10% Judging Bonus — Product Feedback & Developer Experience Report

---

## 1 · What Worked Well ✅

| Area | Details |
|---|---|
| **MCP Protocol Spec (2025-11-25)** | The Streamable HTTP transport spec is well-documented and straightforward to implement as a plain JSON-over-HTTP contract. No WebSocket complexity — a single `POST` endpoint is all you need for tool execution. The manifest + tool-listing convention (`/mcp/v1/tools`) makes client discovery trivial. |
| **Bedrock Converse API** | The unified Converse API is a massive quality-of-life improvement over raw `invoke_model`. Having `toolUse` / `toolResult` as first-class message roles means the agentic loop is clean and model-agnostic — we can swap between foundation models without rewriting orchestration logic. |
| **FastAPI + ASGI Fit** | Building the MCP server on FastAPI felt natural. The JSON-RPC-style contract maps 1:1 to Pydantic models, and the static file serving made bundling the Echo Show UI trivial. |
| **Rule-Based Evaluator Pattern** | The Converse API's predictable error classes (`NoCredentialsError`, `EndpointConnectionError`, `ClientError`) made it straightforward to build a graceful degradation path — the agent works fully offline via a deterministic rule-based clinical evaluator when Bedrock credentials are unavailable. |
| **Developer Onboarding (Bedrock)** | Getting `boto3` → `bedrock-runtime` → `converse()` running took < 30 minutes. The AWS docs for Converse are solid with clear request/response schemas. |

---

## 2 · Areas for Improvement 🔧

### 2.1 MCP Streamable HTTP — Documentation Gaps

The MCP 2025-11-25 spec describes the *protocol contract* but provides no **reference server implementations** for popular frameworks (FastAPI, Express, Spring Boot). A cookiecutter template or official SDK would dramatically reduce boilerplate — we had to hand-roll manifest serving, tool dispatch routing, and MCP session management.

**Suggestion:** Publish official `mcp-server-python` and `mcp-server-node` SDKs with decorators/annotations (e.g., `@mcp_tool(name="analyze_vitals")`) that auto-generate the manifest and handle dispatch.

### 2.2 Bedrock Converse — Tool Schema Strictness

The Bedrock Converse API's `toolSpec.inputSchema` validation is stricter than documented when dealing with optional fields. Nested JSON objects require every property to have an explicit `type` even when the field is optional and may be absent from model output. This created silent failures where the model would omit an optional vitals field and the API would reject the entire tool call.

**Suggestion:** Support `"required": [...]` properly so that unlisted properties are truly optional, or document the strictness level clearly with examples of optional nested fields.

### 2.3 Alexa+ MCP Inspector Tooling

There is no official developer inspector or debug UI for MCP Streamable HTTP traffic. We built a custom protocol inspector panel from scratch to visualize request/response payloads, tool invocations, and timing. An official Chrome DevTools-style inspector (similar to the GraphQL explorer) would be invaluable.

**Suggestion:** Ship an `mcp-inspector` CLI or browser extension that auto-discovers local MCP servers and provides a live traffic view with payload inspection.

---

## 3 · Structured Friction Log Table

| # | Task Attempted | Steps Taken | Expected | Actual | Severity | Workaround Used | Actionable Suggestion |
|---|---|---|---|---|---|---|---|
| **1** | **Stream MCP tool responses via Streamable HTTP through ASGI (Uvicorn)** | Implemented `/mcp/v1/tools/call` returning chunked `Transfer-Encoding: chunked` JSON responses from FastAPI. Tested with the Echo Show UI fetching tool results. | Chunked response body should arrive incrementally in the browser, allowing progressive rendering of long tool outputs (e.g., multi-step emergency protocol results). | **Uvicorn's ASGI layer and certain reverse proxies (nginx default config) buffer the entire chunked response before forwarding**, defeating the purpose of streaming. The browser `fetch()` only receives the complete body after the server closes the connection. No partial chunks are delivered. This is especially problematic for long-running tool executions (emergency dispatch with multiple sub-steps). | **Major** | Switched to returning complete JSON responses in a single flush instead of chunked streaming. For the hackathon, latency was acceptable (< 2s), but this approach won't scale for real-time multi-step tool pipelines. Also tested adding `X-Accel-Buffering: no` header for nginx, which partially helped. | **1)** Document recommended ASGI server settings for streaming (e.g., Uvicorn `--http h11` vs `httptools`, Hypercorn config). **2)** Provide a reference streaming implementation that explicitly flushes per-chunk using ASGI `send()` with `more_body=True`. **3)** Add an MCP transport conformance test suite that validates chunk-by-chunk delivery. |
| **2** | **Define Bedrock Converse `toolSpec` for `analyze_vitals` with optional vital metrics** | Defined `inputSchema` with properties `systolic`, `diastolic`, `heart_rate`, `sp_o2`, `temperature`, `respiratory_rate`. Marked only `systolic`, `diastolic`, and `heart_rate` as `required`. Other fields (`sp_o2`, `temperature`, `respiratory_rate`) were intentionally optional — users may only report "blood pressure is 145/95 and pulse is 108" without SpO2 or temperature. | Bedrock Converse should accept the tool call when the model omits optional properties from its `toolUse` input JSON. The `required` array should be the source of truth for which fields must be present. | **Bedrock Converse API returned a `ValidationException` when the model's `toolUse` input JSON omitted optional nested properties** that had a defined `type` in the schema but were not in `required`. The error message was generic (`invalid tool input`) with no indication of *which* field caused the rejection. Additionally, when we added `"type": ["number", "null"]` union syntax for optional fields, Bedrock rejected the schema itself — it does not support JSON Schema union types in `inputSchema`. | **Major** | **1)** Changed all optional vital fields to `required` in the schema and set sensible defaults (e.g., `sp_o2: 98`, `temperature: 98.6`) in the tool implementation when values are `0` or sentinel. **2)** Added explicit instructions in the Bedrock system prompt: *"Always provide all vital parameters when calling analyze_vitals. Use 0 for unknown values."* This forced the model to always emit every field. **3)** Wrapped tool execution in a try/catch to retry with defaults on validation failure. | **1)** Support JSON Schema `"type": ["number", "null"]` or `oneOf` for optional fields in `toolSpec.inputSchema`. **2)** Return field-level validation errors instead of a generic `ValidationException` — e.g., `"missing required field: sp_o2 at toolUse.input.sp_o2"`. **3)** Document the exact JSON Schema subset supported by Converse `inputSchema` (Draft-04? Draft-07?) with explicit examples of optional fields. |

---

## 4 · Summary & Recommendations

### For the MCP Streamable HTTP Team:
1. **Reference implementations** in Python/Node/Java would cut onboarding from hours to minutes.
2. **Streaming conformance tests** — a test harness that validates chunk-level delivery would prevent ASGI/proxy buffering surprises.
3. **Official inspector tooling** — live MCP traffic visualization is essential for debugging agent ↔ tool interactions.

### For the AWS Bedrock Team:
1. **Flexible `inputSchema` validation** — honor `required` arrays and support union types for optional tool parameters.
2. **Granular error messages** — field-level validation feedback on `toolUse` input would save hours of debugging.
3. **Tool call dry-run mode** — a `/converse/validate` endpoint that checks tool schemas without executing would accelerate development.

---

> **Overall Developer Experience: 8/10**
> The core primitives (MCP protocol, Bedrock Converse) are excellent and well-designed. The friction is primarily at the *edges* — streaming transport behavior, schema validation strictness, and missing developer tooling. These are all solvable and would elevate the platform from great to best-in-class.
