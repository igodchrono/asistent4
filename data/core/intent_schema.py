# -*- coding: utf-8 -*-
"""Схема интентов для LLM, разбор ответа и карта intent → tool.

Здесь только описание намерений и преобразование «интент → имя инструмента».
Самостоятельно инструменты исполняет core/tool_runner, маршрутизацию — core/routing.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

INTENT_SCHEMA = """Ты классификатор намерений. Ответь ТОЛЬКО одним JSON без markdown:

Если пользователь просит НЕСКОЛЬКО действий — верни {"intent":"sequence","args":{"steps":[...]},"speak":"..."}
Каждый шаг — такой же JSON без поля speak:
{"intent":"<имя>","args":{...}}

Примеры:
- «найди погоду и открой браузер» → {"intent":"sequence","args":{"steps":[{"intent":"web_search","args":{"query":"погода","mode":"web"}},{"intent":"pc_open","args":{"target":"chrome"}}]},"speak":"Сейчас всё сделаю"}
- «напиши стих и сохрани в файл» → {"intent":"sequence","args":{"steps":[{"intent":"chat","args":{}},{"intent":"save_file","args":{"name":"stih.txt"}}]},"speak":"Напишу и сохраню"}
- «открой блокнот и калькулятор» → {"intent":"sequence","args":{"steps":[{"intent":"pc_open","args":{"target":"блокнот"}},{"intent":"pc_open","args":{"target":"калькулятор"}}]},"speak":"Открываю"}
- «найди картинки лис и закрой блокнот» → {"intent":"sequence","args":{"steps":[{"intent":"web_search","args":{"query":"лисы","mode":"images"}},{"intent":"pc_close","args":{"target":"блокнот"}}]},"speak":"Ищу и закрываю"}

Если действие одно — верни обычный JSON как раньше:
{"intent":"<имя>","args":{...},"speak":"<короткая фраза пользователю на русском или пусто>"}

intent:
- chat — разговор, знания, объяснить/описать/написать текст, мнение (БЕЗ открытия браузера)
- describe_screen — явно про экран/монитор («что на экране», «посмотри на монитор»)
- web_search — нужно ИСКАТЬ в интернете. args: {"query":"<краткий поисковый запрос 3-8 слов>","mode":"web"|"images"|"video"}
- search_similar — похожее на то что на экране/файл. args: {"kind":"site"|"image"|"generic"}
- download_image — скачать картинку из последней выдачи в чат. args: {"index":1}
- fetch_page — скачать ТЕКСТ страницы из выдачи в чат. args: {"index":1}
- fetch_url — скачать конкретную ссылку (картинка/текст/pdf/json) в чат. args: {"url":"https://..."}
- open_last_search — то же что download_image (картинки) или fetch_page (сайты), НЕ открывать поиск заново

- imggen — СОЗДАТЬ новую картинку (ComfyUI), не искать в гугле.
  Триггеры: нарисуй, нарисовать, сгенерируй, сгенерировать, создай картинку, сделай арт, изобрази, draw, generate.
  НЕ web_search. args: {"prompt":"<сцена>","negative":"","size":"square|portrait|landscape"}
- imggen_edit — править уже загруженную/сгенерированную картинку.
  Триггеры: переделай, измени картинку, дорисуй, перекрась, добавь на картинке, в стиле.
  args: {"prompt":"...","source":"last|uploaded|generated"}

- text_edit — править загруженный текст (txt/md/docx/pdf).
  Триггеры: перепиши, отредактируй, сократи, исправь ошибки, переведи, измени стиль, в официальном стиле.
  args: {"instruction":"<что сделать>","target":"last_upload"}
- file_list — «покажи мои файлы», «что я загружал»
- file_get — «дай ссылку на файл». args: {"file_id":"..."}
- save_file — положить готовый текст/код в чат как файл. Триггеры: скинь файлом, сохрани в файл, дай файлом.
  args: {"name":"player.js","content":"..."}  (content можно не слать — берётся последний ответ)

- memory_add / memory_list / memory_forget
- note_add / note_list / note_find
- reminder_add / reminder_list
- pc_open / pc_close / pc_volume / pc_search_files / pc_search_folders
- pc_open_found / pc_close_last / pc_create_text / pc_recycle / pc_empty_recycle
- deep_think — «подробно», «максимально точно», «разбери»

Правила web_search:
- Срабатывает на: найди, поищи, погугли, загугли, в интернете, в гугле, поиск, найди картинки/фото/видео, кто такой (если просят найти), сколько стоит (если просят найти цены).
- В args.query — НЕ копируй фразу пользователя целиком.
  Убери: «найди», «поищи», «пожалуйста», «можешь», «в интернете», «в гугле», «для меня».
  Оставь СУТЬ: ключевые слова, имена, названия, язык запроса как удобно для Google.
  Примеры:
  «найди в интернете как настроить asyncio» → query="asyncio setup tutorial python"
  «поищи картинки рыжих кошек» → query="рыжие кошки", mode="images"
  «погугли курс доллара» → query="курс доллара ЦБ"
