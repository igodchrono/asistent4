# -*- coding: utf-8 -*-
"""Флаги карточки (nsfw) — только для информации.

Ответственность за контент-политику полностью перенесена в LLM.
См. data/CONTENT_POLICY.md

Этот модуль не содержит логики фильтрации контента.
"""

from __future__ import annotations

from typing import Any, Dict


def character_is_nsfw(app) -> bool:
    """(Информационно) Есть ли у персонажа NSFW-контент.

    Возвращает True, если персонаж предназначен для взрослой аудитории.
    Нигде в коде не используется для фильтрации — только для справки.
    """
    try:
        from character_catalog import read_character_card
        cid = ""
        if app is not None:
            if hasattr(app, "get_active_character"):
                cid = str(app.get_active_character() or "")
            else:
                cid = str(getattr(getattr(app, "config", None), "ACTIVE_CHARACTER", "") or "")
        card = read_character_card(cid) or ""
        for raw in (card or "").splitlines()[:60]:
            line = raw.strip().lower()
            if not line or (line.startswith("#") and ":" not in line[:20]):
                continue
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            key, val = key.strip().lower(), val.strip().lower()
            if key == "nsfw":
                return val in ("true", "yes", "1", "on", "18+", "да")
    except Exception:
        pass
    return False


def character_policy(app) -> Dict[str, Any]:
    """(Информационно) Контент-политика персонажа.

    Возвращает информационный словарь для логов/UI.
    Код не принимает решений на основе этих данных.
    """
    cid = ""
    if app is not None:
        if hasattr(app, "get_active_character"):
            cid = str(app.get_active_character() or "")
        else:
            cid = str(getattr(getattr(app, "config", None), "ACTIVE_CHARACTER", "") or "")
    nsfw = character_is_nsfw(app)
    return {
        "id": cid,
        "nsfw": nsfw,
        "mode": "nsfw" if nsfw else "sfw",
    }


def parse_character_policy(card: str) -> Dict[str, Any]:
    """(Информационно) Парсинг политики из карточки.

    Только для чтения; решения принимает LLM.
    """
    nsfw = False
    for raw in (card or "").splitlines()[:60]:
        line = raw.strip().lower()
        if not line or (line.startswith("#") and ":" not in line[:20]):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key, val = key.strip().lower(), val.strip().lower()
        if key == "nsfw":
            nsfw = val in ("true", "yes", "1", "on", "18+", "да")
    return {"nsfw": nsfw, "mode": "nsfw" if nsfw else "sfw"}
