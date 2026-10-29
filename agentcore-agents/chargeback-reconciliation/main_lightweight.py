"""Chargeback Reconciliation Agent — deployed to Bedrock AgentCore Runtime.

Lightweight HTTP server pattern for fast cold start (<30s).
Compares actual AWS Bedrock billing against per-user token usage estimates,
performs proportional allocation, and generates insights.
"""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler


def compare_billing_vs_usage(billing_data, usage_data):
    """Compare actual billing against per-user estimated costs."""
    total_actual = billing_data.get("total_actual", 0.0)
    total_estimated = usage_data.get("total_estimated", 0.0)
    variance = total_actual - total_estimated
    pct = (abs(variance) / total_estimated * 100) if total_estimated > 0 else 0
    return {
        "total_actual": total_actual,
        "total_estimated": total_estimated,
        "variance": round(variance, 4),
        "variance_percentage": round(pct, 2),
        "direction": "over" if variance > 0 else "under",
    }


def allocate_costs(user_estimated, total_actual_cost):
    """Perform proportional allocation."""
    total_estimated = sum(user_estimated.values())
    allocation = {}
    if total_estimated > 0:
        for user, est in user_estimated.items():
            proportion = est / total_estimated
            allocated = total_actual_cost * proportion
            allocation[user] = {
                "estimated": round(est, 4),
                "allocated": round(allocated, 4),
                "variance": round(allocated - est, 4),
                "share_pct": round(proportion * 100, 2),
            }
    elif user_estimated:
        equal = total_actual_cost / len(user_estimated)
        pct = 100.0 / len(user_estimated)
        for user, est in user_estimated.items():
            allocation[user] = {
                "estimated": round(est, 4),
                "allocated": round(equal, 4),
                "variance": round(equal - est, 4),
                "share_pct": round(pct, 2),
            }
    return allocation


def generate_insights(comparison, allocation, team_data=None):
    """Generate natural-language insights."""
    total_actual = comparison.get("total_actual", 0)
    total_estimated = comparison.get("total_estimated", 0)
    variance = comparison.get("variance", 0)
    pct = comparison.get("variance_percentage", 0)
    direction = "higher" if variance > 0 else "lower"

    lines = [
        "Best-Effort Reconciliation: Variance between actual billing and "
        "CloudWatch Logs estimates is expected.",
        "",
        f"Total actual: ${total_actual:,.2f} | Estimated: ${total_estimated:,.2f}",
        f"Variance: ${variance:,.2f} ({pct:.1f}% {direction})",
    ]

    if team_data:
        sorted_teams = sorted(team_data.items(), key=lambda x: x[1].get("allocated", 0), reverse=True)
        if sorted_teams:
            top = sorted_teams[0]
            lines.append(f"Top cost center: {top[0]} (${top[1].get('allocated', 0):,.2f})")

    return "\n".join(lines)


def handle_invocation(payload):
    """Process an invocation payload."""
    data = payload.get("data", {})
    billing_data = data.get("billing_data", {})
    usage_data = data.get("usage_data", {})
    user_estimated = data.get("user_estimated", {})
    total_actual = billing_data.get("total_actual", 0.0)

    comparison = compare_billing_vs_usage(billing_data, usage_data)
    allocation = allocate_costs(user_estimated, total_actual)
    insights = generate_insights(comparison, allocation, data.get("team_data"))

    return {
        "result": insights,
        "findings": [{"comparison": comparison, "allocation": allocation}],
        "finding_count": 1,
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
    print("Chargeback Reconciliation Agent listening on port 8080")
    server.serve_forever()