- mode=images если: картинк, фото, обои, image, art
- mode=video если: видео, youtube, ютуб, ролик, клип

НЕ web_search:
- «нарисуй / сгенерируй / сделай арт / изобрази / draw» → imggen (не mode=images)
- «переделай / дорисуй картинку» → imggen_edit
- «перепиши / сократи / исправь текст» при загруженном файле → text_edit
- «что такое asyncio» / «объясни» / «расскажи» → chat (ответь сам)
- «опиши закат» / «напиши стих» → chat
- «найди файл X» / «найди папку» → pc_search_files / pc_search_folders
- «открой её / скачай / в чат» ПОСЛЕ поиска картинок → download_image
- «текст со страницы / что там написано» после поиска сайтов → fetch_page

Если не уверен — chat.
"""

# intent (от правил или LLM) → имя инструмента в app.tools
TOOL_ALIASES: Dict[str, str] = {
    "describe_screen": "describe_screen",
    "web_search": "web_search",
    "search_similar": "search_similar",
    "open_last_search": "open_last_search",
    "download_image": "download_image",
    "fetch_url": "fetch_url",
    "fetch_page": "fetch_page",
    "save_search_result": "download_image",
    "memory_add": "memory_add",
    "memory_list": "memory_list",
    "memory_forget": "memory_forget",
    "note_add": "note_add",
    "note_list": "note_list",
    "note_find": "note_find",
    "reminder_add": "reminder_add",
    "reminder_list": "reminder_list",
    "pc_open": "pc_open",
    "pc_close": "pc_close",
    "pc_volume": "pc_volume",
    "pc_search_files": "pc_search_files",
    "pc_search_folders": "pc_search_folders",
    "pc_open_found": "pc_open_found",
    "pc_close_last": "pc_close_last",
    "pc_create_text": "pc_create_text",
    "pc_recycle": "pc_recycle",
    "pc_empty_recycle": "pc_empty_recycle",
    "deep_think": "deep_think",
    "imggen": "generate_image",
    "imggen_edit": "generate_image",
    "text_edit": "edit_uploaded",
    "file_list": "list_uploads",
    "file_get": "get_file_link",
    "read_uploaded": "read_uploaded",
    "save_file": "send_file",
    "send_file": "send_file",
}


def parse_intent(raw: str) -> Dict[str, Any]:
    """Вытащить JSON интента из ответа модели; при неудаче — безопасный chat."""
    text = (raw or "").strip()
    if not text:
        return {"intent": "chat", "args": {}, "speak": ""}
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {"intent": "chat", "args": {}, "speak": ""}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {"intent": "chat", "args": {}, "speak": ""}
    intent = str(data.get("intent") or "chat").strip()
    args = data.get("args") if isinstance(data.get("args"), dict) else {}
    speak = str(data.get("speak") or "").strip()
    return {"intent": intent, "args": args, "speak": speak}


def parse_multi_intent(raw: str) -> Optional[List[Dict[str, Any]]]:
    """Если LLM вернула sequence — разобрать steps. Иначе None."""
    text = (raw or "").strip()
    if not text:
        return None
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    intent = str(data.get("intent") or "").strip()
    if intent != "sequence":
        return None
    raw_steps = data.get("args", {}).get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return None
    steps: List[Dict[str, Any]] = []
    for s in raw_steps:
        if not isinstance(s, dict):
            continue
        si = str(s.get("intent") or "").strip()
        if not si:
            continue
        sa = s.get("args") if isinstance(s.get("args"), dict) else {}
        steps.append({"intent": si, "args": sa})
    return steps if steps else None


def resolve_tool_name(intent: str) -> Optional[str]:
    """Имя инструмента для интента или None, если интент не про инструмент."""
    return TOOL_ALIASES.get(intent)


def prepare_tool_args(
    intent: str,
    args: Dict[str, Any],
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Дописать обязательные аргументы, которые модель часто не шлёт."""
    args = dict(args or {})
    if intent == "imggen_edit":
        args.setdefault("source", "last")
    if intent in ("imggen", "imggen_edit"):
        if not args.get("prompt"):
            args["prompt"] = args.get("text") or args.get("query") or ""
    if intent == "text_edit" and not args.get("instruction"):
        args["instruction"] = args.get("text") or args.get("query") or ""
    if intent in ("save_file", "send_file"):
        if not args.get("content"):
            for m in reversed(history or []):
                if m.get("role") == "assistant":
                    args["content"] = m.get("content") or ""
                    break
        args.setdefault("name", args.get("file") or "")
    return args