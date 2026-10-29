"""Cal.com status for the Integrations page. The connection itself lives in Composio (no key is stored here)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

NOT_CONNECTED = "Cal.com is not connected. Connect it on the Integrations page of the chat app."


@dataclass(frozen=True, slots=True)
class CalcomSummary:
    configured: bool
    account: str  # who connected it (display name or email), "" when nobody did


@dataclass(frozen=True, slots=True)
class CalcomConnectionResult:
    connected: bool
    message: str
    category: str  # ok | config_error | error
    http_status: int = 200


class CalcomService:
    def __init__(self, db: Session | None = None) -> None:
        self.db = db

    def get_summary(self) -> CalcomSummary:
        from acquisition.integrations.calcom import owner

        user_id = owner.owner_user_id()
        if not user_id:
            return CalcomSummary(configured=False, account="")
        account = ""
        try:
            from bizos.control import store as control

            user = control.get_user(user_id)
            account = str(getattr(user, "display_name", "") or getattr(user, "email", "") or "")
        except Exception:  # noqa: BLE001 - the label is cosmetic
            pass
        return CalcomSummary(configured=True, account=account)

    def check_connection(self) -> CalcomConnectionResult:
        """Ask Cal.com who the connected account is."""
        from acquisition.integrations.calcom.client import CalcomClient, CalcomError

        client = CalcomClient(db=self.db)
        if not client.configured:
            return CalcomConnectionResult(connected=False, message=NOT_CONNECTED, category="config_error")
        try:
            client.get_me()
        except CalcomError as exc:
            return CalcomConnectionResult(
                connected=False, message=exc.message, category="error", http_status=exc.http_status
            )
        return CalcomConnectionResult(connected=True, message="", category="ok")
