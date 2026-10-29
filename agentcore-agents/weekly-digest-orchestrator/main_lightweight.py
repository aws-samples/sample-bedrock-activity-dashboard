"""Collects findings from all agents and compiles a weekly digest — deployed to Bedrock AgentCore Runtime.

Lightweight HTTP server pattern for fast cold start (<30s).
"""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

def compile_digest(agent_summaries):
    total_findings = sum(s.get("finding_count", 0) for s in agent_summaries)
    critical = sum(1 for s in agent_summaries for f in s.get("findings", []) if f.get("severity") == "critical")
    digest = {
        "total_findings": total_findings,
        "critical_count": critical,
        "agent_summaries": agent_summaries,
        "delivery_status": "compiled",
    }
    return digest

def handle_invocation(payload):
    data = payload.get("data", {})
    agent_summaries = data.get("agent_summaries", [])
    if not agent_summaries:
        return {"result": "No agent summaries to compile", "findings": [], "finding_count": 0}
    digest = compile_digest(agent_summaries)
    findings = [{
        "digest_period_start": data.get("period_start", ""),
        "digest_period_end": data.get("period_end", ""),
        "included_agent_summaries": digest["agent_summaries"],
        "delivery_status": "compiled",
        "severity": "high" if digest["critical_count"] > 0 else "low",
    }]
    result = f"Weekly digest compiled. {digest['total_findings']} total findings, {digest['critical_count']} critical."
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
