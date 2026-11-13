"""Safe before/after documents for SIP administration receipts."""
from collections.abc import Mapping
import json


def _masked(value):
    if isinstance(value, Mapping):
        result = {}
        for name, item in value.items():
            key = str(name).lower().replace("_", "").replace("-", "")
            secret = (any(part in key for part in ("password", "secret", "token", "credential", "authorization"))
                      or key in {"key", "apikey", "privatekey", "authusername", "error"})
            if key in {"metadata", "lasttestresult"} and isinstance(item, str):
                try:
                    item = json.loads(item)
                except ValueError:
                    item = "[unavailable]"
            result[name] = ("[configured]" if item else None) if secret else _masked(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_masked(item) for item in value]
    return value


def snapshot(row):
    return _masked(dict(row)) if row is not None else None
