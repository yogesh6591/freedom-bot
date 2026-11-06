"""
Chat Attachments
================

Files a user attaches in the chat (PDF, Word .docx, CSV, Excel .xlsx, plain text), so the assistant can
answer questions about them and use what is in them ("email the people in this list").

* **Text only.** The file is read once at upload; its text (or table) is stored, never the file itself.
  Stored text has regulated identifiers (SSN, card and record numbers) replaced, as in chat history.
* **Same rules as a typed message.** The upload route refuses a file whose text the Order/DPA does not
  allow (PHI, card data, children's data, biometrics) before anything is stored or sent to a model.
* **Per user.** A file belongs to the person who uploaded it and to the conversation it was sent in.
  Reads are filtered by ``owner_user_id``; the table carries the workspace's ``client_id`` wall.
* **Untrusted content.** File text is data, never instructions. The assistant reads it through the
  ``attachment_read`` tool, which goes through the guard and the policy check like every other tool.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import threading
import zipfile
from typing import Any, Optional

from sqlalchemy import bindparam, text

from bizos.tenancy.context import TenantContext
from bizos.tenancy.registry import workspace_connection, workspace_location
from bizos.util.ids import new_id
from bizos.util.redaction import scrub_identifiers

#: Accepted file types. ``.doc`` (the old binary Word format) gets its own message.
EXTENSIONS = (".pdf", ".docx", ".csv", ".xlsx", ".txt", ".md")
MAX_BYTES = 10 * 1024 * 1024
#: A video (demo or welcome video, admins only) is not read: it is saved on the server like an admin-panel upload
#: and the assistant is given its link.
VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".m4v")
#: Most text kept from one file, and most rows kept from one table.
MAX_CHARS = 400_000
MAX_ROWS = 20_000
#: Most files on one message, and most characters one ``attachment_read`` call returns.
MAX_PER_MESSAGE = 5
READ_CHARS = 12_000
#: Unpacked size limit for a .docx part or .xlsx workbook (a zip bomb is refused, not unpacked).
_MAX_UNZIPPED = 50 * 1024 * 1024
_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MAX_EMAILS = 500

_ready: set[tuple[str, str]] = set()
_lock = threading.Lock()


class AttachmentError(ValueError):
    """The file cannot be used (type, size, or no readable text)."""


class AttachmentNotFound(LookupError):
    """No attachment with that id belongs to the caller."""


# --------------------------------------------------------------------------- reading files


def kind_of(filename: str) -> str:
    name = (filename or "").lower()
    if name.endswith(".doc"):
        raise AttachmentError("Old Word .doc files can't be read. Save it as .docx or PDF and attach it again.")
    for ext in EXTENSIONS:
        if name.endswith(ext):
            return ext.lstrip(".")
    raise AttachmentError("Attach a PDF, Word (.docx), CSV, Excel (.xlsx) or text file.")


def _tidy(value: str) -> str:
    value = re.sub(r"[ \t]+", " ", value.replace("\r\n", "\n").replace("\r", "\n"))
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _zip_part(data: bytes, name: str) -> bytes:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        info = archive.getinfo(name)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise AttachmentError("The file could not be opened; it may be damaged or not a real .docx.") from exc
    if info.file_size > _MAX_UNZIPPED:
        raise AttachmentError("The document is too large to read.")
    return archive.read(info)


#: Word XML pieces in reading order: a run of text, a tab or line break, or the start or end of a table
#: cell, a table row or a paragraph.
_DOCX_TOKEN = re.compile(
    r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>|<w:tab/>|<w:br/>|<w:tc(?:\s[^>]*)?>|</w:tc>|</w:tr>|</w:p>")


def _docx_text(data: bytes) -> str:
    """Paragraphs, and table rows as "cell | cell", of word/document.xml.

    Read with a pattern, not an XML parser, so nothing in the file is expanded or fetched.
    """
    xml = _zip_part(data, "word/document.xml").decode("utf-8", errors="replace")
    out: list[str] = []
    row: list[str] = []
    cell: Optional[list[str]] = None
    for match in _DOCX_TOKEN.finditer(xml):
        token = match.group(0)
        if match.group(1) is not None:
            (cell if cell is not None else out).append(html.unescape(match.group(1)))
        elif token.startswith("<w:tc"):
            cell = []
        elif token == "</w:tc>":
            row.append(" ".join("".join(cell or []).split()))
            cell = None
        elif token == "</w:tr>":
            out.append(" | ".join(row) + "\n")
            row = []
        elif cell is not None:  # a paragraph end, tab or break inside a cell
            cell.append(" ")
        else:
            out.append(" " if token == "<w:tab/>" else "\n")
    return "".join(out)


def _csv_rows(data: bytes) -> list[list[str]]:
    raw = _decode(data)
    sample = raw[:20_000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for row in csv.reader(io.StringIO(raw), dialect):
        if any(cell.strip() for cell in row):
            rows.append([cell.strip() for cell in row])
        if len(rows) > MAX_ROWS:
            break
    return rows


def _xlsx_tables(data: bytes) -> list[tuple[str, list[list[str]]]]:
    from openpyxl import load_workbook

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        if sum(i.file_size for i in archive.infolist()) > _MAX_UNZIPPED:
            raise AttachmentError("The workbook is too large to read.")
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except AttachmentError:
        raise
    except Exception as exc:  # noqa: BLE001 - any parse failure is the file's problem
        raise AttachmentError("The Excel file could not be opened; it may be damaged.") from exc
    tables, total = [], 0
    for sheet in book.worksheets:
        rows = []
        for values in sheet.iter_rows(values_only=True):
            row = ["" if v is None else str(v).strip() for v in values]
            if any(row):
                rows.append(row)
            total += 1
            if total > MAX_ROWS:
                break
        tables.append((sheet.title, rows))
        if total > MAX_ROWS:
            break
    book.close()
    return tables


def _as_csv(rows: list[list[str]]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


def extract(filename: str, data: bytes) -> dict[str, Any]:
    """The file's text and what the assistant needs to know about it: kind, size, columns, rows, emails."""
    kind = kind_of(filename)
    if len(data) > MAX_BYTES:
        raise AttachmentError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    if not data:
        raise AttachmentError("The file is empty.")
    meta: dict[str, Any] = {}
    if kind in ("pdf", "txt", "md"):
        from bizos.memory.documents import DocumentError, extract_text

        try:
            body = extract_text(filename, data)
        except DocumentError as exc:
            raise AttachmentError(str(exc)) from exc
        if kind == "pdf":
            from pypdf import PdfReader

            meta["pages"] = len(PdfReader(io.BytesIO(data)).pages)
    elif kind == "docx":
        body = _tidy(_docx_text(data))
    elif kind == "csv":
        rows = _csv_rows(data)
        meta.update(columns=rows[0] if rows else [], rows=max(0, len(rows) - 1), truncated=len(rows) > MAX_ROWS)
        body = _as_csv(rows[: MAX_ROWS + 1])
    else:  # xlsx
        tables = _xlsx_tables(data)
        meta["sheets"] = [{"name": name, "columns": rows[0] if rows else [], "rows": max(0, len(rows) - 1)}
                          for name, rows in tables]
        if len(tables) == 1:
            meta.update(columns=meta["sheets"][0]["columns"], rows=meta["sheets"][0]["rows"])
        body = _as_csv(tables[0][1]) if len(tables) == 1 else "\n".join(
            f"## Sheet: {name}\n{_as_csv(rows)}" for name, rows in tables)
    if not body.strip():
        raise AttachmentError("No text was found in the file (a scanned PDF has no text layer).")
    if len(body) > MAX_CHARS:
        body, meta["truncated"] = body[:MAX_CHARS], True
    found = list(dict.fromkeys(e.strip(".'") for e in _EMAIL.findall(body)))
    meta.update(characters=len(body), emails=found[:MAX_EMAILS], emails_total=len(found))
    return {"kind": kind, "text": body, "meta": meta}


