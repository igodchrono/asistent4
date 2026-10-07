# -*- coding: utf-8 -*-
"""Сборка промпта для чата: карточка персонажа + SYSTEM_PROMPT + режим + контекст."""
from __future__ import annotations

from typing import Any, Dict

from . import mode as assistant_mode

CARD_HEADER = (
	"Ты персонаж из карточки, не общий ассистент. "
	"Характер, обращение и границы — только из карточки. "
	"Код — блоком markdown. Стихи — с переводами строк.\n\n"
)

VOICE_OVER_SYSTEM = (
	"Ты персонаж из карточки. Одной-двумя фразами своими словами скажи итог действия. "
	"Не выдумывай фактов. Не копируй списки, пути и URL — они будут ниже отдельно. "
	"Настроение: {mood}. "
)


def active_character_id(app: Any) -> str:
	if hasattr(app, "get_active_character"):
		return str(app.get_active_character())
	return str(getattr(app.config, "ACTIVE_CHARACTER", "default"))


def read_card(character_id: str) -> str:
	try:
		from character_catalog import read_character_card
		return (read_character_card(str(character_id)) or "").strip()
	except Exception:
		return ""


def system_prompt_with_card(app: Any, fallback: str = "Ты живой ассистент.") -> str:
	"""Личность = карточка персонажа, SYSTEM_PROMPT = служебные правила."""
	cid = active_character_id(app)
	card = read_card(cid)
	if not card:
		return fallback
	system = CARD_HEADER + f"--- {cid} ---\n{card[:6000]}\n"
	extra_sys = (fallback or "").strip()
	if extra_sys and len(extra_sys) < 500:
		system += "\n" + extra_sys + "\n"
	return system


def voice_over_system(app: Any) -> str:
	"""Системный промпт для короткой реплики поверх результата инструмента."""
	if hasattr(app, "get_active_character"):
		cid = str(app.get_active_character())
	else:
		cid = str(getattr(app.config, "ACTIVE_CHARACTER", "default"))
	card = read_card(cid)[:900]
	state = getattr(app, "state", {}) or {}
	mood = str(state.get("companion_mood") or state.get("emotion") or "calm")
	system = VOICE_OVER_SYSTEM.format(mood=mood)
	if assistant_mode.is_work(app):
		system += "Рабочий режим: без флирта. "
	if card:
		system += f"\n\nКарточка:\n{card}"
	return system


def context_block(ctx: Dict[str, Any]) -> str:
	"""Служебный [CONTEXT] для классификатора и для чата."""
	lines = ["[CONTEXT]"]
	for k in ("mode", "emotion", "imggen_stage", "last_search_query", "last_search_mode"):
		v = ctx.get(k)
		if v not in ("", None, False, "neutral"):
			lines.append(f"{k}: {v}")
	if len(lines) == 1:
		return ""
	return "\n".join(lines)
