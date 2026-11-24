"""Europe/Kyiv calendar days without a timezone database (Windows has none by default).

Kyiv is UTC+2 (EET) and UTC+3 (EEST); the EU rule applies: summer time starts on the last Sunday of March at 01:00 UTC and ends on
the last Sunday of October at 01:00 UTC. Days are therefore 24 h, except the two change days (23 h in March, 25 h in October)."""
from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc


def _last_sunday(year, month):
    d = date(year, month, 31 if month in (1, 3, 5, 7, 8, 10, 12) else 30)
    while d.weekday() != 6:
        d -= timedelta(days=1)
    return d


def dst_window(year):
    """[start, end) of summer time in `year`, as UTC epochs."""
    s = datetime(year, 3, _last_sunday(year, 3).day, 1, tzinfo=UTC)
    e = datetime(year, 10, _last_sunday(year, 10).day, 1, tzinfo=UTC)
    return s.timestamp(), e.timestamp()


def offset_hours(ts):
    """UTC offset of Kyiv at the UTC instant `ts`: 3 in summer time, else 2."""
    y = datetime.fromtimestamp(ts, UTC).year
    s, e = dst_window(y)
    return 3 if s <= ts < e else 2


def local_dt(ts):
    """Naive datetime of the Kyiv wall clock at the UTC instant `ts`."""
    return datetime.fromtimestamp(ts + offset_hours(ts) * 3600, UTC).replace(tzinfo=None)


def day_of(ts):
    """'YYYY-MM-DD' of the Kyiv calendar day containing the UTC instant `ts`."""
    return local_dt(ts).strftime("%Y-%m-%d")


def hour_of(ts):
    return local_dt(ts).strftime("%Y-%m-%dT%H")


def start_of_day(day):
    """UTC epoch of 00:00 Kyiv time of `day` ('YYYY-MM-DD'). Midnight is never inside a clock change (those happen at 03:00 / 04:00)."""
    y, m, d = (int(x) for x in day.split("-"))
    wall = datetime(y, m, d, tzinfo=UTC).timestamp()
    for off in (3, 2):
        if offset_hours(wall - off * 3600) == off:
            return int(wall - off * 3600)
    return int(wall - 2 * 3600)


def day_bounds(day):
    """[start, end) UTC epochs of a Kyiv calendar day."""
    nxt = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    return start_of_day(day), start_of_day(nxt)


def add_days(day, n):
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def yesterday(now_ts):
    return add_days(day_of(now_ts), -1)
