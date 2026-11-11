"""Small helpers shared by agents."""

from __future__ import annotations

import re
from typing import Any

from forty8.models.common import AgentResult, AuditEntry, ExceptionItem, ResultStatus
from pydantic import BaseModel


def is_empty(cell: Any) -> bool:
    return cell is None or (isinstance(cell, str) and not cell.strip())


def is_number(cell: Any) -> bool:
    return isinstance(cell, (int, float)) and not isinstance(cell, bool)


_MULTIPLIERS = {"k": 1e3, "m": 1e6, "mm": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9, "b": 1e9, "billion": 1e9}


def to_float(value: Any) -> float | None:
    """Parse numbers written as 1,250,000 / $1.2m / 250k / USD 5,000,000. None if not a number."""
    if is_number(value):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.search(r"(-?\d[\d,]*\.?\d*)\s*(million|billion|mm|mn|bn|k|m|b)?\b", value.strip().lower())
    if not match:
        return None
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    return number * _MULTIPLIERS.get(match.group(2) or "", 1.0)


def to_int(value: Any) -> int | None:
    number = to_float(value)
    return int(number) if number is not None and float(number).is_integer() else None


def finish(output: BaseModel | None, audit: list[AuditEntry], exceptions: list[ExceptionItem]) -> AgentResult:
    """PASS when there are no exceptions, otherwise EXCEPTION. The output is kept either way."""
    status = ResultStatus.EXCEPTION if exceptions else ResultStatus.PASS
    return AgentResult(status=status, output=output, audit=audit, exceptions=exceptions)
