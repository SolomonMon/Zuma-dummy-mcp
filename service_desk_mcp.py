import base64
import binascii
import os
import secrets
import uuid

import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from starlette.responses import JSONResponse, PlainTextResponse


# Credentials are configured in Render, never in GitHub.
AUTH_USERNAME = os.environ.get("MCP_USERNAME", "")
AUTH_PASSWORD = os.environ.get("MCP_PASSWORD", "")

if not AUTH_USERNAME or not AUTH_PASSWORD:
    raise RuntimeError(
        "Set MCP_USERNAME and MCP_PASSWORD in Render Environment."
    )

if ":" in AUTH_USERNAME:
    raise RuntimeError("MCP_USERNAME must not contain a colon.")


mcp = FastMCP(
    "IT-Service-Desk-MCP",
    stateless_http=True,
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000")),
    streamable_http_path="/mcp",
)


# =====================================================================
# IN-MEMORY DEMO DATA
# Changes reset when the server restarts.
# =====================================================================

TICKETS = {
    "TICK-101": {
        "user_id": "usr_882",
        "customer_id": "CUST-104",
        "issue": "CRM Permission Denied on Credit Override",
        "status": "Open",
        "priority": "High",
    },
    "TICK-102": {
        "user_id": "usr_104",
        "customer_id": "CUST-880",
        "issue": "VPN Access Issue",
        "status": "Resolved",
        "priority": "Normal",
    },
}

USER_PERMISSIONS = {
    "usr_882": {
        "name": "Sarah Jenkins",
        "department": "Finance",
        "roles": ["CRM_Viewer", "Ticket_Creator"],
    },
    "usr_104": {
        "name": "Alex Rivera",
        "department": "Sales",
        "roles": ["CRM_Editor", "Credit_Manager"],
    },
}


# =====================================================================
# INVESTIGATION TOOLS
# Configure authorization policies separately in Zuma.
# =====================================================================

@mcp.tool()
def get_ticket_status(
    ticket_id: str = Field(description="Ticket ID, e.g. TICK-101"),
) -> dict:
    """Retrieve IT support ticket status and details."""
    if ticket_id in TICKETS:
        return {"found": True, "ticket": TICKETS[ticket_id]}
    return {"found": False, "error": f"Ticket {ticket_id} not found."}


@mcp.tool()
def search_tickets_by_customer(
    customer_id: str = Field(
        description="CRM Customer ID, e.g. CUST-104"
    ),
) -> dict:
    """Search IT service desk tickets linked to a CRM customer."""
    matches = {
        ticket_id: ticket
        for ticket_id, ticket in TICKETS.items()
        if ticket.get("customer_id") == customer_id
    }
    return {
        "found": bool(matches),
        "count": len(matches),
        "tickets": matches,
    }


@mcp.tool()
def check_user_permissions(
    user_id: str = Field(description="Employee User ID, e.g. usr_882"),
) -> dict:
    """Check system roles and permissions for an employee."""
    if user_id in USER_PERMISSIONS:
        return {"found": True, "user": USER_PERMISSIONS[user_id]}
    return {"found": False, "error": f"User {user_id} not found."}


# =====================================================================
# WORKFLOW TOOLS
# These modify only the in-memory demo tickets.
# =====================================================================

@mcp.tool()
def create_support_ticket(
    user_id: str = Field(description="Employee requesting support"),
    issue_description: str = Field(description="Description of the issue"),
    customer_id: str = Field(
        default="N/A",
        description="Associated CRM Customer ID",
    ),
) -> dict:
    """Create a new IT support ticket."""
    new_id = f"TICK-{uuid.uuid4().hex[:8].upper()}"
    while new_id in TICKETS:
        new_id = f"TICK-{uuid.uuid4().hex[:8].upper()}"

    TICKETS[new_id] = {
        "user_id": user_id,
        "customer_id": customer_id,
        "issue": issue_description,
        "status": "Open",
        "priority": "Normal",
    }
    return {"success": True, "ticket_id": new_id, "status": "Open"}


@mcp.tool()
def escalate_ticket_priority(
    ticket_id: str = Field(description="Ticket ID to escalate"),
    new_priority: str = Field(
        description="New priority: High or Critical"
    ),
    justification: str = Field(description="Reason for escalation"),
) -> dict:
    """Escalate the priority of an existing support ticket."""
    if ticket_id not in TICKETS:
        return {"success": False, "error": "Ticket not found."}

    TICKETS[ticket_id]["priority"] = new_priority
    TICKETS[ticket_id]["escalation_reason"] = justification
    return {
        "success": True,
        "ticket_id": ticket_id,
        "new_priority": new_priority,
    }


# =====================================================================
# SIMULATED HIGH-RISK TOOLS
# No actual privileges, passwords, sessions, or audit logs are changed.
# =====================================================================

@mcp.tool()
def grant_admin_privilege(
    user_id: str = Field(description="User receiving elevated privileges"),
    role: str = Field(
        default="SuperAdmin",
        description="Target privileged role",
    ),
) -> dict:
    """HIGH RISK: Request administrative privileges (simulation)."""
    return {
        "success": True,
        "simulated": True,
        "action": "PRIVILEGE_ELEVATION",
        "user_id": user_id,
        "role": role,
    }


@mcp.tool()
def force_password_reset(
    user_id: str = Field(description="User requiring a password reset"),
    reason: str = Field(description="Administrative reason"),
) -> dict:
    """HIGH RISK: Request password reset and session revocation (simulation)."""
    return {
        "success": True,
        "simulated": True,
        "action": "FORCE_RESET",
        "user_id": user_id,
    }


@mcp.tool()
def purge_audit_logs(
    retention_days: int = Field(
        default=0,
        description="Delete logs older than this many days; 0 means all",
    ),
) -> dict:
    """DESTRUCTIVE: Request deletion of audit logs (simulation)."""
    return {
        "success": True,
        "simulated": True,
        "purged_records": 4820,
    }


# =====================================================================
# HTTP BASIC AUTHENTICATION
# =====================================================================

class BasicAuthMiddleware:
    def __init__(self, app):
        self.app = app

    def credentials_valid(self, scope):
        authorization = next(
            (
                value.decode("latin-1")
                for name, value in scope.get("headers", [])
                if name.lower() == b"authorization"
            ),
            "",
        )

        try:
            scheme, encoded = authorization.split(" ", 1)
            if scheme.lower() != "basic":
                return False

            decoded = base64.b64decode(
                encoded.strip(), validate=True
            ).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (ValueError, UnicodeError, binascii.Error):
            return False

        username_ok = secrets.compare_digest(
            username.encode("utf-8"),
            AUTH_USERNAME.encode("utf-8"),
        )
        password_ok = secrets.compare_digest(
            password.encode("utf-8"),
            AUTH_PASSWORD.encode("utf-8"),
        )
        return username_ok and password_ok

    async def __call__(self, scope, receive, send):
        # Preserve the MCP application's startup/shutdown lifecycle.
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return

        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return

        # Public health check: exposes no tools or demo data.
        if (
            scope["path"] == "/healthz"
            and scope["method"] in ("GET", "HEAD")
        ):
            response = JSONResponse({"status": "ok"})
            await response(scope, receive, send)
            return

        if not self.credentials_valid(scope):
            response = PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Basic realm="ITSM Demo", charset="UTF-8"',
                    "Cache-Control": "no-store",
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


app = BasicAuthMiddleware(mcp.streamable_http_app())


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )
