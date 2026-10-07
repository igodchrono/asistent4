# -*- coding: utf-8 -*-
"""Форматирование ответа: вырезание служебных [ANIM:] тегов и чистка пустых строк."""
from __future__ import annotations

import re

ANIM_RE = re.compile(r"\[ANIM:[a-zA-Z0-9_]+\]", re.I)


def strip_anim_tags(text: str) -> str:
	"""Убрать только теги [ANIM:...], сохранив остальной текст как есть."""
	return ANIM_RE.sub("", text or "")


def strip_anim_for_chat(text: str) -> str:
	"""Убрать теги и схлопнуть лишние пустые строки (не больше двух подряд)."""
	t = strip_anim_tags(text).replace("\r\n", "\n").replace("\r", "\n")
	out: list[str] = []
	blanks = 0
	for ln in (x.rstrip() for x in t.split("\n")):
		if not ln:
			blanks += 1
			if blanks <= 2:
				out.append("")
		else:
			blanks = 0
			out.append(ln)
	return "\n".join(out).strip("\n")
