"""Uploaded knowledge-base documents: admin-only upload, and each user sees only what their access allows."""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

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


def _pdf(text_line: str) -> bytes:
    """The smallest real PDF with one line of text."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 150 Td ({text_line}) Tj ET".encode())
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _docx(text_line: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", f"<w:document><w:p><w:r><w:t>{text_line}</w:t></w:r></w:p></w:document>")
    return out.getvalue()


def _token(api, client, prefix: str) -> dict[str, str]:
    response = api.post("/api/auth/login", json={"email": f"{prefix}@{client.slug}.example.com",
                                                 "password": "bizos-dev-password", "client_slug": client.slug})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(scope="module")
def api():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _upload(api, headers, name: str, data: bytes, area: str = ""):
    return api.post("/api/memory/documents", files={"file": (name, data)}, data={"access_area": area}, headers=headers)


def test_the_original_pdf_can_be_opened_by_anyone_who_can_see_it(api, client_scratch):
    admin, viewer = _token(api, client_scratch, "admin"), _token(api, client_scratch, "viewer")
    data = _pdf("Support opens at nine")
    doc = _upload(api, admin, "support.pdf", data).json()

    opened = api.get(f"/api/memory/documents/{doc['document_id']}/file", headers=viewer)
    assert opened.status_code == 200 and opened.content == data
    assert opened.headers["content-type"] == "application/pdf"
    assert opened.headers["content-disposition"].startswith("inline")
    assert opened.headers["x-content-type-options"] == "nosniff"
    assert "Support opens at nine" in api.get(f"/api/memory/documents/{doc['document_id']}/text", headers=viewer).json()["text"]
    listed = {d["document_id"]: d for d in api.get("/api/memory/documents", headers=viewer).json()["items"]}
    assert listed[doc["document_id"]]["has_file"] is True


def test_a_restricted_file_is_not_found_without_that_access(api, client_scratch):
    admin, viewer = _token(api, client_scratch, "admin"), _token(api, client_scratch, "viewer")
    doc = _upload(api, admin, "bonus.pdf", _pdf("Bonus pool is secret"), area="finance").json()
    assert api.get(f"/api/memory/documents/{doc['document_id']}/file", headers=admin).status_code == 200
    assert api.get(f"/api/memory/documents/{doc['document_id']}/file", headers=viewer).status_code == 404
    assert api.get(f"/api/memory/documents/{doc['document_id']}/text", headers=viewer).status_code == 404


def test_another_company_cannot_open_the_file(api, client_scratch, client_b):
    doc = _upload(api, _token(api, client_scratch, "admin"), "own.pdf", _pdf("Ours only")).json()
    outsider = _token(api, client_b, "admin")
    assert api.get(f"/api/memory/documents/{doc['document_id']}/file", headers=outsider).status_code == 404
    assert api.get(f"/api/memory/documents/{doc['document_id']}/text", headers=outsider).status_code == 404


def test_removing_a_document_removes_its_file(api, client_scratch):
    admin = _token(api, client_scratch, "admin")
    doc = _upload(api, admin, "old.pdf", _pdf("Soon removed")).json()
    assert api.post(f"/api/memory/documents/{doc['document_id']}/archive", headers=admin).status_code == 200
    assert api.get(f"/api/memory/documents/{doc['document_id']}/file", headers=admin).status_code == 404


def test_a_word_file_is_searchable_and_downloads_instead_of_opening(api, client_scratch):
    admin = _token(api, client_scratch, "admin")
    data = _docx("The Word handbook says lunch is at noon.")
    doc = _upload(api, admin, "handbook.docx", data).json()
    assert doc["parts"] == 1
    body = api.get(f"/api/memory/documents/{doc['document_id']}/text", headers=admin).json()
    assert "lunch is at noon" in body["text"]
    opened = api.get(f"/api/memory/documents/{doc['document_id']}/file", headers=admin)
    assert opened.content == data and opened.headers["content-disposition"].startswith("attachment")


def test_an_old_word_file_gets_a_clear_message(api, client_scratch):
    response = _upload(api, _token(api, client_scratch, "admin"), "old.doc", b"binary")
    assert response.status_code == 400 and ".docx" in response.json()["detail"]


def test_a_memory_form_cannot_store_blocked_data(api, client_scratch):
    admin = _token(api, client_scratch, "admin")
    for title, content in (
        ("Customer card", "Card number 4111 1111 1111 1111 with CVV 123."),
        ("Patient note", "Patient Ravi Kumar, diagnosis: type 2 diabetes."),
        ("New contact", "Aarav Shah, 9 years old, Greenfield Elementary."),
        ("Door access", "Keep Ravi's fingerprint template for the warehouse door."),
    ):
        response = api.post("/api/memory", json={"category": "FACT", "title": title, "content": content}, headers=admin)
        assert response.status_code == 422, title


def test_a_correction_cannot_add_blocked_data(api, client_scratch):
    admin = _token(api, client_scratch, "admin")
    item = api.post("/api/memory", json={"category": "FACT", "title": "Billing note", "content": "Bill monthly."}, headers=admin).json()
    response = api.post(f"/api/memory/{item['id']}/correct",
                        json={"new_content": "Card number 4111 1111 1111 1111.", "reason": "Test"}, headers=admin)
    assert response.status_code == 422


@pytest.mark.parametrize("category", ["FACT", "SOP", "DECISION"])
def test_an_item_added_from_the_page_shows_in_its_tab(api, client_scratch, category):
    admin = _token(api, client_scratch, "admin")
    title = f"Page added {category.lower()}"
    made = api.post("/api/memory", json={"category": category, "title": title, "content": "Added from the page."}, headers=admin)
    assert made.status_code == 201
    assert title in [i["title"] for i in api.get(f"/api/memory?category={category}", headers=admin).json()["items"]]


def test_a_correction_added_from_the_page_shows_in_the_corrections_tab(api, client_scratch):
    admin = _token(api, client_scratch, "admin")
    item = api.post("/api/memory", json={"category": "FACT", "title": "Review cycle", "content": "Every year."}, headers=admin).json()
    done = api.post(f"/api/memory/{item['id']}/correct", json={"new_content": "Every half year.", "reason": "New policy"}, headers=admin)
    assert done.status_code == 200
    rows = {i["id"]: i for i in api.get("/api/memory?category=CORRECTION", headers=admin).json()["items"]}
    assert rows[item["id"]]["current"]["content"] == "Every half year."


def test_a_viewer_cannot_add_memory(api, client_scratch):
    response = api.post("/api/memory", json={"category": "FACT", "title": "No", "content": "No."},
                        headers=_token(api, client_scratch, "viewer"))
    assert response.status_code == 403