# --------------------------------------------------------------------------- storage


def _ensure_table(ctx: TenantContext) -> None:
    from bizos.tenancy.schema import client_wall_ddl

    key = workspace_location(ctx)
    if key in _ready:
        return
    with _lock:
        if key in _ready:
            return
        with workspace_connection(ctx) as conn:
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS chat_attachments (
                    id            TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    session_id    TEXT,
                    filename      TEXT NOT NULL,
                    kind          TEXT NOT NULL,
                    size_bytes    INTEGER NOT NULL,
                    content       TEXT NOT NULL,
                    meta          JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS chat_attachments_owner_session "
                "ON chat_attachments (owner_user_id, session_id)"
            ))
            for statement in client_wall_ddl(ctx.client_id, ("chat_attachments",)):
                conn.execute(text(statement))
        _ready.add(key)


def _card(row: Any) -> dict[str, Any]:
    """What the UI and the assistant's file list show (no file text)."""
    meta = row.meta if isinstance(row.meta, dict) else json.loads(row.meta or "{}")
    card = {"id": row.id, "name": row.filename, "kind": row.kind, "size_bytes": row.size_bytes}
    for key in ("pages", "rows", "columns", "sheets", "characters", "emails_total", "truncated"):
        if key in meta:
            card[key] = meta[key]
    return card


