# -*- coding: utf-8 -*-
"""Настройки умного поиска. Берутся из settings.json, иначе эти значения."""
from __future__ import annotations

import json
from typing import Any, Dict

DEFAULTS: Dict[str, Any] = {
    "query_parser": {
        "default_quantity": "one",
        "default_language": "auto",
    },
    "search": {
        "engines": ["duckduckgo", "bing_images", "wikipedia"],
        "max_candidates_per_engine": 10,
        "parallel": True,
        "timeout_seconds": 8,
        "cache_ttl_hours": 24,
        "cache_dir": "cache/search",
        "download_dir": "attachments",
        "max_file_size_mb": 20,
        "weights": {
            "title_match": 0.30,
            "url_relevance": 0.15,
            "modifier_match": 0.15,
            "filter_match": 0.15,
            "source_quality": 0.10,
            "resolution": 0.10,
            "recency": 0.05,
        },
        "source_quality": {
            "wikipedia.org": 1.0,
            "github.com": 0.95,
            "stackoverflow.com": 0.95,
            "youtube.com": 0.8,
            "wikimedia.org": 0.9,
            "pinterest.com": 0.5,
        },
    },
    "response_format": {
        "always_include_description": True,
        "always_include_source": True,
        "max_links": 5,
        "link_style": "block",
    },
}


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> Dict[str, Any]:
    cfg = {
        "query_parser": dict(DEFAULTS["query_parser"]),
        "search": _merge({}, DEFAULTS["search"]),
        "response_format": dict(DEFAULTS["response_format"]),
    }
    try:
        from settings_manager import load_settings
        data = load_settings() or {}
    except Exception:
        data = {}
    for key in ("query_parser", "search", "response_format"):
        if isinstance(data.get(key), dict):
            cfg[key] = _merge(cfg[key], data[key])
    return cfg
