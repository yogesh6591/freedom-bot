"""
Load knowledge paragraphs into a client's memory, then test the assistant on them.

Write paragraphs in a markdown file (see ``knowledge/acme.md`` for the format),
then run against the running API:

    python3 scripts/load_knowledge.py knowledge/acme.md            # load only
    python3 scripts/load_knowledge.py knowledge/acme.md --test     # load, then ask each Q:
    python3 scripts/load_knowledge.py knowledge/acme.md --test-only

Each section is stored as approved memory under a stable key, so re-running
after editing a paragraph records a new version instead of a duplicate.
Standard library only, so it runs from the host against Docker.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

CATEGORIES = {"FACT", "SOP", "DECISION"}
AREAS = {"finance", "hr", "salary", "exec", "legal"}


@dataclass
class Section:
    title: str
    category: str = "FACT"
    area: Optional[str] = None
    lines: list[str] = field(default_factory=list)
    checks: list[tuple[str, list[str]]] = field(default_factory=list)

    @property
    def content(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self.lines)).strip()

    @property
    def key(self) -> str:
        return "kb_" + re.sub(r"[^a-z0-9]+", "_", self.title.casefold()).strip("_")[:70]


def parse(path: Path) -> list[Section]:
    sections: list[Section] = []
    current: Optional[Section] = None
    in_header = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            current = Section(title=line[3:].strip())
            sections.append(current)
            in_header = True
            continue
        if current is None or line.lstrip().startswith("#"):
            continue
        meta = re.match(r"^(category|area)\s*:\s*(.+)$", line.strip(), re.I)
        if in_header and meta:
            name, value = meta.group(1).casefold(), meta.group(2).strip()
            if name == "category":
                if value.upper() not in CATEGORIES:
                    raise SystemExit(f"{current.title}: category must be one of {sorted(CATEGORIES)}")
                current.category = value.upper()
            else:
                if value.casefold() not in AREAS:
                    raise SystemExit(f"{current.title}: area must be one of {sorted(AREAS)}")
                current.area = value.casefold()
            continue
        if line.strip():
            in_header = False
        question = re.match(r"^Q\s*:\s*(.+)$", line.strip(), re.I)
        expect = re.match(r"^Expect\s*:\s*(.+)$", line.strip(), re.I)
        if question:
            current.checks.append((question.group(1).strip(), []))
        elif expect:
            if not current.checks:
                raise SystemExit(f"{current.title}: 'Expect:' needs a 'Q:' before it")
            current.checks[-1][1].append(expect.group(1).strip())
        else:
            current.lines.append(line)
    empty = [s.title for s in sections if not s.content]
    if empty:
        raise SystemExit(f"Sections with no paragraph: {', '.join(empty)}")
    return sections


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token: Optional[str] = None

    def call(self, method: str, path: str, body: Any = None, timeout: int = 180) -> Any:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"{method} {path} failed ({exc.code}): {exc.read().decode()[:400]}")
        except urllib.error.URLError as exc:
            raise SystemExit(f"Cannot reach {self.base} ({exc.reason}). Is `docker compose up` running?")

    def login(self, email: str, password: str, client: str) -> None:
        self.token = self.call(
            "POST", "/api/auth/login", {"email": email, "password": password, "client_slug": client}
        )["access_token"]


def load(api: Api, sections: list[Section]) -> None:
    existing = {
        item.get("memory_key"): item
        for item in api.call("GET", "/api/memory?limit=500").get("items", [])
    }
    for section in sections:
        current = existing.get(section.key) or {}
        if (current.get("current") or {}).get("content") == section.content:
            print(f"  unchanged  {section.title}")
            continue
        api.call(
            "POST",
            "/api/memory",
            {
                "category": section.category,
                "title": section.title,
                "content": section.content,
                "key": section.key,
                "access_area": section.area,
                "tags": ["knowledge-file"],
                "source_type": "MANUAL",
            },
        )
        print(f"  {'updated' if current else 'added'}    {section.title} ({section.category}"
              f"{', ' + section.area if section.area else ''})")


def test(api: Api, sections: list[Section]) -> int:
    checks = [(s.title, q, expects) for s in sections for q, expects in s.checks]
    if not checks:
        print("No 'Q:' lines found — nothing to test.")
        return 0
    failed = 0
    for number, (title, question, expects) in enumerate(checks, 1):
        started = time.time()
        reply = api.call("POST", "/api/chat", {"message": question}).get("content") or ""
        missing = [e for e in expects if e.casefold() not in reply.casefold()]
        ok = not missing
        failed += not ok
        print(f"\n[{'PASS' if ok else 'FAIL'}] #{number} {question}  ({time.time() - started:.0f}s)")
        print(f"       from: {title}")
        if expects:
            print(f"       expect: {', '.join(expects)}" + (f"   missing: {', '.join(missing)}" if missing else ""))
        print(f"       reply: {reply.strip()[:400]}")
    print(f"\n{len(checks) - failed}/{len(checks)} passed")
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path, help="markdown file of knowledge paragraphs")
    parser.add_argument("--client", default="acme")
    parser.add_argument("--email", default=None, help="defaults to admin@<client>.example.com")
    parser.add_argument("--password", default="bizos-dev-password")
    parser.add_argument("--api", default="http://localhost:8001")
    parser.add_argument("--test", action="store_true", help="load, then ask each Q: and check Expect:")
    parser.add_argument("--test-only", action="store_true", help="ask the questions without loading")
    parser.add_argument("--as", dest="ask_as", default=None,
                        help="email to ask the questions as (e.g. employee@acme.example.com)")
    args = parser.parse_args()

    sections = parse(args.file)
    api = Api(args.api)
    api.login(args.email or f"admin@{args.client}.example.com", args.password, args.client)

    if not args.test_only:
        print(f"Loading {len(sections)} sections from {args.file} into '{args.client}':")
        load(api, sections)
    if args.test or args.test_only:
        if args.ask_as:
            api.login(args.ask_as, args.password, args.client)
        sys.exit(1 if test(api, sections) else 0)


if __name__ == "__main__":
    main()
