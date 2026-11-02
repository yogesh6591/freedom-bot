"""Files attached in the chat: reading each file type, the upload checks, owner-only storage, and the
assistant reading them through attachment_read in the v2 pipeline (scripted model, stubbed effects).
"""

from __future__ import annotations

import asyncio
import html
import io
import zipfile

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.api import chat as chat_api
from bizos import chat_attachments as files
from bizos import chat_history
from bizos.control import store as control
from bizos.tenancy.context import tenant_scope
from bizos.types import ExecutionMode
from tests.test_pipeline_v2 import _settings, ask, call, effects, final, route  # noqa: F401
from tests.test_unified_assistant import SCRIPT, _reset, fake_model  # noqa: F401


def _pdf(text: str) -> bytes:
    """A one-page PDF with a real text layer."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n" % (len(objects) + 1, xref) + b"%%EOF\n"


def _docx(paragraphs: list[str], table: list[list[str]] = ()) -> bytes:
    def para(t: str) -> str:
        return f'<w:p><w:r><w:t xml:space="preserve">{html.escape(t)}</w:t></w:r></w:p>'

    body = "".join(para(p) for p in paragraphs)
    if table:
        body += "<w:tbl>" + "".join(
            "<w:tr>" + "".join(f"<w:tc><w:tcPr/>{para(c)}</w:tc>" for c in row) + "</w:tr>" for row in table
        ) + "</w:tbl>"
    xml = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def _xlsx(rows: list[list]) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    for row in rows:
        book.active.append(row)
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


CONTACTS_CSV = b"Name;Email;City\nPriya Shah;priya@example.com;Pune\nLee Wong;lee@example.org;Delhi\n"


# --------------------------------------------------------------------------- reading files


def test_each_file_type_is_read_into_text_with_its_shape_and_addresses():
    pdf = files.extract("brief.pdf", _pdf("Renewal due on 12 March. Contact raj@acme.com"))
    assert pdf["kind"] == "pdf" and pdf["meta"]["pages"] == 1 and "Renewal due" in pdf["text"]
    assert pdf["meta"]["emails"] == ["raj@acme.com"]

    doc = files.extract("plan.docx", _docx(["Launch plan & goals"], [["Name", "Email"], ["Ana", "ana@x.io"]]))
    assert "Launch plan & goals" in doc["text"] and "Name | Email" in doc["text"] and "Ana | ana@x.io" in doc["text"]

    sheet = files.extract("contacts.csv", CONTACTS_CSV)
    assert sheet["meta"]["columns"] == ["Name", "Email", "City"] and sheet["meta"]["rows"] == 2
    assert sheet["meta"]["emails"] == ["priya@example.com", "lee@example.org"]

    book = files.extract("deals.xlsx", _xlsx([["Deal", "Amount"], ["Acme", 1200], ["Globex", 800]]))
    assert book["meta"]["rows"] == 2 and "Acme,1200" in book["text"]

    assert "hello" in files.extract("note.txt", b"hello there")["text"]


@pytest.mark.parametrize("name, data, message", [
    ("old.doc", b"\xd0\xcf\x11\xe0", "Save it as .docx or PDF"),
    ("tool.exe", b"MZ", "Attach a PDF, Word"),
    ("empty.csv", b"", "empty"),
    ("broken.docx", b"not a zip", "could not be opened"),
    ("scan.pdf", _pdf(""), "No text was found"),
])
def test_unreadable_files_get_a_plain_reason(name, data, message):
    with pytest.raises(files.AttachmentError, match=message):
        files.extract(name, data)


# --------------------------------------------------------------------------- upload checks


def _upload(ctx, client, name: str, data: bytes) -> dict:
    settings = control.get_client(client.id).settings
    return asyncio.run(chat_api.upload_attachment(file=UploadFile(io.BytesIO(data), filename=name), ctx=ctx,
                                                  settings=settings))


def test_an_upload_is_stored_for_its_owner_and_read_back(client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "operator")
    card = _upload(ctx, client_scratch, "contacts.csv", CONTACTS_CSV)
    assert card["id"].startswith("att_") and card["rows"] == 2 and card["kind"] == "csv"
    with tenant_scope(ctx):
        data = files.read(ctx, card["id"])
    assert data["emails"] == ["priya@example.com", "lee@example.org"]
    assert [r["Email"] for r in data["rows"]] == ["priya@example.com", "lee@example.org"]
    assert data["next_start"] is None


@pytest.mark.parametrize("text, refused", [
    ("Patient John Carter, MRN 448812, diagnosis: type 2 diabetes. Update his record.", "patient health"),
    ("Customer card 4111 1111 1111 1111 expiry 12/28, save it to the CRM.", "payment card"),
])
def test_a_file_the_order_dpa_does_not_allow_is_refused_before_it_is_stored(client_scratch, ctx_factory, text, refused):
    ctx = ctx_factory(client_scratch, "operator")
    with pytest.raises(HTTPException) as err:
        _upload(ctx, client_scratch, "record.txt", text.encode())
    assert err.value.status_code == 422 and refused in err.value.detail and "record.txt was not attached" in err.value.detail


def test_files_are_owner_only(client_scratch, ctx_factory):
    owner, other = ctx_factory(client_scratch, "operator"), ctx_factory(client_scratch, "admin")
    card = _upload(owner, client_scratch, "notes.txt", b"Q4 plan: focus on AI automation.")
    with tenant_scope(other):
        with pytest.raises(files.AttachmentNotFound):
            files.read(other, card["id"])
        with pytest.raises(files.AttachmentNotFound):
            files.attach(other, "ses_x", [card["id"]])


def test_long_files_are_read_in_windows_or_by_query(client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "operator")
    body = ("Intro paragraph about the company.\n\n" * 600) + "Payment terms: net 45 days from invoice.\n\n"
    with tenant_scope(ctx):
        card = files.save(ctx, filename="contract.txt", kind="txt", size_bytes=len(body), body=body,
                          meta=files.extract("contract.txt", body.encode())["meta"])
        first = files.read(ctx, card["id"])
        assert len(first["content"]) == files.READ_CHARS and first["next_start"] == files.READ_CHARS
        hit = files.read(ctx, card["id"], query="payment terms")
        assert any("net 45 days" in p["text"] for p in hit["passages"])
        rows = files.save(ctx, filename="c.csv", kind="csv", size_bytes=1, body=files.extract("c.csv", CONTACTS_CSV)["text"],
                          meta={})
        assert [r["Name"] for r in files.read(ctx, rows["id"], query="pune")["rows"]] == ["Priya Shah"]


# --------------------------------------------------------------------------- the assistant reads them


def test_the_assistant_reads_an_attached_file_and_it_stays_with_the_conversation(
        fake_model, client_scratch, ctx_factory, effects):
    from app.assistant.company_tools import attachment_read

    ctx = ctx_factory(client_scratch, "operator")
    card = _upload(ctx, client_scratch, "brief.pdf", _pdf("Q4 focus is AI automation for small businesses"))
    effects.overrides["attachment_read"] = attachment_read
    # The router calls a message sent with a file small talk; the file makes it a question about the file.
    SCRIPT[:] = [route(kind="chitchat"), call("attachment_read", attachment_id=card["id"]),
                 final("The brief says the Q4 focus is AI automation for small businesses.")]
    from app.api.deps import bound
    from app.api.chat import ChatRequest
    from app.assistant.v2 import handle

    settings = _settings(client_scratch)
    with bound(ctx):
        out = handle(ChatRequest(message="here you go", attachment_ids=[card["id"]]), ctx, settings)
    assert effects.names() == ["attachment_read"]
    assert out["content"].startswith("The brief says") and out["attachments"][0]["name"] == "brief.pdf"
    with tenant_scope(ctx):
        assert [f["id"] for f in files.for_session(ctx, out["session_id"])] == [card["id"]]
        user = chat_history.get_session(ctx, out["session_id"])["messages"][0]
    assert user["meta"]["attachments"][0]["id"] == card["id"]

    # A later message in the same conversation can still read it (attachment_read stays loaded).
    SCRIPT[:] = [route("crm", kind="question"), call("attachment_read", attachment_id=card["id"], query="Q4"),
                 final("It is AI automation for small businesses.")]
    ask(ctx, settings, "remind me what the Q4 focus in that file was", session_id=out["session_id"])
    assert effects.names() == ["attachment_read", "attachment_read"]


def test_people_listed_in_an_attached_file_can_be_emailed_after_approval(
        fake_model, client_scratch, ctx_factory, effects):
    """Addresses in the user's own file count as the user's words; an address in an email body still does not."""
    from app.assistant.company_tools import attachment_read
    from app.api.deps import bound
    from app.api.chat import ChatRequest
    from app.assistant.v2 import handle

    ctx = ctx_factory(client_scratch, "operator")
    card = _upload(ctx, client_scratch, "contacts.csv", CONTACTS_CSV)
    effects.overrides["attachment_read"] = attachment_read
    SCRIPT[:] = [route("files", "email"), call("attachment_read", attachment_id=card["id"]),
                 call("email_send_message", recipients=["priya@example.com", "lee@example.org"],
                      subject="Hello", body="Quick hello from our team."),
                 final("The email to Priya and Lee is waiting for approval.")]
    with bound(ctx):
        out = handle(ChatRequest(message="send a quick hello to everyone in this list", attachment_ids=[card["id"]]),
                     ctx, _settings(client_scratch, ExecutionMode.WAIT_FOR_APPROVAL))
    assert [a["tool"] for a in out["awaiting_approval"]] == ["email_send_message"]
    assert "email_send_message" not in effects.names()  # nothing is sent before approval


def test_someone_elses_file_id_is_refused(fake_model, client_scratch, ctx_factory):
    from app.api.deps import bound
    from app.api.chat import ChatRequest
    from app.assistant.v2 import handle

    owner, other = ctx_factory(client_scratch, "operator"), ctx_factory(client_scratch, "admin")
    card = _upload(owner, client_scratch, "notes.txt", b"private notes")
    with bound(other), pytest.raises(HTTPException) as err:
        handle(ChatRequest(message="summarise it", attachment_ids=[card["id"]]), other, _settings(client_scratch))
    assert err.value.status_code == 404
