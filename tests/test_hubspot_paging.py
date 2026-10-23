"""HubSpot deal counts read every page, not only HubSpot's first 100 results.

Mocked Composio calls only: no request reaches HubSpot.
"""

from __future__ import annotations

from typing import Any

from bizos.connectors.base import ConnectorError
from bizos.connectors.composio import ComposioHubSpotConnector

STAGES = ["appointmentscheduled"] * 22 + ["qualifiedtobuy"] * 31 + ["presentationscheduled"] * 31 + \
    ["contractsent"] * 56


def _connector(deals: list[dict[str, Any]], *, fail_after_first: bool = False) -> tuple[Any, list[dict]]:
    calls: list[dict] = []
    hs = ComposioHubSpotConnector.__new__(ComposioHubSpotConnector)

    def run(action: str, args: dict[str, Any]) -> Any:
        calls.append(dict(args))
        if fail_after_first and "after" in args:
            raise ConnectorError("page 2 unavailable")
        wanted = [d for d in deals if not args.get("filterGroups")
                  or d["properties"]["dealstage"] == args["filterGroups"][0]["filters"][0]["value"]]
        start = int(args.get("after") or 0)
        page = wanted[start:start + args["limit"]]
        nxt = start + len(page)
        body: dict[str, Any] = {"total": len(wanted), "results": page}
        if nxt < len(wanted):
            body["paging"] = {"next": {"after": str(nxt)}}
        return {"response_data": body}

    hs._run = run
    return hs, calls


def _deals() -> list[dict[str, Any]]:
    return [{"id": str(i), "properties": {"dealname": f"Deal {i}", "dealstage": s, "amount": "0"}}
            for i, s in enumerate(STAGES)]


def test_pipeline_summary_counts_all_140_deals():
    hs, calls = _connector(_deals())
    out = hs.pipeline_summary()
    assert out.ok
    by_stage = {s["stage"]: s["deals"] for s in out.data["stages"]}
    assert by_stage["appointmentscheduled"] == 22
    assert out.data["deals_counted"] == 140 == out.data["deals_in_hubspot"]
    assert len(calls) == 2 and calls[1]["after"] == "100"
    assert "partial" not in out.summary


def test_a_failed_second_page_says_the_count_is_partial():
    hs, _ = _connector(_deals(), fail_after_first=True)
    out = hs.pipeline_summary()
    assert out.ok
    assert out.data["deals_counted"] == 100 and out.data["deals_in_hubspot"] == 140
    assert "partial" in out.summary


def test_search_by_hubspot_stage_label_and_total():
    hs, calls = _connector(_deals())
    out = hs.search_deals(stage="Appointment Scheduled", limit=10)
    assert calls[0]["filterGroups"][0]["filters"][0]["value"] == "appointmentscheduled"
    assert len(out.data) == 10
    assert out.metadata["total"] == 22 and "of 22" in out.summary
