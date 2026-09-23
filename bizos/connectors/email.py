"""
Email Connectors
================

Two adapters behind one capability surface:

``WorkspaceEmailConnector`` — a working mailbox backed by the client's
``email_messages`` / ``email_drafts`` tables. Drafting and sending are genuinely
different operations against genuinely different tables, so "drafted but not
sent" is an observable database state, which is what makes DRAFT mode testable.

``GmailConnector`` — the live adapter, built on agno's ``GmailTools``. It is wired
but **not exercised in this deployment** (no Google credentials), so it refuses
clearly rather than half-working. See CONNECTORS.md.

Message bodies are external content and are neutralized on the way out (§20).
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text

from bizos.connectors.base import Connector, ConnectorNotConfigured, ConnectorResult
from bizos.connectors.untrusted import wrap_many
from bizos.tenancy.registry import readonly_connection, workspace_connection
from bizos.util.ids import new_id


class WorkspaceEmailConnector(Connector):
    category = "email"
    provider = "workspace"

    def health(self) -> ConnectorResult:
        with readonly_connection(self.ctx) as conn:
            messages = conn.execute(text("SELECT count(*) FROM email_messages")).scalar()
            drafts = conn.execute(text("SELECT count(*) FROM email_drafts")).scalar()
        return ConnectorResult(
            ok=True, data={"messages": messages, "drafts": drafts}, summary=f"{messages} messages, {drafts} drafts"
        )

    # -- reads --------------------------------------------------------------

    def search_threads(self, query: str = "", *, limit: int = 20) -> ConnectorResult:
        params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
        where = ""
        if query:
            where = "WHERE subject ILIKE :q OR body ILIKE :q OR sender ILIKE :q OR :qe = ANY(recipients)"
            params["q"] = f"%{query}%"
            params["qe"] = query
        with readonly_connection(self.ctx) as conn:
            rows = conn.execute(
                text(
                    f"SELECT DISTINCT ON (thread_id) thread_id, subject, sender, recipients, sent_at "
                    f"FROM email_messages {where} ORDER BY thread_id, sent_at DESC LIMIT :limit"
                ),
                params,
            ).fetchall()
        threads = [dict(r._mapping) for r in rows]
        # Subjects are external content too.
        threads, findings = wrap_many(threads, source="email_subject", text_field="subject", id_field="thread_id")
        return ConnectorResult(
            ok=True, data=threads, summary=f"{len(threads)} thread(s)", untrusted_findings=findings
        )

    def read_thread(self, thread_id: str) -> ConnectorResult:
        """Read one thread. Bodies are neutralized before they reach the model."""
        with readonly_connection(self.ctx) as conn:
            rows = conn.execute(
                text("SELECT * FROM email_messages WHERE thread_id = :t ORDER BY sent_at"),
                {"t": thread_id},
            ).fetchall()
        if not rows:
            return ConnectorResult.failure(f"No thread {thread_id!r}")
        messages, findings = wrap_many(
            [dict(r._mapping) for r in rows], source="email_body", text_field="body"
        )
        return ConnectorResult(
            ok=True,
            data={"thread_id": thread_id, "messages": messages},
            summary=f"{len(messages)} message(s) in thread {thread_id}",
            untrusted_findings=findings,
        )

    def list_drafts(self, *, limit: int = 50) -> ConnectorResult:
        with readonly_connection(self.ctx) as conn:
            rows = conn.execute(
                text("SELECT * FROM email_drafts ORDER BY created_at DESC LIMIT :l"),
                {"l": max(1, min(limit, 200))},
            ).fetchall()
        return ConnectorResult(ok=True, data=[dict(r._mapping) for r in rows], summary=f"{len(rows)} draft(s)")

    # -- writes -------------------------------------------------------------

    def create_draft(
        self,
        *,
        recipients: list[str],
        subject: str,
        body: str,
        cc: Optional[list[str]] = None,
        thread_id: Optional[str] = None,
        action_id: Optional[str] = None,
    ) -> ConnectorResult:
        """Prepare a draft. This never sends — a different table, a different verb."""
        draft_id = new_id("draft")
        with workspace_connection(self.ctx) as conn:
            conn.execute(
                text(
                    "INSERT INTO email_drafts (id, thread_id, recipients, cc, subject, body, "
                    "status, action_id, created_by) VALUES (:i, :t, :r, :c, :s, :b, 'draft', :a, :u)"
                ),
                {
                    "i": draft_id,
                    "t": thread_id,
                    "r": list(recipients),
                    "c": list(cc or []),
                    "s": subject,
                    "b": body,
                    "a": action_id,
                    "u": self.ctx.user_id,
                },
            )
        return ConnectorResult(
            ok=True,
            data={"draft_id": draft_id, "recipients": recipients, "subject": subject},
            summary=f"draft {draft_id} prepared for {', '.join(recipients)} — not sent",
        )

    def send_message(
        self,
        *,
        recipients: list[str],
        subject: str,
        body: str,
        cc: Optional[list[str]] = None,
        thread_id: Optional[str] = None,
        draft_id: Optional[str] = None,
    ) -> ConnectorResult:
        """Actually send. Writes an outbound message row and closes the draft."""
        message_id = new_id("msg")
        thread = thread_id or new_id("thr")
        sender = self.ctx.email or self.ctx.user_id
        with workspace_connection(self.ctx) as conn:
            conn.execute(
                text(
                    "INSERT INTO email_messages (id, thread_id, direction, sender, recipients, cc, "
                    "subject, body, labels) VALUES (:i, :t, 'outbound', :s, :r, :c, :su, :b, "
                    "ARRAY['SENT'])"
                ),
                {
                    "i": message_id,
                    "t": thread,
                    "s": sender,
                    "r": list(recipients),
                    "c": list(cc or []),
                    "su": subject,
                    "b": body,
                },
            )
            if draft_id:
                conn.execute(
                    text("UPDATE email_drafts SET status = 'sent', sent_at = NOW() WHERE id = :i"),
                    {"i": draft_id},
                )
        return ConnectorResult(
            ok=True,
            data={"message_id": message_id, "thread_id": thread, "recipients": recipients},
            summary=f"email sent to {', '.join(recipients)}",
        )


class GmailConnector(Connector):
    """Live Gmail adapter over agno's ``GmailTools``.

    Wired but not exercised in this deployment: no Google OAuth credentials are
    configured, so it refuses with a clear message rather than failing obscurely
    at call time. Reported as NOT TESTED in the final report.
    """

    category = "email"
    provider = "gmail"

    def _tools(self) -> Any:
        credentials = self.config.get("credentials_path") or self.config.get("token")
        if not credentials:
            raise ConnectorNotConfigured(
                "Gmail is not configured for this workspace. Connect it under Integrations "
                "(needs GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and a refresh token)."
            )
        from agno.tools.gmail import GmailTools  # imported lazily: optional dependency

        return GmailTools()

    def health(self) -> ConnectorResult:
        try:
            self._tools()
        except ConnectorNotConfigured as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, summary="gmail configured")

    def search_threads(self, query: str = "", *, limit: int = 20) -> ConnectorResult:
        tools = self._tools()
        raw = tools.search_emails(query=query, count=limit)
        threads, findings = wrap_many(
            raw if isinstance(raw, list) else [{"body": str(raw)}], source="gmail", text_field="body"
        )
        return ConnectorResult(ok=True, data=threads, summary=f"{len(threads)} thread(s)", untrusted_findings=findings)

    def read_thread(self, thread_id: str) -> ConnectorResult:
        tools = self._tools()
        raw = tools.get_thread(thread_id=thread_id)
        messages, findings = wrap_many(
            raw if isinstance(raw, list) else [{"body": str(raw)}], source="gmail", text_field="body"
        )
        return ConnectorResult(
            ok=True, data={"thread_id": thread_id, "messages": messages}, untrusted_findings=findings
        )

    def create_draft(self, *, recipients: list[str], subject: str, body: str, **kwargs: Any) -> ConnectorResult:
        tools = self._tools()
        result = tools.create_draft_email(to=",".join(recipients), subject=subject, body=body)
        return ConnectorResult(ok=True, data={"gmail": str(result)}, summary="gmail draft created")

    def send_message(self, *, recipients: list[str], subject: str, body: str, **kwargs: Any) -> ConnectorResult:
        tools = self._tools()
        result = tools.send_email(to=",".join(recipients), subject=subject, body=body)
        return ConnectorResult(ok=True, data={"gmail": str(result)}, summary="gmail message sent")
