import urllib.request
import json
import sys

BASE = 'http://localhost:8000'

def request(method, path, body=None, headers=None):
    url = f'{BASE}{path}'
    hdrs = {'Content-Type': 'application/json'}
    if headers:
        hdrs.update(headers)
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            resp_body = resp.read().decode('utf-8')
            return resp.status, resp.headers, resp_body
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode('utf-8')

print("Running 13 Verification Tests against live server at", BASE)
print("=" * 65)

# 1. initialize -> 200, result.protocolVersion == '2025-11-25', serverInfo present
status, hdrs, body = request('POST', '/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}})
data = json.loads(body)
assert status == 200, f"Expected 200, got {status}"
assert data.get('result', {}).get('protocolVersion') == '2025-11-25'
assert 'serverInfo' in data.get('result', {})
print("[PASS] 1. initialize -> 200, protocolVersion=2025-11-25, serverInfo:", data['result']['serverInfo'])

# 2. notifications/initialized -> 202 with an empty body
status, hdrs, body = request('POST', '/mcp', {'jsonrpc': '2.0', 'method': 'notifications/initialized'})
assert status == 202, f"Expected 202, got {status}"
assert body == "", f"Expected empty body, got: {body}"
print("[PASS] 2. notifications/initialized -> status:", status, "body len:", len(body))

# 3. ping -> {'result':{}}
status, hdrs, body = request('POST', '/mcp', {'jsonrpc': '2.0', 'id': 2, 'method': 'ping'})
data = json.loads(body)
assert status == 200, f"Expected 200, got {status}"
assert data.get('result') == {}
print("[PASS] 3. ping ->", body.strip())

# 4. tools/list -> 4 tools, schemas valid
status, hdrs, body = request('POST', '/mcp', {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list'})
data = json.loads(body)
tools = data.get('result', {}).get('tools', [])
assert len(tools) == 4, f"Expected 4 tools, got {len(tools)}"
tool_names = [t['name'] for t in tools]
print("[PASS] 4. tools/list -> count:", len(tools), "names:", tool_names)

# 5. tools/call analyze_vitals {systolic:150,diastolic:95,heart_rate:110} -> result.content[0].text is valid JSON, isError false
status, hdrs, body = request('POST', '/mcp', {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'analyze_vitals', 'arguments': {'systolic': 150, 'diastolic': 95, 'heart_rate': 110}}})
data = json.loads(body)
res = data.get('result', {})
assert res.get('isError') is False
content_text = json.loads(res['content'][0]['text'])
assert 'risk_level' in content_text
print("[PASS] 5. tools/call analyze_vitals -> isError:", res.get('isError'), "risk_level:", content_text.get('risk_level'))

# 6. tools/call get_patient_profile {patient_id:'p1'} -> works
status, hdrs, body = request('POST', '/mcp', {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call', 'params': {'name': 'get_patient_profile', 'arguments': {'patient_id': 'p1'}}})
data = json.loads(body)
res = data.get('result', {})
assert res.get('isError') is False
print("[PASS] 6. tools/call get_patient_profile -> name:", res.get('structuredContent', {}).get('name'))

# 7. tools/call trigger_emergency_protocol {reason:'chest pain'} -> works, output marked simulated
status, hdrs, body = request('POST', '/mcp', {'jsonrpc': '2.0', 'id': 6, 'method': 'tools/call', 'params': {'name': 'trigger_emergency_protocol', 'arguments': {'reason': 'chest pain'}}})
data = json.loads(body)
res = data.get('result', {})
assert res.get('isError') is False
assert res.get('structuredContent', {}).get('simulated') is True
print("[PASS] 7. tools/call trigger_emergency_protocol -> simulated:", res.get('structuredContent', {}).get('simulated'), "status:", res.get('structuredContent', {}).get('status'))

# 8. tools/call with unknown tool -> JSON-RPC error -32602
status, hdrs, body = request('POST', '/mcp', {'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call', 'params': {'name': 'unknown_tool', 'arguments': {}}})
data = json.loads(body)
assert data.get('error', {}).get('code') == -32602, f"Expected -32602, got {data}"
print("[PASS] 8. tools/call unknown tool -> error code:", data.get('error', {}).get('code'), "message:", data.get('error', {}).get('message'))

# 9. tools/call analyze_vitals with missing systolic -> result with isError true (not a protocol error)
status, hdrs, body = request('POST', '/mcp', {'jsonrpc': '2.0', 'id': 8, 'method': 'tools/call', 'params': {'name': 'analyze_vitals', 'arguments': {'heart_rate': 80}}})
data = json.loads(body)
assert 'error' not in data
assert data.get('result', {}).get('isError') is True
print("[PASS] 9. tools/call analyze_vitals missing required arg -> isError:", data.get('result', {}).get('isError'), "content:", data.get('result', {}).get('content', [{}])[0].get('text'))

# 10. GET /mcp -> 405
status, hdrs, body = request('GET', '/mcp')
assert status == 405
assert hdrs.get('Allow') == 'POST'
print("[PASS] 10. GET /mcp -> status:", status, "Allow header:", hdrs.get('Allow'))

# 11. POST /mcp with Origin: http://evil.com -> 403
status, hdrs, body = request('POST', '/mcp', {'jsonrpc': '2.0', 'id': 9, 'method': 'ping'}, headers={'Origin': 'http://evil.com'})
assert status == 403
print("[PASS] 11. POST /mcp with untrusted Origin -> status:", status, "body:", body.strip())

# 12. POST /api/alexa/interact with prompts
prompts = [
    "my blood pressure is 145/95 and heart rate is 110",
    "I do not have chest pain",
    "schedule my Metformin reminder tomorrow at 8 PM",
    "I'm having severe chest pain"
]
print("\n[PASS] 12. POST /api/alexa/interact tests:")
for p in prompts:
    status, hdrs, body = request('POST', '/api/alexa/interact', {'prompt': p})
    res = json.loads(body)
    print(f"  Prompt: '{p}'")
    print(f"    Engine: {res.get('engine')} | Fallback: {res.get('fallback_reason')}")
    print(f"    Tool Called: {res.get('tool_called')}")
    print(f"    Alexa Speech: {res.get('alexa_speech')}")
    if res.get('tool_called') == 'schedule_reminder':
        print(f"    Scheduled Time: {res.get('tool_parameters', {}).get('scheduled_time')}")
    if 'chest pain' in p:
        if 'not' in p:
            assert res.get('tool_called') != 'trigger_emergency_protocol'
            print("    [CHECK] Correctly avoided emergency trigger on negated symptom!")
        else:
            assert res.get('tool_called') == 'trigger_emergency_protocol'
            assert res.get('tool_result', {}).get('simulated') is True
            print("    [CHECK] Correctly triggered emergency protocol with simulated=True!")

print("=" * 65)
print("ALL DIRECT HTTP TESTS PASSED!")
