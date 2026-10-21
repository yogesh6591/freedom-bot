"""Instantly V2 allowed campaign schedule timezones.

Source: Instantly Create Campaign OpenAPI enum for
campaign_schedule.schedules[].timezone.
"""

from __future__ import annotations

INSTANTLY_TIMEZONES: tuple[str, ...] = (
    "Etc/GMT+12",
    "Etc/GMT+11",
    "Etc/GMT+10",
    "America/Anchorage",
    "America/Dawson",
    "America/Creston",
    "America/Chihuahua",
    "America/Boise",
    "America/Belize",
    "America/Chicago",
    "America/Bahia_Banderas",
    "America/Regina",
    "America/Bogota",
    "America/Detroit",
    "America/Indiana/Marengo",
    "America/Caracas",
    "America/Asuncion",
    "America/Glace_Bay",
    "America/Campo_Grande",
    "America/Anguilla",
    "America/Santiago",
    "America/St_Johns",
    "America/Sao_Paulo",
    "America/Argentina/La_Rioja",
    "America/Araguaina",
    "America/Godthab",
    "America/Montevideo",
    "America/Bahia",
    "America/Noronha",
    "America/Scoresbysund",
    "Atlantic/Cape_Verde",
    "Africa/Casablanca",
    "America/Danmarkshavn",
    "Europe/Isle_of_Man",
    "Atlantic/Canary",
    "Africa/Abidjan",
    "Arctic/Longyearbyen",
    "Europe/Belgrade",
    "Africa/Ceuta",
    "Europe/Sarajevo",
    "Africa/Algiers",
    "Africa/Windhoek",
    "Asia/Nicosia",
    "Asia/Beirut",
    "Africa/Cairo",
    "Asia/Damascus",
    "Europe/Bucharest",
    "Africa/Blantyre",
    "Europe/Helsinki",
    "Europe/Istanbul",
    "Asia/Jerusalem",
    "Africa/Tripoli",
    "Asia/Amman",
    "Asia/Baghdad",
    "Europe/Kaliningrad",
    "Asia/Aden",
    "Africa/Addis_Ababa",
    "Europe/Kirov",
    "Europe/Astrakhan",
    "Asia/Tehran",
    "Asia/Dubai",
    "Asia/Baku",
    "Indian/Mahe",
    "Asia/Tbilisi",
    "Asia/Yerevan",
    "Asia/Kabul",
    "Antarctica/Mawson",
    "Asia/Yekaterinburg",
    "Asia/Karachi",
    "Asia/Kolkata",
    "Asia/Colombo",
    "Asia/Kathmandu",
    "Antarctica/Vostok",
    "Asia/Dhaka",
    "Asia/Rangoon",
    "Antarctica/Davis",
    "Asia/Novokuznetsk",
    "Asia/Hong_Kong",
    "Asia/Krasnoyarsk",
    "Asia/Brunei",
    "Australia/Perth",
    "Asia/Taipei",
    "Asia/Choibalsan",
    "Asia/Irkutsk",
    "Asia/Dili",
    "Asia/Pyongyang",
    "Australia/Adelaide",
    "Australia/Darwin",
    "Australia/Brisbane",
    "Australia/Melbourne",
    "Antarctica/DumontDUrville",
    "Australia/Currie",
    "Asia/Chita",
    "Antarctica/Macquarie",
    "Asia/Sakhalin",
    "Pacific/Auckland",
    "Etc/GMT-12",
    "Pacific/Fiji",
    "Asia/Anadyr",
    "Asia/Kamchatka",
    "Etc/GMT-13",
    "Pacific/Apia",
)

INSTANTLY_TIMEZONE_SET = frozenset(INSTANTLY_TIMEZONES)

# Common UI aliases → Instantly enum value
_TIMEZONE_ALIASES: dict[str, str] = {
    "india": "Asia/Kolkata",
    "ist": "Asia/Kolkata",
    "asia/calcutta": "Asia/Kolkata",
    "kolkata": "Asia/Kolkata",
    "america/new_york": "America/Detroit",
    "us/eastern": "America/Detroit",
    "eastern": "America/Detroit",
    "america/denver": "America/Boise",
    "us/mountain": "America/Boise",
    "mountain": "America/Boise",
    "us/central": "America/Chicago",
    "central": "America/Chicago",
    "cdt": "America/Chicago",
    "cst": "America/Chicago",
    "america/los_angeles": "America/Dawson",
    "us/pacific": "America/Dawson",
    "pacific": "America/Dawson",
    "california": "America/Dawson",
    "pdt": "America/Dawson",
    "pst": "America/Dawson",
    "la": "America/Dawson",
    "los_angeles": "America/Dawson",
    # Instantly has no UTC id — map to a GMT-equivalent allowed zone.
    "utc": "Africa/Abidjan",
    "gmt": "Africa/Abidjan",
}

# Short labels for the schedule dropdown (others use the IANA id).
TIMEZONE_LABELS: dict[str, str] = {
    "America/Chicago": "US Central — CDT/CST (Chicago)",
    "America/Detroit": "US Eastern (Detroit)",
    "America/Boise": "US Mountain (Boise)",
    "America/Dawson": "US Pacific — California (Los Angeles)",
    "America/Anchorage": "US Alaska (Anchorage)",
    "Asia/Kolkata": "India — IST (Kolkata)",
    "Asia/Dubai": "UAE (Dubai)",
    "Asia/Hong_Kong": "Hong Kong",
    "Europe/London": "Europe/Isle_of_Man",  # placeholder unused
    "Europe/Isle_of_Man": "UK-ish (Isle of Man)",
    "Europe/Berlin": "Europe/Belgrade",
    "Europe/Belgrade": "Central Europe (Belgrade)",
    "Australia/Melbourne": "Australia (Melbourne)",
    "Pacific/Auckland": "New Zealand (Auckland)",
}


def normalize_instantly_timezone(value: str) -> str:
    """Map free-text / aliases to an Instantly-allowed timezone id."""
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw in INSTANTLY_TIMEZONE_SET:
        return raw
    alias = _TIMEZONE_ALIASES.get(raw.lower().replace(" ", "_"))
    if alias:
        return alias
    # Case-insensitive exact match against enum
    lowered = {tz.lower(): tz for tz in INSTANTLY_TIMEZONES}
    hit = lowered.get(raw.lower())
    return hit or raw


def timezone_choices() -> list[tuple[str, str]]:
    """(value, label) pairs for schedule UI select."""
    preferred = [
        "America/Chicago",  # US Central — platform default
        "America/Detroit",
        "America/Boise",
        "America/Dawson",  # US Pacific / California
        "America/Anchorage",
        "Asia/Kolkata",
        "Asia/Dubai",
        "Europe/Isle_of_Man",
        "Europe/Belgrade",
        "Asia/Hong_Kong",
        "Australia/Melbourne",
        "Pacific/Auckland",
    ]
    seen: set[str] = set()
    choices: list[tuple[str, str]] = []
    for tz in preferred:
        if tz in INSTANTLY_TIMEZONE_SET and tz not in seen:
            choices.append((tz, TIMEZONE_LABELS.get(tz, tz)))
            seen.add(tz)
    for tz in INSTANTLY_TIMEZONES:
        if tz not in seen:
            choices.append((tz, TIMEZONE_LABELS.get(tz, tz)))
            seen.add(tz)
    return choices
