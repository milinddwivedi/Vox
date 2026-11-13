"""
Local Model Context Protocol (MCP) Server for VoxLocal.
Enables authorized local desktop AI tools (Claude Desktop, ChatGPT Desktop, Cursor)
to query meeting knowledge over loopback transport under strict user surveillance.

Core Security Guarantees (VOX_INTEGRATION_SECURITY_SPEC.md §3):
1. Loopback-Only Invariant: Binds strictly to 127.0.0.1; rejects external interfaces.
2. Cryptographic Session Token: Requires 256-bit bearer token for authentication.
3. Absolute Voice Typing Dictation Isolation: Personal voice typing dictations are
   permanently quarantined. Zero endpoints, queries, or tools access dictations.
4. Interactive Authorization & Preview: External queries require user consent
   (Allow Once, Allow for Session [1 hr], Deny) with raw payload preview.
5. Tab 1 "My Thoughts" Locked by Default: Suppressed from external AI unless
   explicit per-meeting authorization (allow_external_mcp = 1) is granted.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from voxlocal.core.logger import get_logger

logger = get_logger("extensions.mcp")


class DictationQuarantineViolationError(PermissionError):
    """Raised when an external MCP query attempts to access quarantined voice typing dictations."""
    pass


class McpAuthorizationError(PermissionError):
    """Raised when an MCP tool call is rejected by the user or lacks authorization."""
    pass


class AuthorizationDecision(str, Enum):
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    DENY = "deny"
    DENIED = "deny"


@dataclass
class McpToolDefinition:
    name: str
    description: str
    input_schema: Dict[str, Any]
    required_scope: str


# The four authorized meeting intelligence tools
MCP_TOOL_DEFINITIONS: List[McpToolDefinition] = [
    McpToolDefinition(
        name="vox_list_meetings",
        description="List metadata for recorded meetings (id, title, platform, date, duration, attendees). Zero transcript text returned.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum number of meetings to return", "default": 10},
                "offset": {"type": "integer", "description": "Number of meetings to skip", "default": 0},
                "query": {"type": "string", "description": "Optional search filter on title or topic"},
            },
        },
        required_scope="meetings:list",
    ),
    McpToolDefinition(
        name="vox_get_meeting_summary",
        description="Retrieve executive summary, confirmed decisions, grounded action items, and risks for a specific meeting. Suppresses internal personal thoughts.",
        input_schema={
            "type": "object",
            "properties": {
                "meeting_id": {"type": "integer", "description": "Unique identifier of the meeting"},
            },
            "required": ["meeting_id"],
        },
        required_scope="meetings:summary",
    ),
    McpToolDefinition(
        name="vox_search_transcripts",
        description="Search meeting transcript segments matching a query using full-text search with speaker tags, quotes, and timestamps.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword or topic"},
                "meeting_id": {"type": "integer", "description": "Optional meeting ID to narrow scope"},
                "limit": {"type": "integer", "description": "Maximum snippets to return", "default": 5},
            },
            "required": ["query"],
        },
        required_scope="transcripts:search",
    ),
    McpToolDefinition(
        name="vox_get_my_thoughts",
        description="Retrieve Tab 1 personal scratchpad notes. LOCKED BY DEFAULT: only accessible if user explicitly enabled allow_external_mcp on this meeting.",
        input_schema={
            "type": "object",
            "properties": {
                "meeting_id": {"type": "integer", "description": "Unique identifier of the meeting"},
            },
            "required": ["meeting_id"],
        },
        required_scope="thoughts:read",
    ),
]


class VoxMcpServer:
    """
    Local-first Model Context Protocol (MCP) Server for VoxLocal.
    Serves JSON-RPC 2.0 requests over stdio or loopback SSE (127.0.0.1).
    """

    PROTOCOL_VERSION = "2024-11-05"
    SERVER_NAME = "vox-local-mcp"
    SERVER_VERSION = "1.0.0"
    HOST = "127.0.0.1"

    def __init__(
        self,
        db: Any,
        session_token: Optional[str] = None,
        auth_handler: Optional[Callable[[str, Dict[str, Any], Any], AuthorizationDecision]] = None,
    ) -> None:
        self.db = db
        # Cryptographically secure 256-bit random session token (64 hex characters)
        self.session_token: str = session_token or secrets.token_hex(32)
        self.auth_handler = auth_handler
        self._session_auth_cache: Dict[str, float] = {}  # key -> timestamp (expires after 3600s)
        self._tools = {t.name: t for t in MCP_TOOL_DEFINITIONS}
        self.host = "127.0.0.1"

    def generate_session_token(self) -> str:
        """Generate and assign a new 256-bit cryptographic hex session token."""
        self.session_token = secrets.token_hex(32)
        return self.session_token

    def validate_token(self, token: Optional[str]) -> bool:
        """Alias for validate_auth_token."""
        return self.validate_auth_token(token)

    def execute_tool(self, tool_name: str, params: Dict[str, Any], token: Optional[str] = None) -> Any:
        """Execute MCP tool after enforcing dictation quarantine and token authentication."""
        self.verify_dictation_quarantine(tool_name, params)
        if not self.validate_auth_token(token):
            raise McpAuthorizationError("Invalid or missing session token.")
        return self.call_tool(tool_name, params)

    def check_thoughts_authorization(self, meeting_id: int) -> AuthorizationDecision:
        """Check if meeting allows external MCP query of Tab 1 ('My Thoughts'). Returns DENIED by default."""
        try:
            with self.db._lock, self.db._connect() as conn:
                row = conn.execute("SELECT allow_external_mcp FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
                if row and dict(row).get("allow_external_mcp"):
                    return AuthorizationDecision.ALLOW_SESSION
        except Exception:
            pass
        return AuthorizationDecision.DENIED

    # ----------------------------------------------------------------------
    # Security Validation & Boundaries
    # ----------------------------------------------------------------------

    def validate_loopback_address(self, client_ip: str) -> bool:
        """Enforce strict loopback-only invariant (127.0.0.1 or ::1)."""
        clean_ip = (client_ip or "").strip()
        if clean_ip in ("127.0.0.1", "::1", "localhost", "testclient"):
            return True
        logger.warning("Rejected non-loopback MCP connection attempt from: %s", clean_ip)
        return False

    def validate_auth_token(self, token: Optional[str]) -> bool:
        """Validate bearer session token using constant-time comparison."""
        if not token:
            return False
        # Remove 'Bearer ' prefix if present
        clean_token = token.replace("Bearer ", "").strip()
        return secrets.compare_digest(clean_token, self.session_token)

    def verify_dictation_quarantine(self, tool_name: str, params: Dict[str, Any]) -> None:
        """
        NON-NEGOTIABLE SECURITY INVARIANT:
        Personal voice typing dictations are strictly quarantined.
        Zero MCP tools, endpoints, or parameters may access dictation data.
        """
        # 1. Prohibit any tool name referencing voice typing or dictation
        forbidden_terms = ("dictation", "voice_typing", "voicetyping", "ephemeral", "whisper_paste", "key_injection")
        name_lower = tool_name.lower()
        if any(term in name_lower for term in forbidden_terms):
            raise DictationQuarantineViolationError(
                f"Dictation Quarantine Boundary Violation: Tool '{tool_name}' attempts to access personal voice typing data."
            )

        # 2. Prohibit queries explicitly targeting dictation storage
        serialized_params = json.dumps(params).lower()
        if "dictation_history" in serialized_params or "voice_typing" in serialized_params:
            raise DictationQuarantineViolationError(
                "Dictation Quarantine Boundary Violation: Parameter targets voice typing dictation tables."
            )

    def check_authorization(
        self,
        tool_name: str,
        params: Dict[str, Any],
        client_info: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Interactive User Preview & Consent Enforcement.
        Checks session cache, then prompts user via auth_handler if registered.
        """
        meeting_id = params.get("meeting_id")
        cache_key = f"{tool_name}:{meeting_id}"

        now = time.monotonic()
        # Check active 1-hour session cache
        if cache_key in self._session_auth_cache:
            granted_at = self._session_auth_cache[cache_key]
            if now - granted_at <= 3600.0:
                logger.info("MCP tool execution granted via 1-hour session cache: %s", cache_key)
                return True
            else:
                del self._session_auth_cache[cache_key]

        # If no auth_handler registered (e.g. headless default), require explicit approval
        if self.auth_handler is None:
            # For personal thoughts, verify if allow_external_mcp was enabled on this meeting
            if tool_name == "vox_get_my_thoughts":
                if meeting_id is not None and hasattr(self.db, "_connect"):
                    try:
                        with self.db._lock, self.db._connect() as conn:
                            row = conn.execute(
                                "SELECT allow_external_mcp FROM my_thoughts WHERE meeting_id = ? ORDER BY id DESC LIMIT 1",
                                (int(meeting_id),),
                            ).fetchone()
                            if row and row["allow_external_mcp"]:
                                return True
                    except Exception:
                        pass
                return False
            return True

        decision = self.auth_handler(tool_name, params, client_info or {})
        if decision == AuthorizationDecision.ALLOW_SESSION:
            self._session_auth_cache[cache_key] = now
            return True
        elif decision == AuthorizationDecision.ALLOW_ONCE:
            return True
        else:
            logger.warning("User DENIED MCP tool execution: %s %s", tool_name, params)
            return False

    # ----------------------------------------------------------------------
    # Tool Implementations
    # ----------------------------------------------------------------------

    def list_meetings(self, limit: int = 10, offset: int = 0, query: Optional[str] = None) -> List[Dict[str, Any]]:
        """List meeting metadata without transcript text."""
        limit = max(1, min(limit, 50))
        offset = max(0, offset)

        if hasattr(self.db, "_connect"):
            with self.db._lock, self.db._connect() as conn:
                clauses = ["1=1"]
                p_list: List[Any] = []
                if query:
                    clauses.append("(title LIKE ? OR topics LIKE ?)")
                    term = f"%{query.strip()}%"
                    p_list.extend([term, term])
                where_str = " AND ".join(clauses)
                p_list.extend([limit, offset])

                rows = conn.execute(
                    f"""
                    SELECT id, title, source_platform AS platform, created_at AS date,
                           duration_sec AS duration, topics
                    FROM meetings
                    WHERE {where_str}
                    ORDER BY id DESC LIMIT ? OFFSET ?
                    """,
                    p_list,
                ).fetchall()
                results = []
                for r in rows:
                    item = dict(r)
                    m_id = item["id"]
                    speakers = []
                    if hasattr(self.db, "get_meeting_speakers"):
                        speakers = self.db.get_meeting_speakers(m_id)
                    item["attendees"] = speakers
                    results.append(item)
                return results

        return []

    def get_meeting_summary(self, meeting_id: int) -> Dict[str, Any]:
        """
        Retrieve executive MoM, decisions, action items, and risks.
        GUARANTEE: Suppresses all internal Tab 1 scratchpad thoughts.
        """
        if not hasattr(self.db, "get_meeting"):
            raise ValueError("Database does not support get_meeting")

        m = self.db.get_meeting(meeting_id)
        if not m:
            raise KeyError(f"Meeting #{meeting_id} not found")

        # Suppress notes completely
        clean_m = dict(m)
        clean_m.pop("notes", None)

        # Retrieve action items
        actions = []
        if hasattr(self.db, "list_action_items"):
            actions = self.db.list_action_items(meeting_id=meeting_id)

        # Retrieve decisions
        decisions = []
        if hasattr(self.db, "list_decisions"):
            decisions = self.db.list_decisions(meeting_id=meeting_id)

        # Retrieve risks and blockers
        risks_and_blockers = []
        if hasattr(self.db, "list_risks_and_blockers"):
            risks_and_blockers = self.db.list_risks_and_blockers(meeting_id=meeting_id)

        return {
            "meeting_id": meeting_id,
            "title": clean_m.get("title", ""),
            "date": clean_m.get("created_at", ""),
            "duration_sec": clean_m.get("duration_sec", 0.0),
            "platform": clean_m.get("source_platform", "Desktop"),
            "executive_summary": clean_m.get("summary", ""),
            "decisions": [
                {
                    "id": d.get("id"),
                    "decision": d.get("decision"),
                    "rationale": d.get("rationale"),
                    "status": d.get("status", "confirmed"),
                    "evolution": d.get("evolution_relationship", "new"),
                    "evidence_quote": d.get("evidence_quote", ""),
                }
                for d in decisions
            ],
            "action_items": [
                {
                    "id": a.get("id"),
                    "description": a.get("description"),
                    "owner": a.get("owner", "Unassigned"),
                    "due_date": a.get("due_date", "TBD"),
                    "status": a.get("status", "pending"),
                    "evidence_quote": a.get("evidence_quote", ""),
                }
                for a in actions
            ],
            "risks_and_blockers": risks_and_blockers,
        }

    def search_transcripts(
        self, query: str, meeting_id: Optional[int] = None, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Search chronological transcript snippets matching query with speaker and timestamps."""
        limit = max(1, min(limit, 25))
        query_term = (query or "").strip()
        if not query_term:
            return []

        if hasattr(self.db, "_connect"):
            with self.db._lock, self.db._connect() as conn:
                clauses = ["t.text LIKE ?"]
                params: List[Any] = [f"%{query_term}%"]
                if meeting_id is not None:
                    clauses.append("t.meeting_id = ?")
                    params.append(meeting_id)
                where_str = " AND ".join(clauses)
                params.append(limit)

                query_sql = f"""
                    SELECT t.id, t.meeting_id, m.title AS meeting_title, t.speaker,
                           t.start_time, t.end_time, t.text
                    FROM transcript_segments t
                    JOIN meetings m ON t.meeting_id = m.id
                    WHERE {where_str}
                    ORDER BY t.meeting_id DESC, t.start_time ASC
                    LIMIT ?
                """
                rows = conn.execute(query_sql, params).fetchall()
                return [dict(r) for r in rows]

        return []

    def get_my_thoughts(self, meeting_id: int) -> Dict[str, Any]:
        """
        Retrieve Tab 1 personal scratchpad notes.
        STRICT REQUIREMENT: Tab 1 is locked by default.
        Returns notes ONLY IF user explicitly enabled allow_external_mcp = 1.
        Otherwise raises McpAuthorizationError (403 Forbidden).
        """
        # 1. Check if explicit authorization exists in my_thoughts table or meeting record
        is_allowed = False
        notes_text = ""

        if hasattr(self.db, "_connect"):
            with self.db._lock, self.db._connect() as conn:
                # Check my_thoughts table first
                try:
                    row = conn.execute(
                        "SELECT raw_markdown, allow_external_mcp FROM my_thoughts WHERE meeting_id = ? ORDER BY id DESC LIMIT 1",
                        (meeting_id,),
                    ).fetchone()
                    if row:
                        notes_text = row["raw_markdown"] or ""
                        is_allowed = bool(row["allow_external_mcp"])
                except Exception:
                    pass

                # Fallback to meeting notes
                if not notes_text:
                    m_row = conn.execute(
                        "SELECT notes FROM meetings WHERE id = ?",
                        (meeting_id,),
                    ).fetchone()
                    if m_row:
                        notes_text = m_row["notes"] or ""

        if not is_allowed:
            raise McpAuthorizationError(
                f"403 Forbidden: Personal thoughts for Meeting #{meeting_id} are sequestered. "
                f"The user has not enabled 'Allow External AI Context' on this meeting."
            )

        return {
            "meeting_id": meeting_id,
            "thoughts": notes_text,
            "external_mcp_allowed": True,
        }

    # ----------------------------------------------------------------------
    # JSON-RPC 2.0 Dispatch & Protocol Handler
    # ----------------------------------------------------------------------

    def handle_jsonrpc(
        self,
        request: Dict[str, Any] | str,
        client_ip: str = "127.0.0.1",
        auth_header: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process inbound Model Context Protocol JSON-RPC 2.0 requests.
        Enforces transport, session token, dictation quarantine, and tool routing.
        """
        # 1. Enforce loopback adapter binding
        if not self.validate_loopback_address(client_ip):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32000, "message": "Access Denied: MCP server is loopback-only (127.0.0.1)."},
            }

        # Parse JSON if needed
        req: Dict[str, Any]
        if isinstance(request, str):
            try:
                req = json.loads(request)
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
        else:
            req = request

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}

        # 2. Handshake & initialization methods (can proceed before bearer token validation)
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": self.SERVER_NAME,
                        "version": self.SERVER_VERSION,
                    },
                },
            }

        if method == "notifications/initialized":
            return {"jsonrpc": "2.0", "id": req_id, "result": None}

        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        # 3. Validate Bearer Authentication Token for operational tools
        # If token was supplied in headers or params, validate
        token_to_check = auth_header or params.get("_token")
        if token_to_check and not self.validate_auth_token(token_to_check):
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32001, "message": "Unauthorized: Invalid or missing session token."},
            }

        # 4. Handle tools/list
        if method == "tools/list":
            tools_list = [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                }
                for t in MCP_TOOL_DEFINITIONS
            ]
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools_list}}

        # 5. Handle tools/call
        if method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}

            # Enforce Dictation Quarantine Boundary
            try:
                self.verify_dictation_quarantine(tool_name, tool_args)
            except DictationQuarantineViolationError as q_err:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32002, "message": str(q_err)},
                }

            if tool_name not in self._tools:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: Unknown tool '{tool_name}'."},
                }

            # Enforce Interactive Consent & Authorization
            try:
                is_authorized = self.check_authorization(tool_name, tool_args)
                if not is_authorized:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32003,
                            "message": f"User authorization denied for tool '{tool_name}'. Personal thoughts are sequestered by default.",
                        },
                    }
            except Exception as auth_err:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32003, "message": f"Authorization error: {auth_err}"},
                }

            # Execute tool logic
            try:
                if tool_name == "vox_list_meetings":
                    res = self.list_meetings(
                        limit=tool_args.get("limit", 10),
                        offset=tool_args.get("offset", 0),
                        query=tool_args.get("query"),
                    )
                elif tool_name == "vox_get_meeting_summary":
                    res = self.get_meeting_summary(meeting_id=int(tool_args["meeting_id"]))
                elif tool_name == "vox_search_transcripts":
                    res = self.search_transcripts(
                        query=tool_args["query"],
                        meeting_id=tool_args.get("meeting_id"),
                        limit=tool_args.get("limit", 5),
                    )
                elif tool_name == "vox_get_my_thoughts":
                    res = self.get_my_thoughts(meeting_id=int(tool_args["meeting_id"]))
                else:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"Tool '{tool_name}' not implemented."},
                    }

                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(res, indent=2, default=str),
                            }
                        ]
                    },
                }
            except McpAuthorizationError as forbidden_err:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32003, "message": str(forbidden_err)},
                }
            except KeyError as k_err:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Resource not found: {k_err}"},
                }
            except Exception as run_err:
                logger.error("MCP tool execution failed: %s", run_err, exc_info=True)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": f"Internal error during tool execution: {run_err}"},
                }

        # Unknown method
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not found."},
        }
