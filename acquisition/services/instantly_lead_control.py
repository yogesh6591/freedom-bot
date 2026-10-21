"""Stop a single Instantly lead using documented interest-status update.

Instantly does not expose a dedicated "pause this lead only" endpoint.
Official approach: POST /leads/update-interest-status with interest_value=-1
(Not Interested), which stops further outreach while preserving lead history.
We do NOT delete leads to stop messaging.
"""

from __future__ import annotations

from acquisition.integrations.instantly.client import InstantlyClient
from acquisition.integrations.instantly.errors import InstantlyError
from acquisition.models.campaign import Campaign, CampaignProspect

# Official static interest value: Not Interested
INSTANTLY_NOT_INTERESTED = -1


def stop_instantly_lead_outreach(
    client: InstantlyClient,
    *,
    campaign: Campaign,
    link: CampaignProspect,
    interest_value: int = INSTANTLY_NOT_INTERESTED,
) -> None:
    email = link.prospect.email if link.prospect else None
    if not email or not campaign.instantly_campaign_id:
        return
    try:
        client.update_lead_interest_status(
            {
                "lead_email": email,
                "interest_value": interest_value,
                "campaign_id": campaign.instantly_campaign_id,
            }
        )
    except InstantlyError:
        # Caller records local stop even if provider call fails; re-raise optional.
        raise
