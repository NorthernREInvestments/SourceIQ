"""Helpers for serializing Python objects to JSON-safe structures."""

import json
from datetime import date, datetime


def json_safe(value):
    """Recursively convert sets, frozensets, and other non-JSON types for storage."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return [json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def dumps_json_safe(obj, **kwargs) -> str:
    """json.dumps after converting sets and other non-JSON types."""
    return json.dumps(json_safe(obj), **kwargs)
