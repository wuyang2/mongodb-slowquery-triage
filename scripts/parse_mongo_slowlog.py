#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse MongoDB slow query log lines embedded in application logs.

Input: text from stdin (paste logs).
Output: JSON list of extracted entries with normalized fields.

It targets log lines like:
[ERROR] ... 慢查询 {"command":{...},"duration_ms":2181.05} 2026-03-16 ...

The "command" object may include:
- find, aggregate, count
- filter/query
- sort, limit, skip
- projection

This script is intentionally permissive: it tries to locate a JSON object substring
and parse it. If parsing fails, the line is ignored.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple


JSON_CANDIDATE_RE = re.compile(r"(\{.*\})")


@dataclass
class Entry:
    raw_line: str
    duration_ms: Optional[float]
    op: Optional[str]
    collection: Optional[str]
    filter: Optional[Dict[str, Any]]
    sort: Optional[Dict[str, Any]]
    limit: Optional[int]
    skip: Optional[int]
    projection: Optional[Dict[str, Any]]
    full_command: Optional[Dict[str, Any]]


def _first_json_object(line: str) -> Optional[Dict[str, Any]]:
    m = JSON_CANDIDATE_RE.search(line)
    if not m:
        return None

    candidate = m.group(1)

    # Try direct parse first
    try:
        return json.loads(candidate)
    except Exception:
        pass

    # Fallback: attempt to find a valid JSON object by trimming from ends
    # (cheap heuristic for extra trailing text after JSON)
    for end in range(len(candidate), 1, -1):
        sub = candidate[:end]
        try:
            obj = json.loads(sub)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue

    return None


def _normalize_command(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
    cmd = payload.get("command")
    if not isinstance(cmd, dict):
        return None, None, {}

    # Determine operation and collection
    for op_key in ("find", "aggregate", "count"):
        if op_key in cmd:
            return op_key, cmd.get(op_key), cmd

    return None, None, cmd


def parse_lines(text: str) -> List[Entry]:
    out: List[Entry] = []

    for line in text.splitlines():
        payload = _first_json_object(line)
        if not payload:
            continue

        op, coll, cmd = _normalize_command(payload)

        # filter/query differs by op
        filt = None
        if isinstance(cmd, dict):
            if "filter" in cmd and isinstance(cmd.get("filter"), dict):
                filt = cmd.get("filter")
            elif "query" in cmd and isinstance(cmd.get("query"), dict):
                filt = cmd.get("query")

        entry = Entry(
            raw_line=line,
            duration_ms=(payload.get("duration_ms") if isinstance(payload.get("duration_ms"), (int, float)) else None),
            op=op,
            collection=coll if isinstance(coll, str) else None,
            filter=filt,
            sort=(cmd.get("sort") if isinstance(cmd.get("sort"), dict) else None),
            limit=(cmd.get("limit") if isinstance(cmd.get("limit"), int) else None),
            skip=(cmd.get("skip") if isinstance(cmd.get("skip"), int) else None),
            projection=(cmd.get("projection") if isinstance(cmd.get("projection"), dict) else None),
            full_command=cmd if isinstance(cmd, dict) else None,
        )

        out.append(entry)

    return out


def group_by_shape(entries: List[Entry]) -> Dict[str, Any]:
    """Group entries by a naive query shape key.

    Key includes: op, collection, filter keys (top-level), sort keys.
    """

    groups: Dict[str, Dict[str, Any]] = {}

    for e in entries:
        filter_keys = []
        if e.filter:
            filter_keys = sorted(list(e.filter.keys()))

        sort_keys = []
        if e.sort:
            sort_keys = [f"{k}:{e.sort[k]}" for k in sorted(e.sort.keys())]

        key = "|".join([
            str(e.op or ""),
            str(e.collection or ""),
            ",".join(filter_keys),
            ",".join(sort_keys),
            f"limit={e.limit}" if e.limit is not None else "",
        ])

        if key not in groups:
            groups[key] = {
                "shape": {
                    "op": e.op,
                    "collection": e.collection,
                    "filter_keys": filter_keys,
                    "sort": e.sort,
                    "limit": e.limit,
                },
                "count": 0,
                "duration_ms": {
                    "min": None,
                    "max": None,
                    "avg": None,
                },
                "examples": [],
            }

        g = groups[key]
        g["count"] += 1

        d = e.duration_ms
        if isinstance(d, (int, float)):
            cur_min = g["duration_ms"]["min"]
            cur_max = g["duration_ms"]["max"]
            g["duration_ms"]["min"] = d if cur_min is None else min(cur_min, d)
            g["duration_ms"]["max"] = d if cur_max is None else max(cur_max, d)

            # maintain running avg
            # avg = (prev_avg*(n-1)+d)/n
            n = g["count"]
            prev_avg = g["duration_ms"]["avg"]
            g["duration_ms"]["avg"] = d if prev_avg is None else (prev_avg * (n - 1) + d) / n

        if len(g["examples"]) < 3:
            g["examples"].append({
                "duration_ms": e.duration_ms,
                "command": e.full_command,
            })

    return groups


def main() -> int:
    text = sys.stdin.read()
    entries = parse_lines(text)

    result = {
        "entries": [asdict(e) for e in entries],
        "grouped_by_shape": group_by_shape(entries),
        "count": len(entries),
    }

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
