"""Record schemas. Every durable ATC record names its schema as ``atc.<kind>/<major>``.

The major version changes only when a change breaks existing readers; new
optional fields keep it. Behaviour a reader must understand to act on a
record is declared separately, as capabilities (see core.capabilities).
"""
from __future__ import annotations

from typing import Mapping

CALLBACK_SCHEMA = "atc.callback/1"


def is_callback(record: object) -> bool:
    return isinstance(record, Mapping) and record.get("schema") == CALLBACK_SCHEMA
