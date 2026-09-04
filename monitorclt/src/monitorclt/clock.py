"""Injectable time, so history and retention are testable to the second."""

from __future__ import annotations

import datetime as _dt


class Clock:
    """Wall clock by default; `Clock.fixed(...)` for tests, `.advance()` to move it."""

    def __init__(self, fixed: "_dt.datetime | None" = None) -> None:
        self._fixed = fixed

    @classmethod
    def fixed(cls, iso: str) -> "Clock":
        return cls(parse_ts(iso))

    def now(self) -> _dt.datetime:
        if self._fixed is not None:
            return self._fixed
        return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)

    def now_iso(self) -> str:
        return fmt_ts(self.now())

    def advance(self, **delta: int) -> None:
        if self._fixed is None:
            raise ValueError("cannot advance a wall clock")
        self._fixed = self._fixed + _dt.timedelta(**delta)


def parse_ts(value: str) -> _dt.datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if len(text) == 10:  # bare date
        return _dt.datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc)
    parsed = _dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def fmt_ts(value: _dt.datetime) -> str:
    return value.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def date_only(value: "str | None") -> "str | None":
    if not value:
        return None
    return str(value)[:10]
