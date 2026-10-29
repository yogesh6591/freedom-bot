"""The pre-call brief page is standalone; other admin pages still have the sidebar."""

from types import SimpleNamespace
from uuid import uuid4

from acquisition.templating import create_templates


def _request(path: str):
    return SimpleNamespace(session={}, url=SimpleNamespace(path=path))


def test_brief_page_has_no_sidebar_but_assessments_list_does():
    templates = create_templates()
    request = _request("/assessments")
    brief_html = templates.env.get_template("assessments/pre_call_brief.html").render(
        request=request,
        standalone=True,
        link=SimpleNamespace(id=uuid4()),
        prospect=None,
        header=SimpleNamespace(name="Nina", company=None, title=None, revenue=None, headcount=None, meeting_start=None),
        brief=None,
        brief_status=None,
        brief_error=None,
        generated_at=None,
        flash=None,
    )
    list_html = templates.env.get_template("assessments/list.html").render(
        request=request,
        rows=[],
        total=0,
        kpis=SimpleNamespace(totals=None, by_source=[], by_campaign=[]),
        flash=None,
    )
    assert 'class="sidebar"' not in brief_html
    assert 'href="/prospects"' not in brief_html
    assert 'class="sidebar"' in list_html
    assert 'href="/prospects"' in list_html
    assert "Prospects" in list_html
    assert 'class="app-shell standalone"' in brief_html
    assert "standalone" not in list_html.split("app-shell", 1)[1][:80]
