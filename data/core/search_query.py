# -*- coding: utf-8 -*-
"""Поисковый запрос: превратить разговорную фразу в короткий query для поисковика."""
from __future__ import annotations

from typing import Any, Dict

from .intents import strip_search_fluff


def guess_mode(text_low: str) -> str:
	"""Какой режим поиска подразумевает фраза."""
	if any(w in text_low for w in ("картин", "фото", "обои", "image", "арт", "art ")):
		return "images"
	if any(w in text_low for w in ("видео", "youtube", "ютуб", "ролик")):
		return "video"
	return "web"


def looks_like_sentence(query: str, user_text: str) -> bool:
	"""Правда ли, что query — это всё ещё сырая фраза пользователя."""
	q_low = (query or "").lower()
	return (
		len((query or "").split()) > 10
		or any(w in q_low for w in ("можешь", "пожалуйста", "хочу", "давай", "мне нужно"))
		or q_low == (user_text or "").lower()
	)


REFINE_PROMPT = (
	"Преврати фразу пользователя в короткий поисковый запрос для Google (3–8 слов). "
	"Без кавычек и пояснений. Язык: русский или английский — как лучше для поиска.\n"
	"Фраза: {text}\nЗапрос:"
)


async def refine_search_args(
	llm: Any,
	user_text: str,
	args: Dict[str, Any],
) -> Dict[str, Any]:
	"""Вытащить нормальный поисковый запрос из фразы пользователя."""
	args = dict(args or {})
	raw_q = str(args.get("query") or user_text or "").strip()
	low = (user_text or "").lower()
	mode = str(args.get("mode") or "").lower() or guess_mode(low)

	# быстрая чистка без LLM
	q = strip_search_fluff(raw_q)
	if len(q) < 3:
		q = strip_search_fluff(user_text)

	# если всё ещё похоже на целую разговорную фразу — спросить LLM коротко
	if looks_like_sentence(q, user_text):
		try:
			refined = await llm.chat_once(
				[
					{"role": "system", "content": "Ты извлекало поисковых запросов. Ответь одной строкой."},
					{"role": "user", "content": REFINE_PROMPT.format(text=user_text)},
				],
				temperature=0.1,
				max_tokens=40,
			)
			refined = strip_search_fluff((refined or "").strip().strip('"').strip("'"))
			if len(refined) >= 2:
				q = refined
		except Exception as e:
			print(f"intent: refine failed: {e}", flush=True)

	args["query"] = q
	args["mode"] = mode
	return args
