"""Generates Bedrock Guardrail configuration suggestions based on PII findings — deployed to Bedrock AgentCore Runtime.

Lightweight HTTP server pattern for fast cold start (<30s).
"""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

GUARDRAIL_TEMPLATES = {
    "email": {"type": "PII", "action": "ANONYMIZE", "description": "Email addresses detected"},
    "phone": {"type": "PII", "action": "ANONYMIZE", "description": "Phone numbers detected"},
    "ssn": {"type": "PII", "action": "BLOCK", "description": "Social Security Numbers detected"},
    "credit_card": {"type": "PII", "action": "BLOCK", "description": "Credit card numbers detected"},
    "aws_access_key": {"type": "PII", "action": "BLOCK", "description": "AWS access keys detected"},
}

def generate_guardrail_config(pii_types):
    configs = []
    for pii_type in pii_types:
        template = GUARDRAIL_TEMPLATES.get(pii_type, {"type": "PII", "action": "ANONYMIZE", "description": f"{pii_type} detected"})
        configs.append({"pii_type": pii_type, **template})
    return configs

def handle_invocation(payload):
    data = payload.get("data", {})
    pii_findings = data.get("pii_findings", [])
    if not pii_findings:
        return {"result": "No PII findings to process", "findings": [], "finding_count": 0}
    pii_types = list(set(f.get("pii_type", "unknown") for f in pii_findings))
    configs = generate_guardrail_config(pii_types)
    findings = [{"recommendation": c, "based_on_findings": pii_types, "severity": "medium"} for c in configs]
    result = f"Generated {len(configs)} guardrail recommendation(s) for PII types: {', '.join(pii_types)}"
    return {"result": result, "findings": findings, "finding_count": len(findings)}

class AgentHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/invocations":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = {"prompt": body.decode("utf-8", errors="replace")}
            result = handle_invocation(payload)
            resp = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path == "/ping":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"healthy"}')
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    # AgentCore Runtime routes traffic into the container on 8080; binding
    # to 0.0.0.0 is required for the control plane to reach the handler.
    # Network isolation is enforced by the container's security group, not
    # the bind address.
    server = HTTPServer(("0.0.0.0", 8080), AgentHandler)  # nosec B104 - bind required for AgentCore Runtime; isolation via container SG
    print("Agent listening on port 8080")
    server.serve_forever()
