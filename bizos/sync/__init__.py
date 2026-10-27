"""
Integration sync
================

Keeps a per-user copy of each person's latest Gmail, Google Calendar, YouTube and HubSpot activity
(connected through Composio), so the assistant can search what is new without a live call per question.

* :mod:`bizos.sync.store`   tables, upserts, checkpoints, owner-only search.
* :mod:`bizos.sync.sources` one fetcher per source, built on the existing Composio connectors.
* :mod:`bizos.sync.runner`  sync one user or everyone (one run at a time across processes).
* :mod:`bizos.sync.scheduler` the background loop started with the API.

Synced records belong to one user: only that user's searches see them, and disconnecting a source
deletes its records. Text arrives already neutralized by the connectors and stays untrusted data.
"""
