# -*- coding: utf-8 -*-
"""Исполнение инструментов: интент → имя функции в app.tools → вызов в отдельном потоке.

Модуль не знает про LLM и историю диалога — только про инструменты и их аргументы.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from .intent_schema import prepare_tool_args, resolve_tool_name

# один поток: tools не блокируют GUI, state не гоняется параллельно
_TOOL_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tool")


def run_tool(
	app: Any,
	intent: str,
	args: Dict[str, Any],
	history: Optional[List[Dict[str, str]]] = None,
) -> Optional[str]:
	"""Выполнить инструмент для интента. None — если интент не про инструмент."""
	name = resolve_tool_name(intent)
	if not name:
		return None
	tools = getattr(app, "tools", None) or {}
	fn = tools.get(name)
	if not callable(fn):
		return f"Инструмент «{name}» не зарегистрирован (плагин выключен?)."
	prepared = prepare_tool_args(intent, args, history)
	try:
		from .tool_args import filter_tool_args
		safe = filter_tool_args(name, prepared)
	except Exception:
		safe = {}
	try:
		return fn(app, **safe)
	except TypeError:
		# args mismatch — вызвать только с app
		try:
			return fn(app)
		except Exception as e:
			return f"Ошибка {name}: {e}"
	except Exception as e:
		return f"Ошибка {name}: {e}"


async def run_tool_async(
	app: Any,
	intent: str,
	args: Dict[str, Any],
	history: Optional[List[Dict[str, str]]] = None,
) -> Optional[str]:
	"""То же, но в пуле потоков — не замораживает GUI."""
	loop = asyncio.get_running_loop()
	return await loop.run_in_executor(_TOOL_POOL, run_tool, app, intent, args, history)
