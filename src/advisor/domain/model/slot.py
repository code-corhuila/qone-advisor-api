"""A weekly class block (shared schema Slot of _shared.yaml). Two slots overlap when they share
the day and `start < other.end and other.start < end`; a class that ends when another starts
does not overlap. The advisor checks a section against the student's confirmed sections with
the same rule the saga applies (INV-ENR-003)."""

import re
from dataclasses import dataclass
from datetime import time
from enum import StrEnum

from advisor.domain.model.errors import FieldError, ValidationError

TIME_PATTERN = re.compile(r"^[0-2][0-9]:[0-5][0-9]$")


class Day(StrEnum):
    MON = "MON"
    TUE = "TUE"
    WED = "WED"
    THU = "THU"
    FRI = "FRI"
    SAT = "SAT"


DAY_RANK = {day: rank for rank, day in enumerate(Day)}


@dataclass(frozen=True)
class Slot:
    day: Day
    start: time
    end: time

    @staticmethod
    def parse(day: str, start: str, end: str, field: str = "slots") -> "Slot":
        """From the contract's strings (`MON`, `08:00`, `10:00`), or ValidationError naming the field."""
        details: list[FieldError] = []
        try:
            parsed_day = Day(day)
        except ValueError:
            parsed_day = None
            details.append(FieldError(f"{field}.day", "must be one of MON, TUE, WED, THU, FRI, SAT"))
        times: list[time] = []
        for name, value in (("start", start), ("end", end)):
            if not isinstance(value, str) or not TIME_PATTERN.match(value) or int(value[:2]) > 23:
                details.append(FieldError(f"{field}.{name}", "must be HH:MM"))
            else:
                times.append(time(int(value[:2]), int(value[3:])))
        if not details and times[0] >= times[1]:
            details.append(FieldError(f"{field}.end", "must be after start"))
        if details:
            raise ValidationError(details)
        assert parsed_day is not None
        return Slot(parsed_day, times[0], times[1])

    def __lt__(self, other: "Slot") -> bool:
        """Slots sort by weekday, then time: Monday before Tuesday, not alphabetically."""
        return (DAY_RANK[self.day], self.start, self.end) < (DAY_RANK[other.day], other.start, other.end)

    def overlaps(self, other: "Slot") -> bool:
        return self.day is other.day and self.start < other.end and other.start < self.end


def any_overlap(slots: list[Slot]) -> bool:
    return any(a.overlaps(b) for i, a in enumerate(slots) for b in slots[i + 1 :])
