"""Uploaded knowledge-base documents: admin-only upload, and each user sees only what their access allows."""

from __future__ import annotations

from app.api.deps import bound
from bizos.control import store as control
from bizos.memory import documents
from bizos.memory import store as memory


def test_a_restricted_document_is_found_only_by_users_with_that_access(client_scratch, ctx_factory):
    admin, viewer = ctx_factory(client_scratch, "admin"), ctx_factory(client_scratch, "viewer")
    settings = control.get_client(client_scratch.id).settings
    with bound(admin):
        everyone = documents.upload(title="Leave policy", filename="leave.txt",
                                    data=b"Staff get 24 days of paid leave each year.", access_area=None,
                                    settings=settings, ctx=admin)
        finance = documents.upload(title="Bonus pool", filename="bonus.txt",
                                   data=b"The quarterly bonus pool is 4 percent of profit.", access_area="finance",
                                   settings=settings, ctx=admin)
    with bound(viewer):
        assert memory.search("paid leave", ctx=viewer)
        assert not memory.search("bonus pool", ctx=viewer)
        seen = {d["document_id"] for d in documents.list_documents(ctx=viewer)}
    assert everyone["document_id"] in seen and finance["document_id"] not in seen
    with bound(admin):
        assert memory.search("bonus pool", ctx=admin)
        assert documents.archive_document(finance["document_id"], reason="test", ctx=admin) == 1
        assert not memory.search("bonus pool", ctx=admin)


def test_only_an_admin_can_upload(client_scratch, ctx_factory):
    from fastapi import HTTPException

    from app.api.deps import require_admin

    for who in ("operator", "approver", "viewer"):
        try:
            require_admin(ctx_factory(client_scratch, who))
            raise AssertionError(f"{who} passed the admin check")
        except HTTPException as exc:
            assert exc.status_code == 403
    assert require_admin(ctx_factory(client_scratch, "admin"))
