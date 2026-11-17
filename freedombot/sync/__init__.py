"""
Integration sync
================

Keeps a per-user copy of each person's latest Gmail, Google Calendar, YouTube and HubSpot activity
(connected through Composio), so the assistant can search what is new without a live call per question.

* :mod:`freedombot.sync.store`   tables, upserts, checkpoints, owner-only search.
* :mod:`freedombot.sync.sources` one fetcher per source, built on the existing Composio connectors.
* :mod:`freedombot.sync.runner`  sync one user or everyone (one run at a time across processes).
* :mod:`freedombot.sync.scheduler` the background loop started with the API.

Synced records belong to one user: only that user's searches see them, and disconnecting a source
deletes its records. Text arrives already neutralized by the connectors and stays untrusted data.
"""
