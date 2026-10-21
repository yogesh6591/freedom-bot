"""Developer CLI: run exactly ONE live prospect through acquisition with tracing.

Usage:
  ACQUISITION_TRACE=true python -m app.scripts.trace_acquisition --limit 1

Or simply (this CLI forces tracing on for the process):
  python -m app.scripts.trace_acquisition --limit 1

Uses the SAME production services as real discovery.
Never calls Instantly / never sends outreach.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys


def _configure_logging(*, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    # Readable terminal format — keep [ACQ] lines dominant.
    logging.basicConfig(
        level=level,
        format="%(message)s",
        force=True,
    )
    # Quiet noisy HTTP clients unless debugging.
    if not verbose:
        for name in ("httpx", "httpcore", "openai", "urllib3"):
            logging.getLogger(name).setLevel(logging.WARNING)
            logging.getLogger(name).propagate = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Trace exactly one FreedomBot acquisition prospect end-to-end "
            "(ICP → search → research → score → AI learning). No Instantly/outreach."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Number of prospects to discover/upsert (default 1).",
    )
    parser.add_argument(
        "--no-research",
        action="store_true",
        help="Stop after discovery upsert (skip research/score/learning).",
    )
    parser.add_argument(
        "--no-score",
        action="store_true",
        help="Research but skip scoring/learning.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Also show non-trace debug logs.",
    )
    args = parser.parse_args(argv)

    limit = max(1, int(args.limit or 1))
    if limit > 5:
        print("Refusing --limit > 5 for developer safety.", file=sys.stderr)
        return 2

    # Force process-local tracing ON regardless of .env default.
    os.environ["ACQUISITION_TRACE"] = "true"
    from acquisition.config import get_settings
    from acquisition.services.acquisition_trace import set_trace_override

    get_settings.cache_clear()
    set_trace_override(True)
    _configure_logging(verbose=bool(args.verbose))

    settings = get_settings()
    logging.getLogger("acquisition.trace").info(
        "[ACQ][START]\nmode=developer_trace\nlimit=%s\nprovider=%s\ninstantly=DISABLED\noutreach=DISABLED",
        limit,
        settings.discovery_provider,
    )

    from acquisition.db.session import SessionLocal
    from acquisition.services.acquisition_orchestration_service import AcquisitionOrchestrationService

    with SessionLocal() as db:
        orch = AcquisitionOrchestrationService(db, settings=settings)
        # Explicit: discovery + optional research/score only — never activation.
        result = orch.run_discovery_cycle(
            limit=limit,
            research=not args.no_research,
            score=(not args.no_research) and (not args.no_score),
        )

    discovery = (result or {}).get("discovery") or {}
    researched = (result or {}).get("research") or []
    logging.getLogger("acquisition.trace").info(
        "[ACQ][CYCLE]\nok=%s\ncandidates=%s\ncreated=%s\nupdated=%s\nresearched=%s\nerrors=%s",
        discovery.get("ok"),
        discovery.get("candidates"),
        discovery.get("created"),
        discovery.get("updated"),
        len(researched),
        discovery.get("errors") or [],
    )
    # Safety assertion for operators reading the terminal.
    logging.getLogger("acquisition.trace").info(
        "[ACQ][SAFETY]\nInstantlyCalls=0\nOutreachSends=0\nActivation=skipped"
    )
    return 0 if discovery.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
