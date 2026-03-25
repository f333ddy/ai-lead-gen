from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo

def get_now_utc() -> datetime:
    return datetime.now(timezone.utc)
DATE_NOW = get_now_utc()

def get_today_midnight_formatted() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)

#START prnewswire
def format_date_prnewswire(time_str: str):
    time_part = time_str.replace(" ET", "")
    now_et = datetime.now(ZoneInfo("America/New_York"))

    dt_et = datetime.strptime(time_part, "%H:%M").replace(
        year=now_et.year,
        month = now_et.month,
        day=now_et.day,
        tzinfo=ZoneInfo("America/New_York")
    )

    return dt_et.astimezone(ZoneInfo("UTC")).isoformat()

def is_today_prnewswire_date(date):
    return "," not in date
#END prnewswire

#START nahb
def parse_nahb_date(date_text: str) -> datetime | None:
    try:
        return datetime.strptime(date_text.strip(), "%b %d, %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def is_nahb_date(date_text: str, target_date: date = datetime.now(timezone.utc).date()) -> bool:
    dt = parse_nahb_date(date_text)
    if not dt:
        return False
    return dt.date() == target_date

def format_date_nahb(date_text: str) -> datetime | None:
    return parse_nahb_date(date_text)
#END nahb

# START chainstoreage
def parse_chainstoreage_date(date_text: str) -> datetime | None:
    if not date_text:
        return None

    value = " ".join(str(date_text).strip().split())
    if not value:
        return None

    formats = [
        # ISO with numeric offset
        "%Y-%m-%dT%H:%M:%S%z",      # 2026-03-23T16:02:27-0500
        "%Y-%m-%dT%H:%M:%S.%f%z",   # 2026-03-23T16:02:27.123456-0500

        # ISO with literal Z
        "%Y-%m-%dT%H:%M:%SZ",       # 2026-03-23T21:02:27Z
        "%Y-%m-%dT%H:%M:%S.%fZ",    # 2026-03-23T21:02:27.123456Z

        # ISO without timezone
        "%Y-%m-%dT%H:%M:%S",        # 2026-03-23T21:02:27
        "%Y-%m-%d %H:%M:%S",        # 2026-03-23 21:02:27

        # Simple dates
        "%Y-%m-%d",                 # 2026-03-23
        "%m/%d/%Y",                 # 3/23/2026 or 03/23/2026

        # Human-readable published/modified variants
        "%a, %m/%d/%Y - %H:%M",     # Tue, 03/24/2026 - 08:04
        "%A, %m/%d/%Y - %H:%M",     # Tuesday, 03/24/2026 - 08:04
        "%a, %b %d, %Y - %H:%M",    # Tue, Mar 24, 2026 - 08:04
        "%A, %b %d, %Y - %H:%M",    # Tuesday, Mar 24, 2026 - 08:04
        "%b %d, %Y",                # Mar 24, 2026
        "%B %d, %Y",                # March 24, 2026
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue

    # Fallback normalization for ISO strings with colon offset, e.g. -05:00
    iso_candidate = value
    if iso_candidate.endswith("Z"):
        iso_candidate = iso_candidate[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass

    return None


def is_today_chainstoreage_date(date_text: str) -> bool:
    dt = parse_chainstoreage_date(date_text)
    if not dt:
        return False
    return dt.date() == DATE_NOW.date()


def format_date_chainstoreage(date_text: str) -> datetime | None:
    return parse_chainstoreage_date(date_text)
# END chainstoreage

