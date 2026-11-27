from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256(obj: Any) -> str:
    data = obj if isinstance(obj, str) else canonical_json(obj)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
