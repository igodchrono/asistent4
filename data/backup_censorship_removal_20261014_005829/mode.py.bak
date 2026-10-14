# -*- coding: utf-8 -*-
"""Режим: компаньон 18+ или работа. Один флаг, без второй личности."""
from __future__ import annotations

from typing import Any, Optional

COMPANION = "companion"
WORK = "work"
MODES = (COMPANION, WORK)

_WORK_PHRASES = (
    "рабочий режим",
    "режим работы",
    "режим работа",
    "давай по делу",
    "хватит флирта",
    "без пошлостей",
    "work mode",
    "давай работать",
    "рабочий тон",
)
_COMP_PHRASES = (
    "режим лисы",
    "режим компаньона",
    "режим компаньон",
    "можно пошло",
    "можно 18",
    "не рабочий",
    "companion mode",
    "давай как обычно",
    "верни лисичку",
    "хватит работы",
)


def _cfg(app_or_cfg: Any) -> Any:
    return getattr(app_or_cfg, "config", None) or app_or_cfg


def normalize_mode(value: str) -> str:
    v = (value or "").strip().lower()
    if v in ("work", "работа", "рабочий", "job", "office"):
        return WORK
    return COMPANION


def get_mode(app_or_cfg: Any) -> str:
    cfg = _cfg(app_or_cfg)
    st = getattr(app_or_cfg, "state", None)
    if isinstance(st, dict) and st.get("assistant_mode"):
        return normalize_mode(str(st.get("assistant_mode")))
    return normalize_mode(str(getattr(cfg, "ASSISTANT_MODE", "") or COMPANION))


def is_work(app_or_cfg: Any) -> bool:
    return get_mode(app_or_cfg) == WORK


def set_mode(app: Any, mode: str) -> str:
    mode = normalize_mode(mode)
    cfg = _cfg(app)
    setattr(cfg, "ASSISTANT_MODE", mode)
    st = getattr(app, "state", None)
    if isinstance(st, dict):
        st["assistant_mode"] = mode
    try:
        from settings_manager import save_settings

        save_settings({"ASSISTANT_MODE": mode})
    except Exception as e:
        print(f"mode save: {e}", flush=True)
    print(f"mode → {mode}", flush=True)
    return mode


def detect_mode_switch(text: str) -> Optional[str]:
    """Только явная команда в начале фразы, не «скинь файл по работе»."""
    low = (text or "").strip().lower().replace("ё", "е")
    if not low:
        return None

    def _hit(phrases: tuple) -> bool:
        for p in phrases:
            if low == p or low.startswith(p + " ") or low.startswith(p + ",") or low.startswith(p + ".") or low.startswith(p + "!"):
                return True
        return False

    if _hit(_WORK_PHRASES):
        return WORK
    if _hit(_COMP_PHRASES):
        return COMPANION
    return None


def system_addendum(mode: str) -> str:
    if normalize_mode(mode) == WORK:
        return (
            "[РЕЖИМ: работа]\n"
            "Сейчас рабочий режим. Порядок:\n"
            "1) Сначала задача (код, поиск, файлы, объяснение) — чётко и по делу.\n"
            "2) Образ персонажа можно оставить (обращение, лёгкий тон), "
            "но без флирта, эротики и пошлости, пока хозяин сам не попросит.\n"
            "3) Не предлагай 18+ и не комментируй внешность.\n"
            "4) Если задача неясна — один уточняющий вопрос, не болтовня.\n"
            "Выход: «режим лисы» / «компаньон» / кнопка в окне.\n"
        )
    return (
        "[РЕЖИМ: компаньон]\n"
        "Личный 18+ компаньон. Флирт и взрослый тон — по карточке и запросу.\n"
        "Команды (поиск, код, ПК) важнее болтовни: сначала делается, потом можно дразнить.\n"
        "Рабочий режим: «давай по делу» / кнопка в окне.\n"
    )


def history_tail(app: Any, fallback: int = 40) -> int:
    cfg = _cfg(app)
    n = 0
    try:
        n = int(getattr(cfg, "HISTORY_TAIL", 0) or 0)
    except (TypeError, ValueError):
        n = 0
    if not n and app is not None and hasattr(app, "get_plugin_setting"):
        try:
            n = int(app.get_plugin_setting("memory", "history_tail", fallback) or fallback)
        except (TypeError, ValueError):
            n = fallback
    if not n:
        n = fallback
    if is_work(app):
        n = max(n, 48)
    return max(12, min(80, n))


def label(mode: str) -> str:
    return "💼 Работа" if normalize_mode(mode) == WORK else "🦊 Компаньон"
