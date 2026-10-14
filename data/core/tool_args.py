# -*- coding: utf-8 -*-
"""Allowlist аргументов tool-вызовов. LLM не может подсунуть confirmed/force."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Set

# confirmed / force / _internal — только из UI/кода, никогда из JSON модели
_FORBIDDEN = {
    "confirmed",
    "force",
    "internal",
    "_internal",
    "ok",
    "_ok",
    "execute",
    "skip_confirm",
}

_COMMON_SAFE = {"query", "text", "url", "index", "target", "mode", "name", "kind", "direction", "disk"}

TOOL_ARG_ALLOWLIST: Dict[str, Set[str]] = {
    "describe_screen": set(),
    "web_search": {
        "query", "mode", "queries", "text", "into_chat",
        "quantity", "subject", "content_type", "wants_download", "wants_description",
        "modifiers", "language", "filters",
    },
    "search_similar": {"kind", "query"},
    "open_last_search": {"text", "browser_only", "index"},
    "download_image": {"index", "url", "text", "href", "n"},
    "fetch_url": {"url", "href"},
    "fetch_page": {"index", "url", "text"},
    "save_search_result": {"index", "url", "text", "href", "n"},
    "memory_add": {"text", "query"},
    "memory_list": set(),
    "memory_forget": {"text", "query"},
    "note_add": {"text", "query"},
    "note_list": set(),
    "note_find": {"text", "query"},
    "reminder_add": {"text", "query"},
    "reminder_list": set(),
    "reminder_delete": {"text", "query", "id"},
    "pc_open": {"target", "query"},
    "pc_close": {"target", "query"},
    "pc_volume": {"direction"},
    "pc_search_files": {"query", "disk", "text", "path"},
    "pc_search_folders": {"query", "disk", "text"},
    "pc_open_found": set(),
    "pc_close_last": set(),
    "pc_create_text": {"name", "text"},
    "pc_recycle": {"name", "text"},
    "pc_empty_recycle": set(),
    "deep_think": set(),
    "generate_image": {
        "prompt", "text", "query", "negative", "size", "source", "workflow",
        "style", "quality", "seed", "count", "variation", "refine_feedback",
        "reference_strength", "source_strength",
    },
    "edit_uploaded": {"instruction", "text", "query", "target", "file_id"},
    "list_uploads": set(),
    "get_file_link": {"file_id", "name", "text"},
    "read_uploaded": {"file_id", "name", "target"},
    "send_file": {"name", "content", "text", "query", "file"},
    "pick_from_screen": {"request", "query", "text"},
    "pick_similar_to_past": {"n", "query", "text"},
}


def filter_tool_args(tool_name: str, args: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Оставить только разрешённые ключи. confirmed и _* всегда выбрасываются."""
    src = dict(args or {})
    allowed = TOOL_ARG_ALLOWLIST.get(str(tool_name))
    if allowed is None:
        allowed = set(_COMMON_SAFE)
    out: Dict[str, Any] = {}
    for key, value in src.items():
        k = str(key)
        if k in _FORBIDDEN or k.startswith("_"):
            continue
        if k in allowed:
            out[k] = value
    return out
