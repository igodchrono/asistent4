# -*- coding: utf-8 -*-
"""Маршрутизация: дешёвые решения об интенте без обращения к LLM.

Здесь только чистые функции «текст + состояние → интент». Вызовом LLM
занимается классификатор, исполнением — tool_runner.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional

# «да», «ок», «ищи» — не гоняем через LLM
FAST_CONFIRMS = frozenset(
	("да", "давай", "ок", "окей", "yes", "ага", "угу", "ищи", "найди", "хорошо")
)

# фразы, которые означают НОВОЕ действие, а не работу с прошлой выдачей
_SEARCH_RESTART_WORDS = (
	"найди", "поищи", "погугли", "загугли", "скинь файлом",
	"дай файлом", "сохрани в файл", "сохрани как файл", "отправь файлом",
	"приложи файл", "в виде файла",
)

_INDEX_RE = re.compile(r"(?:^|\s)(?:номер\s*)?(\d{1,2})(?:\s|$)")

_MEDIA_WORDS = ("открой", "открыть", "скачай", "сохрани", "в чат", "пришли", "эту картин", "лучш")

_PRONOUN_TARGETS = (
	"", "ее", "её", "его", "их", "это", "эту", "этот", "ту", "то",
	"найденное", "ссылку", "картинку", "ее в другой вкладке",
)


def fast_confirm(text_low: str, state: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
	"""Подтверждение «да/ок» при ожидании похожего поиска."""
	if text_low in FAST_CONFIRMS and state.get("screen_vision_pending_similar"):
		return {"intent": "search_similar", "args": {"kind": "generic"}, "speak": ""}
	return None


def fast_after_search(state: Mapping[str, Any], low: str) -> Optional[Dict[str, Any]]:
	"""После поиска не ходить в LLM и не открывать Google заново.

	Только короткие follow-up про УЖЕ найденное. Новые поиски, «скинь файлом»
	и «выбери лучшую» (описать выдачу) сюда не входят.
	"""
	if not (state.get("last_search_results") or state.get("last_search_query")):
		return None
	if any(w in low for w in _SEARCH_RESTART_WORDS):
		return None

	idx = 1
	m = _INDEX_RE.search(low)
	if m:
		n = int(m.group(1))
		if 1 <= n <= 20:
			idx = n

	mode = str(state.get("last_search_mode") or "web")
	want_dl = any(
		w in low
		for w in (
			"скач", "пришли картин", "эту картин",
			"выдай картин", "выдай фото", "сохрани картин", "сохрани фото",
		)
	)
	if "скинь" in low and "файл" not in low:
		if any(w in low for w in ("её", "ее", "эту", "это", "перв", "втор", "номер")):
			want_dl = True
		elif low.strip(" .!?") in ("скинь", "скинь её", "скинь ее", "скинь это"):
			want_dl = True
	want_open = (
		low.startswith("открой")
		or low.startswith("открыть")
		or "открой её" in low
		or "открой ее" in low
		or "открой эту" in low
	)
	want_text = any(
		w in low
		for w in ("текст со", "текст страниц", "что там написано", "выдай текст", "содержимое")
	)
	if "вкладк" in low and "чат" not in low:
		return {"intent": "open_last_search", "args": {"browser_only": True}, "speak": ""}
	if want_text:
		return {"intent": "fetch_page", "args": {"index": idx}, "speak": ""}
	if want_dl or want_open:
		if mode == "images" or "картин" in low or "фото" in low or "скач" in low:
			return {"intent": "download_image", "args": {"index": idx}, "speak": ""}
		return {"intent": "fetch_page", "args": {"index": idx}, "speak": ""}
	return None


def has_search_hits(state: Mapping[str, Any]) -> bool:
	return bool(state.get("last_search_results") or state.get("last_search_query"))


def remap_after_search(
	intent: str,
	args: Dict[str, Any],
	user_text: str,
	state: Mapping[str, Any],
) -> str:
	"""«открой её / скачай» после поиска — НЕ pc_open и НЕ повтор URL поиска."""
	if not has_search_hits(state):
		return intent
	low = (user_text or "").lower()
	if not any(w in low for w in _MEDIA_WORDS):
		return intent
	tgt = str((args or {}).get("target") or "").lower().strip(" .!?,…")
	pronounish = tgt in _PRONOUN_TARGETS
	if not (
		intent in ("pc_open_found", "open_last_search", "chat", "web_search")
		or (intent == "pc_open" and pronounish)
	):
		return intent
	mode = str(state.get("last_search_mode") or "web")
	if mode == "images" or "картин" in low or "фото" in low or "скач" in low:
		new_intent = "download_image"
	else:
		new_intent = "fetch_page"
	print(f"intent: remap → {new_intent} (после поиска, не ПК/не URL выдачи)", flush=True)
	return new_intent