def save(ctx: TenantContext, *, filename: str, kind: str, size_bytes: int, body: str, meta: dict[str, Any],
         attachment_id: Optional[str] = None) -> dict[str, Any]:
    """Store an extracted file for its uploader; it joins a conversation when a message is sent with it."""
    _ensure_table(ctx)
    attachment_id = attachment_id or new_id("att")
    with workspace_connection(ctx) as conn:
        # Files uploaded but never sent are kept for a day.
        conn.execute(text("DELETE FROM chat_attachments WHERE owner_user_id = :o AND session_id IS NULL "
                          "AND created_at < NOW() - INTERVAL '1 day'"), {"o": ctx.user_id})
        conn.execute(
            text("INSERT INTO chat_attachments (id, owner_user_id, filename, kind, size_bytes, content, meta) "
                 "VALUES (:i, :o, :f, :k, :s, :c, CAST(:m AS JSONB))"),
            {"i": attachment_id, "o": ctx.user_id, "f": filename[:200], "k": kind, "s": int(size_bytes),
             "c": scrub_identifiers(body), "m": json.dumps(meta, default=str)},
        )
    return {"id": attachment_id, "name": filename[:200], "kind": kind, "size_bytes": int(size_bytes),
            **{k: meta[k] for k in ("pages", "rows", "columns", "sheets", "characters", "emails_total", "truncated")
               if k in meta}}


def save_video(ctx: TenantContext, *, filename: str, size_bytes: int, url: str) -> dict[str, Any]:
    """Record a video that was saved on the server; its text is the link the assistant needs."""
    body = (f"Video file {filename[:200]} was saved on the server. Its link is {url} . To use it as the demo video "
            f"set demo_video_url to exactly this link; to use it as the welcome video shown after booking set "
            f"after_booking_video_url to exactly this link.")
    return save(ctx, filename=filename, kind="video", size_bytes=size_bytes, body=body, meta={"video_url": url})


def attach(ctx: TenantContext, session_id: str, ids: list[str], *, bind: bool = True) -> list[dict[str, Any]]:
    """Join the caller's newly sent files to ``session_id``. Returns their cards in the order given.

    A file that is not the caller's, or already belongs to another conversation, is refused. With
    ``bind=False`` the files are only checked (the turn binds them once it is saved, so a failed turn
    leaves them free to send again).
    """
    wanted = list(dict.fromkeys(str(i) for i in ids if i))
    if not wanted:
        return []
    if len(wanted) > MAX_PER_MESSAGE:
        raise AttachmentError(f"Attach at most {MAX_PER_MESSAGE} files to one message.")
    _ensure_table(ctx)
    with workspace_connection(ctx) as conn:
        rows = conn.execute(
            text("SELECT id, filename, kind, size_bytes, meta, session_id FROM chat_attachments "
                 "WHERE owner_user_id = :o AND id IN :ids").bindparams(bindparam("ids", expanding=True)),
            {"o": ctx.user_id, "ids": wanted},
        ).fetchall()
        found = {r.id: r for r in rows if r.session_id in (None, session_id)}
        missing = [i for i in wanted if i not in found]
        if missing:
            raise AttachmentNotFound(missing[0])
        if not bind:
            return [_card(found[i]) for i in wanted]
        conn.execute(
            text("UPDATE chat_attachments SET session_id = :s WHERE owner_user_id = :o AND id IN :ids "
                 "AND session_id IS NULL").bindparams(bindparam("ids", expanding=True)),
            {"s": session_id, "o": ctx.user_id, "ids": wanted},
        )
    return [_card(found[i]) for i in wanted]


