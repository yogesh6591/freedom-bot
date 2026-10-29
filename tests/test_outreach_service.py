from datetime import datetime, timedelta, timezone

import pytest

from acquisition.services import outreach_service as o


def test_merge_fills_blanks():
    out = o.merge("Hi {{firstName}}, {{ companyName }} {{assessment_url}}",
                  {"firstName": "Ana", "companyName": "Acme", "assessment_url": "http://x/a/t"})
    assert out == "Hi Ana, Acme http://x/a/t"


def test_merge_missing_value_raises():
    # companyName and lastName are optional: a blank value renders empty, not an error.
    assert o.merge("Hi {{firstName}} {{companyName}}", {"firstName": "Ana", "companyName": " "}) == "Hi Ana "
    assert o.merge("{{lastName}}", {"lastName": " "}) == ""
    with pytest.raises(o.OutreachError):
        o.merge("Hi {{firstName}}", {"firstName": " "})


def test_merge_unknown_blank_raises():
    with pytest.raises(o.OutreachError):
        o.merge("{{nope}}", {"firstName": "A"})


def test_split_name():
    assert o.split_name("Ana Maria Lopez") == ("Ana", "Maria Lopez")
    assert o.split_name("") == ("", "")


def test_footer_requires_address():
    with pytest.raises(o.OutreachError):
        o.build_footer("Reply unsubscribe", "  ")
    assert "Cepoch HQ" in o.build_footer("Reply unsubscribe", "Cepoch HQ")


def test_pacing():
    now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
    assert o.can_send_now(sent_today=0, ceiling=400, last_sent_at=None, now=now, gap_seconds=25) == (True, "")
    assert o.can_send_now(sent_today=400, ceiling=400, last_sent_at=None, now=now, gap_seconds=25) == (False, "daily_ceiling")
    assert o.can_send_now(sent_today=1, ceiling=400, last_sent_at=now - timedelta(seconds=10), now=now, gap_seconds=25) == (False, "gap")
    assert o.can_send_now(sent_today=1, ceiling=400, last_sent_at=now - timedelta(seconds=30), now=now, gap_seconds=25)[0]


def test_local_day_start_chicago():
    now = datetime(2026, 10, 28, 3, 0, tzinfo=timezone.utc)  # 22:00 Oct 27 in Chicago (CDT)
    assert o.local_day_start(now, "America/Chicago") == datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc)
