"""CUR Ingestion agent package — deterministic, runs on the backend direct-execution path.

This package exists for consistency with the other agent packages. The actual
CUR 2.0 billed-cost ingestion (Athena query -> upsert into cur_billed_cost)
executes deterministically inside the backend via CurIngestionService; it is
never routed through AgentCore Runtime. This lightweight HTTP server only
answers health checks and returns an informational response on invocation.
"""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler


def handle_invocation(payload):
    return {
        "result": (
            "CUR Ingestion is a deterministic agent. Ingestion runs on the backend "
            "direct-execution path (CurIngestionService) and on a daily EventBridge "
            "Scheduler -> ECS RunTask schedule; it is not executed via AgentCore."
        ),
        "findings": [],
        "finding_count": 0,
    }


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