def for_session(ctx: TenantContext, session_id: str) -> list[dict[str, Any]]:
    """The caller's files in one conversation, oldest first."""
    _ensure_table(ctx)
    with workspace_connection(ctx, readonly=True) as conn:
        rows = conn.execute(
            text("SELECT id, filename, kind, size_bytes, meta FROM chat_attachments "
                 "WHERE owner_user_id = :o AND session_id = :s ORDER BY created_at, id"),
            {"o": ctx.user_id, "s": session_id},
        ).fetchall()
    return [_card(r) for r in rows]


def delete_session(ctx: TenantContext, session_id: Optional[str] = None) -> int:
    """Remove the caller's files of one conversation, or of all of them when ``session_id`` is None."""
    _ensure_table(ctx)
    with workspace_connection(ctx) as conn:
        if session_id is None:
            result = conn.execute(text("DELETE FROM chat_attachments WHERE owner_user_id = :o"), {"o": ctx.user_id})
        else:
            result = conn.execute(text("DELETE FROM chat_attachments WHERE owner_user_id = :o AND session_id = :s"),
                                  {"o": ctx.user_id, "s": session_id})
    return int(result.rowcount or 0)


# --------------------------------------------------------------------------- the assistant's read


def _words(value: str) -> list[str]:
    return [w for w in re.findall(r"[\w@.'-]+", value.casefold()) if len(w) > 2]


def read(ctx: TenantContext, attachment_id: str, *, query: str = "", start: int = 0) -> dict[str, Any]:
    """One file's content for the assistant: a window of its text or rows, or the parts matching ``query``.

    Every address in the file is returned under ``emails`` (structured), so the user can say "email
    these people" about a file they attached; sending still goes through the policy check and approval.
    """
    _ensure_table(ctx)
    with workspace_connection(ctx, readonly=True) as conn:
        row = conn.execute(
            text("SELECT id, filename, kind, size_bytes, content, meta FROM chat_attachments "
                 "WHERE owner_user_id = :o AND id = :i"),
            {"o": ctx.user_id, "i": str(attachment_id or "")},
        ).first()
    if row is None:
        raise AttachmentNotFound(str(attachment_id))
    meta = row.meta if isinstance(row.meta, dict) else json.loads(row.meta or "{}")
    out: dict[str, Any] = {"attachment_id": row.id, "name": row.filename, "kind": row.kind,
                           "emails": meta.get("emails") or [], "emails_total": meta.get("emails_total", 0)}
    for key in ("pages", "sheets", "characters"):
        if key in meta:
            out[key] = meta[key]
    body: str = row.content
    start = max(0, int(start or 0))
    words = _words(query or "")
    if row.kind in ("csv", "xlsx") and "sheets" not in out:
        return {**out, **_table_window(body, words, start)}
    if words:
        from bizos.memory.documents import split

        parts = split(body)
        scored = sorted(((sum(p.casefold().count(w) for w in words), n) for n, p in enumerate(parts)), reverse=True)
        best = sorted(n for score, n in scored[:8] if score > 0)
        chosen, size = [], 0
        for n in best:
            if size + len(parts[n]) > READ_CHARS:
                break
            chosen.append({"part": n + 1, "text": parts[n]})
            size += len(parts[n])
        return {**out, "query": query, "parts_total": len(parts), "passages": chosen,
                "note": "" if chosen else "No part of the file matches those words; read it from the start instead."}
    window = body[start:start + READ_CHARS]
    more = start + READ_CHARS < len(body)
    return {**out, "start": start, "content": window, "next_start": start + READ_CHARS if more else None}


def _table_window(body: str, words: list[str], start: int) -> dict[str, Any]:
    rows = list(csv.reader(io.StringIO(body)))
    if not rows:
        return {"columns": [], "rows": [], "rows_total": 0}
    header, data = rows[0], rows[1:]
    keys = [h or f"column_{i + 1}" for i, h in enumerate(header)]
    if words:
        data = [r for r in data if all(any(w in c.casefold() for c in r) for w in words)]
    picked, size = [], 0
    for row in data[start:]:
        record = {keys[i] if i < len(keys) else f"column_{i + 1}": cell[:300] for i, cell in enumerate(row)}
        size += len(json.dumps(record, ensure_ascii=False))
        if size > READ_CHARS and picked:
            break
        picked.append(record)
    end = start + len(picked)
    return {"columns": keys, "rows_total": len(data), "start": start, "rows": picked,
            "next_start": end if end < len(data) else None, **({"matching": True} if words else {})}
