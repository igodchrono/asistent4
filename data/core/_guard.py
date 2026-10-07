# -*- coding: utf-8 -*-
"""Флаги карточки (nsfw). Цензуру контента делает модель, не этот модуль."""
from __future__ import annotations

from typing import Any, Dict

_MODE_NSFW = {"nsfw", "uncensored", "uncensored_adult", "adult", "open"}
_MODE_LOCK = {"full_censor", "censor_all", "lock", "sfw_strict"}


def parse_character_policy(card: str) -> Dict[str, Any]:
    nsfw = False
    mode = "sfw"
    seen_mode = False
    for raw in (card or "").splitlines()[:60]:
        line = raw.strip()
        if not line or (line.startswith("#") and ":" not in line[:20]):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key, val = key.strip().lower(), val.strip()
        if key == "nsfw":
            nsfw = val.lower() in ("true", "yes", "1", "on", "18+", "да")
        elif key in ("content_policy", "policy", "censor"):
            seen_mode = True
            mode = val.lower().split()[0] if val else "sfw"
    if not seen_mode:
        mode = "nsfw" if nsfw else "sfw"
    if mode in _MODE_LOCK:
        nsfw = False
        mode = "full_censor"
    elif mode in _MODE_NSFW or nsfw:
        nsfw = True
        mode = "nsfw"
    else:
        nsfw = False
        mode = "sfw"
    return {"nsfw": bool(nsfw), "mode": mode}


def _active_cid(app) -> str:
    if app is None:
        return ""
    try:
        if hasattr(app, "get_active_character"):
            return str(app.get_active_character() or "")
    except Exception:
        pass
    try:
        return str(getattr(getattr(app, "config", None), "ACTIVE_CHARACTER", "") or "")
    except Exception:
        return ""


def character_policy(app) -> Dict[str, Any]:
    cid = _active_cid(app)
    card = ""
    try:
        from character_catalog import read_character_card
        card = read_character_card(cid) or ""
    except Exception:
        card = ""
    meta = parse_character_policy(card)
    meta["id"] = cid
    st = getattr(app, "state", None) if app is not None else None
    if isinstance(st, dict):
        st["character_nsfw"] = bool(meta["nsfw"])
        st["content_policy"] = meta["mode"]
    return meta


def character_is_nsfw(app) -> bool:
    return bool(character_policy(app).get("nsfw"))
