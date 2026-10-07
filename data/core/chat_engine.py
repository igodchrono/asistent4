# -*- coding: utf-8 -*-
"""ChatEngine: LLM intent → tools → ответ. Плагины = исполнители, не парсеры фраз."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncIterator, Dict, List, Optional

from .llm_client import LLMClient
from .plugin_api import AppContext, HookResult
from .intents import classify as rule_classify, sanitize_intent, strip_search_fluff
from . import mode as assistant_mode

# один поток: tools не блокируют GUI, state не гоняется параллельно
_TOOL_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tool")


INTENT_SCHEMA = """Ты классификатор намерений. Ответь ТОЛЬКО одним JSON без markdown:
{"intent":"<имя>","args":{...},"speak":"<короткая фраза пользователю на русском или пусто>"}

intent:
- chat — разговор, знания, объяснить/описать/написать текст, мнение, шутка, привет (БЕЗ открытия браузера)
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
- pc_open — открыть программу/файл. args: {"target":"<имя>"}
  Триггеры: открой, запусти, стартани, старт
  Примеры:
  * «открой телеграм» → pc_open {target:"telegram"}
  * «запусти vs code» → pc_open {target:"vscode"}
  * «открой блокнот и напиши привет» → pc_open сначала потом pc_type
  * «открой найденное/найденную/этот» → pc_open {target:"найденное"} (использует pc_last_found)
- pc_close — закрыть программу. args: {"target":"<имя>"}
  Триггеры: закрой, заверши, выйди из
  * «закрой блокнот» → pc_close {target:"блокнот"}
  * «закрой телеграм» → pc_close {target:"telegram"}
  * «заверши Discord» → pc_close {target:"discord"}
- pc_volume — громкость. args: {"direction":"up|down"}
  Триггеры: громче/тише, прибавь/убавь звук
- pc_type — напечатать текст в активное окно. args: {"text":"<текст>"}
  Триггеры: напиши, напечатай, введи, набери, вбей
  * «напиши в блокноте привет мир» → pc_type {text:"привет мир"}
  * «напечатай купить молоко» → pc_type {text:"купить молоко"}
- pc_search_files — поиск файлов на дисках. args: {"query":"<имя>","disk":"C"}
  Триггеры: найди файл, найди папку, поищи на диске
  * «найди файл report на диске D» → pc_search_files {query:"report",disk:"D"}
  * «найди папку pictures» → pc_search_files {query:"pictures"}
  * «найди фото на диске C» → pc_search_files {query:"*",disk:"C"}
  * «найди и открой report.txt» → pc_search_files → ПОТОМ pc_open_found
- pc_search_folders — только папки
- pc_open_found — открыть найденный файл (если pc_search_files уже выполнен)
- pc_close_last / pc_create_text / pc_recycle / pc_empty_recycle
- deep_think — «подробно», «максимально точно», «разбери»

Правила web_search:
- Срабатывает на: найди, поищи, погугли, загугли, в интернете, в гугле, поиск, найди картинки/фото/видео, кто такой (если просят найти), сколько стоит (если просят найти цены).
  ПРИМЕРЫ НАЙТИ КАРТИНКУ:
  * «найди картинку рыжей лисы» → web_search {query:"рыжая лиса", mode:"images"}
  * «покажи фото красивых девушек» → web_search {query:"красивые девушки фото", mode:"images"}
  * «скинь обои на рабочий стол» → web_search {query:"обои на рабочий стол", mode:"images"}
  * «найди в интернете картинку заката» → web_search {query:"закат картинка", mode:"images"}
  * «поищи арт с драконами» → web_search {query:"арт драконы", mode:"images"}
  * «скини как выглядит Эйфелева башня» → web_search {query:"Эйфелева башня фото", mode:"images"}
  ВСЕГДА web_search а НЕ imggen когда пользователь ПРОСИТ НАЙТИ/ПОИСКАТЬ готовую картинку.
- В args.query — НЕ копируй фразу пользователя целиком.
  Убери: «найди», «поищи», «пожалуйста», «можешь», «в интернете», «в гугле», «для меня».
  Оставь СУТЬ: ключевые слова, имена, названия, язык запроса как удобно для Google.
  Примеры:
  «найди в интернете как настроить asyncio» → query="asyncio setup tutorial python"
  «поищи картинки рыжих кошек» → query="рыжие кошки", mode="images"
  «погугли курс доллара» → query="курс доллара ЦБ"
- mode=images если: картинк, фото, обои, image, art
- mode=video если: видео, youtube, ютуб, ролик

НЕ web_search:
- «нарисуй / сгенерируй / сделай арт / изобрази / draw» → imggen (не mode=images)
- «нарисуй кота» → imggen (а НЕ web_search mode=images)
- «сделай мне аватарку» → imggen
- «сгенерируй изображение заката» → imggen (а НЕ найди в интернете)
- «переделай / дорисуй картинку» → imggen_edit
- «перепиши / сократи / исправь текст» при загруженном файле → text_edit
- «что такое asyncio» / «объясни» / «расскажи» → chat (ответь сам)
- «опиши закат» / «напиши стих» → chat
- «найди файл X» / «найди папку» → pc_search_files / pc_search_folders
- «открой её / скачай / в чат» ПОСЛЕ поиска картинок → download_image
- «текст со страницы / что там написано» после поиска сайтов → fetch_page

Если не уверен — chat.
"""


class ChatEngine:
    _ANIM_RE = re.compile(r"\[ANIM:[a-zA-Z0-9_]+\]", re.I)

    @staticmethod
    def _strip_anim_tags_only(text: str) -> str:
        return ChatEngine._ANIM_RE.sub("", text or "")

    @classmethod
    def _strip_anim_for_chat(cls, text: str) -> str:
        t = cls._strip_anim_tags_only(text).replace("\r\n", "\n").replace("\r", "\n")
        lines = [ln.rstrip() for ln in t.split("\n")]
        out: list[str] = []
        blanks = 0
        for ln in lines:
            if not ln:
                blanks += 1
                if blanks <= 2:
                    out.append("")
            else:
                blanks = 0
                out.append(ln)
        return "\n".join(out).strip("\n")

    def __init__(self, app: AppContext, llm: LLMClient | None = None):
        self.app = app
        self.llm = llm or LLMClient.from_config(app.config, app)
        app.llm = self.llm
        self.history: List[Dict[str, str]] = []
        self.system_prompt = getattr(app.config, "SYSTEM_PROMPT", "") or "Ты полезный ассистент."
        app.state.setdefault("context", {})
        app.state["engine"] = self
        app.engine = self

    def _ctx(self) -> Dict[str, Any]:
        st = self.app.state
        return {
            "last_screen_desc": str(st.get("screen_vision_last_desc") or "")[:500],
            "last_screen_query": str(st.get("screen_vision_search_query") or ""),
            "last_search_query": str(st.get("last_search_query") or ""),
            "last_search_mode": str(st.get("last_search_mode") or ""),
            "last_search_n": len(st.get("last_search_results") or []),
            "pending_similar": bool(st.get("screen_vision_pending_similar")),
            "imggen_stage": str(st.get("imggen_stage") or ""),
            "last_upload_id": str(st.get("last_upload_id") or ""),
            "has_upload": bool(st.get("last_upload_id") or st.get("uploads")),
            "has_last_image": bool(st.get("phone_media_last")),
            "character": str(
                self.app.get_active_character()
                if hasattr(self.app, "get_active_character")
                else getattr(self.app.config, "ACTIVE_CHARACTER", "")
            ),
            "nsfw": bool(st.get("character_nsfw")),
            "emotion": str(st.get("companion_mood") or st.get("emotion") or "neutral"),
            "mode": assistant_mode.get_mode(self.app),
        }

    def _context_block(self) -> str:
        c = self._ctx()
        lines = ["[CONTEXT]"]
        for k in ("mode", "emotion", "imggen_stage", "last_search_query", "last_search_mode"):
            v = c.get(k)
            if v not in ("", None, False, "neutral"):
                lines.append(f"{k}: {v}")
        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    async def _classify(self, text: str) -> Dict[str, Any]:
        low = text.strip().lower()
        # быстрые подтверждения без LLM
        if low in ("да", "давай", "ок", "окей", "yes", "ага", "угу", "ищи", "найди", "хорошо"):
            if self.app.state.get("screen_vision_pending_similar"):
                return {"intent": "search_similar", "args": {"kind": "generic"}, "speak": ""}
        fast = self._fast_after_search(low)
        if fast:
            print(f"intent fast: {fast}", flush=True)
            return fast
        try:
            ruled = rule_classify(text, self._ctx())
        except Exception as e:
            print(f"intent rules failed: {e}", flush=True)
            ruled = None
        if ruled is not None:
            ruled = sanitize_intent(ruled)
            print(f"intent rules: {ruled.get('intent')} args={ruled.get('args')}", flush=True)
            return ruled
        ctx = self._context_block()
        messages = [
            {"role": "system", "content": INTENT_SCHEMA + "\n\n" + ctx},
            {"role": "user", "content": text},
        ]
        try:
            raw = await self.llm.chat_once(
                messages, temperature=0.1, max_tokens=200, model=self.llm.model
            )
        except Exception as e:
            print(f"intent: classify failed: {e}", flush=True)
            return {"intent": "chat", "args": {}, "speak": ""}
        parsed = sanitize_intent(self._parse_intent(raw))
        if parsed.get("intent") == "chat":
            # маленькая модель часто говорит chat на «найди X» — ещё раз правила
            try:
                again = rule_classify(text, self._ctx())
            except Exception:
                again = None
            if again is not None and sanitize_intent(again).get("intent") != "chat":
                parsed = sanitize_intent(again)
                print(f"intent llm overridden by rules: {parsed}", flush=True)
                return parsed
        print(f"intent llm: {parsed.get('intent')} args={parsed.get('args')}", flush=True)
        return parsed

    def _fast_after_search(self, low: str) -> Optional[Dict[str, Any]]:
        """После поиска не ходить в LLM и не открывать Google заново.

        Только короткие follow-up про УЖЕ найденное. Новые поиски, «скинь файлом»
        и «выбери лучшую» (описать выдачу) сюда не входят.
        """
        has = bool(
            self.app.state.get("last_search_results")
            or self.app.state.get("last_search_query")
        )
        if not has:
            return None
        if any(w in low for w in (
            "найди", "поищи", "погугли", "загугли", "скинь файлом",
            "дай файлом", "сохрани в файл", "сохрани как файл", "отправь файлом",
            "приложи файл", "в виде файла",
        )):
            return None
        idx = 1
        m = re.search(r"(?:^|\s)(?:номер\s*)?(\d{1,2})(?:\s|$)", low)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 20:
                idx = n
        mode = str(self.app.state.get("last_search_mode") or "web")
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

    @staticmethod
    def _parse_intent(raw: str) -> Dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            return {"intent": "chat", "args": {}, "speak": ""}
        # вытащить JSON
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

    def _run_tool(self, intent: str, args: Dict[str, Any]) -> Optional[str]:
        tools = self.app.tools or {}
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
                for m in reversed(self.history or []):
                    if m.get("role") == "assistant":
                        args["content"] = m.get("content") or ""
                        break
            args.setdefault("name", args.get("file") or "")
        # алиасы intent → tool name
        alias = {
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
            "pc_type": "pc_type",
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
        name = alias.get(intent)
        if not name:
            return None
        fn = tools.get(name)
        if not callable(fn):
            return f"Инструмент «{name}» не зарегистрирован (плагин выключен?)."
        try:
            from .tool_args import filter_tool_args
            safe = filter_tool_args(name, args)
        except Exception:
            safe = {}
        try:
            return fn(self.app, **safe)
        except TypeError:
            # args mismatch — вызвать только с app
            try:
                return fn(self.app)
            except Exception as e:
                return f"Ошибка {name}: {e}"
        except Exception as e:
            return f"Ошибка {name}: {e}"

    async def _run_tool_async(self, intent: str, args: Dict[str, Any]) -> Optional[str]:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_TOOL_POOL, self._run_tool, intent, args)

    def _active_plugins(self):
        if hasattr(self.app, "iter_plugins"):
            plugs = list(self.app.iter_plugins())
        else:
            plugs = list((self.app.plugins or {}).values())
        out = []
        for pl in plugs:
            pid = getattr(pl, "id", "") or ""
            if pid and hasattr(self.app, "is_plugin_enabled") and not self.app.is_plugin_enabled(pid):
                continue
            out.append(pl)
        # поза/настроение должны увидеть фразу ДО перехвата notes/memory/imggen
        watch = {"persona", "companion", "character_log", "attachments", "screen"}
        head = [p for p in out if getattr(p, "id", "") in watch]
        tail = [p for p in out if getattr(p, "id", "") not in watch]
        return head + tail

    def _memory_plugin(self):
        plugins = getattr(self.app, "plugins", None) or {}
        return plugins.get("memory")

    def _remember(self, role: str, content: str) -> None:
        mem = self._memory_plugin()
        if mem is None or not hasattr(mem, "record"):
            return
        if hasattr(self.app, "is_plugin_enabled") and not self.app.is_plugin_enabled("memory"):
            return
        if not self.app.get_plugin_setting("memory", "enabled", True):
            return
        try:
            mem.record(role, content)
        except Exception as e:
            print(f"memory record: {e}", flush=True)

    def _schedule_summary(self) -> None:

        mem = self._memory_plugin()
        if mem is None or not hasattr(mem, "maybe_summarize"):
            return
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            loop.create_task(mem.maybe_summarize(self.llm))
        except Exception:
            pass

    async def _refine_search_args(self, user_text: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Вытащить нормальный поисковый запрос из фразы пользователя."""
        args = dict(args or {})
        raw_q = str(args.get("query") or user_text or "").strip()
        mode = str(args.get("mode") or "").lower()
        low = (user_text or "").lower()

        if not mode:
            if any(w in low for w in ("картин", "фото", "обои", "image", "арт", "art ")):
                mode = "images"
            elif any(w in low for w in ("видео", "youtube", "ютуб", "ролик")):
                mode = "video"
            else:
                mode = "web"

        # быстрая чистка без LLM
        q = strip_search_fluff(raw_q)
        if len(q) < 3:
            q = strip_search_fluff(user_text)

        # если всё ещё похоже на целую разговорную фразу — спросить LLM коротко
        need_llm = (
            len(q.split()) > 10
            or any(w in q.lower() for w in ("можешь", "пожалуйста", "хочу", "давай", "мне нужно"))
            or q.lower() == (user_text or "").lower()
        )
        if need_llm:
            try:
                prompt = (
                    "Преврати фразу пользователя в короткий поисковый запрос для Google (3–8 слов). "
                    "Без кавычек и пояснений. Язык: русский или английский — как лучше для поиска.\n"
                    f"Фраза: {user_text}\nЗапрос:"
                )
                refined = await self.llm.chat_once(
                    [
                        {"role": "system", "content": "Ты извлекало поисковых запросов. Ответь одной строкой."},
                        {"role": "user", "content": prompt},
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

    def _history_tail(self) -> int:
        return assistant_mode.history_tail(self.app, 40)

    def _trim_history(self) -> None:
        n = self._history_tail()
        if len(self.history) > n:
            self.history = self.history[-n:]


    def _after_plugins(self, plugs, reply: str) -> str:
        """Плагины видят [ANIM:] и только потом тег вырезается из пузыря."""
        text = reply or ""
        for pl in plugs:
            try:
                text = pl.on_after_llm(text, self.app) or text
            except Exception as e:
                print(f"[plugin {pl.id}] on_after_llm: {e}", flush=True)
        return self._strip_anim_for_chat(text)

    async def _in_character_line(self, tool_text: str, intent: str) -> str:
        """Короткая реплика персонажа поверх сырого результата инструмента."""
        raw = (tool_text or "").strip()
        if len(raw) < 12:
            return raw
        cid = (
            self.app.get_active_character()
            if hasattr(self.app, "get_active_character")
            else getattr(self.app.config, "ACTIVE_CHARACTER", "default")
        )
        card = ""
        try:
            from character_catalog import read_character_card
            card = (read_character_card(str(cid)) or "").strip()[:900]
        except Exception:
            card = ""
        mood = str(self.app.state.get("companion_mood") or self.app.state.get("emotion") or "calm")
        work = assistant_mode.is_work(self.app)
        system = (
            "Ты персонаж из карточки. Одной-двумя фразами своими словами скажи итог действия. "
            "Не выдумывай фактов. Не копируй списки, пути и URL — они будут ниже отдельно. "
            f"Настроение: {mood}. "
            + ("Рабочий режим: без флирта. " if work else "")
            + (f"\n\nКарточка:\n{card}" if card else "")
        )
        try:
            line = await self.llm.chat_once(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Действие: {intent}\nРезультат:\n{raw[:1400]}"},
                ],
                temperature=0.75,
                max_tokens=80,
            )
        except Exception as e:
            print(f"voice-over tool: {e}", flush=True)
            return raw
        line = (line or "").strip()
        if not line:
            return raw
        if len(raw) > 160 or "\n" in raw or "http" in raw.lower() or ":\\" in raw or "/" in raw:
            return f"{line}\n\n{raw}"
        return line

    async def handle_user(self, text: str) -> AsyncIterator[str]:
        text = (text or "").strip()
        if not text:
            return
        self.history.append({"role": "user", "content": text})
        self._remember("user", text)
        self._trim_history()
        import time as _time
        self.app.state["last_user_activity"] = _time.time()
        self.app.state["last_chat_activity"] = _time.time()
        self.app.state["last_user_text"] = text
        switched = assistant_mode.detect_mode_switch(text)
        if switched:
            prev_mode = assistant_mode.get_mode(self.app)
            assistant_mode.set_mode(self.app, switched)
            gui = getattr(self.app, "window", None) or self.app.state.get("gui")
            if gui is not None and hasattr(gui, "refresh_mode_chrome"):
                gui.refresh_mode_chrome()
            if switched != prev_mode:
                print(f"mode switch: {prev_mode} → {switched}", flush=True)

        # 1) редкие sync-перехваты (голос, подтверждения pc) — если плагин сам handled
        plugs = self._active_plugins()
        handled_by = ""
        handled_reply = None
        for pl in plugs:
            try:
                hr = pl.on_user_message(text, self.app)
            except Exception as e:
                print(f"[plugin {pl.id}] on_user_message: {e}", flush=True)
                continue
            if isinstance(hr, HookResult) and hr.handled:
                handled_reply = hr.reply or ""
                handled_by = getattr(pl, "id", "") or ""
                break
        if handled_reply is not None:
            intent_from = {
                "memory": "memory_add",
                "notes": "note_add",
                "reminders": "reminder_add",
                "phone_media": "imggen",
                "pc_control": "pc_open",
            }.get(handled_by, "chat")
            self.app.state["last_intent"] = intent_from
            reply = handled_reply or ""
            if handled_by not in ("persona", "voice", "phone_media", "files", "screen") and len(reply.strip()) > 24:
                reply = await self._in_character_line(reply, intent_from)
            reply = self._after_plugins(plugs, reply)
            self.history.append({"role": "assistant", "content": reply})
            self._remember("assistant", reply)
            self._trim_history()
            self._schedule_summary()
            if reply:
                yield reply
            return

        # 2) классификация намерения
        classified = await self._classify(text)
        intent = classified.get("intent") or "chat"
        args = classified.get("args") if isinstance(classified.get("args"), dict) else {}
        speak = classified.get("speak") or ""

        # «открой её / скачай» после поиска — НЕ pc_open и НЕ повтор URL поиска
        _low = text.lower()
        has_hits = bool(self.app.state.get("last_search_results") or self.app.state.get("last_search_query"))
        want_media = any(
            w in _low
            for w in ("открой", "открыть", "скачай", "сохрани", "в чат", "пришли", "эту картин", "лучш")
        )
        tgt = str(args.get("target") or "").lower().strip(" .!?,…")
        pronounish = tgt in (
            "", "ее", "её", "его", "их", "это", "эту", "этот", "ту", "то",
            "найденное", "ссылку", "картинку", "ее в другой вкладке",
        )
        if has_hits and want_media and (
            intent in ("pc_open_found", "open_last_search", "chat", "web_search")
            or (intent == "pc_open" and pronounish)
        ):
            mode = str(self.app.state.get("last_search_mode") or "web")
            if mode == "images" or "картин" in _low or "фото" in _low or "скач" in _low:
                intent = "download_image"
            else:
                intent = "fetch_page"
            print(f"intent: remap → {intent} (после поиска, не ПК/не URL выдачи)", flush=True)

        print(f"intent: {intent} args={args}", flush=True)
        self.app.state["last_intent"] = intent

        # улучшить query для поиска (не сырая фраза пользователя)
        if intent == "web_search":
            args = await self._refine_search_args(text, args)
            print(f"intent: web_search refined args={args}", flush=True)


        if intent == "deep_think":
            n = 4096
            try:
                n = int(self.app.get_plugin_setting("deep_think", "max_tokens", 4096) or 4096)
            except Exception:
                n = 4096
            self.app.state["llm_max_tokens"] = max(512, min(16000, n))
            intent = "chat"

        # describe_screen: снимок + обычный LLM с vision-вложением
        if intent == "describe_screen":
            await self._run_tool_async("describe_screen", args)
            self.app.state["screen_vision_attach"] = True
            self.app.state["screen_vision_just_captured"] = True
            intent = "chat"

        if intent != "chat":
            result = await self._run_tool_async(intent, args)
            if result is not None:
                reply = (speak + "\n" + result).strip() if speak else result
                if intent not in ("imggen", "imggen_edit", "generate_image"):
                    reply = await self._in_character_line(reply, intent)
                reply = self._after_plugins(plugs, reply)
                self.history.append({"role": "assistant", "content": reply})
                self._remember("assistant", reply)
                self._trim_history()
                self._schedule_summary()
                if reply:
                    yield reply
                return

        # 3) обычный чат: карточка персонажа = личность, SYSTEM_PROMPT = служебное
        cid = (
            self.app.get_active_character()
            if hasattr(self.app, "get_active_character")
            else getattr(self.app.config, "ACTIVE_CHARACTER", "default")
        )
        card = ""
        try:
            from character_catalog import read_character_card
            card = (read_character_card(str(cid)) or "").strip()
        except Exception:
            card = ""

        if card:
            system = (
                "Ты персонаж из карточки, не общий ассистент. "
                "Характер, обращение и границы — только из карточки. "
                "Код — блоком markdown. Стихи — с переводами строк.\n\n"
                f"--- {cid} ---\n{card[:6000]}\n"
            )
            extra_sys = (self.system_prompt or "").strip()
            if extra_sys and len(extra_sys) < 500:
                system += "\n" + extra_sys + "\n"
        else:
            system = self.system_prompt or "Ты живой ассистент."
        system = (system or "") + "\n" + assistant_mode.system_addendum(assistant_mode.get_mode(self.app))
        ctx = self._context_block()
        if ctx:
            system += "\n" + ctx

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        tail = self._history_tail()
        for m in self.history[-tail:]:
            messages.append({"role": m["role"], "content": m["content"]})

        for pl in plugs:
            try:
                messages = pl.on_before_llm(messages, self.app) or messages
            except Exception as e:
                print(f"[plugin {pl.id}] on_before_llm: {e}", flush=True)

        extra: Dict[str, Any] = {}
        if self.app.state.get("llm_max_tokens"):
            extra["max_tokens"] = int(self.app.state.pop("llm_max_tokens", 0) or 0) or None
            if extra["max_tokens"] is None:
                extra.pop("max_tokens", None)
        if self.app.state.get("llm_temperature") is not None:
            extra["temperature"] = float(self.app.state["llm_temperature"])
        elif assistant_mode.is_work(self.app):
            extra["temperature"] = 0.4
        else:
            extra["temperature"] = max(0.72, float(getattr(self.llm, "temperature", 0.75) or 0.75))

        parts: List[str] = []
        model = getattr(self.app.config, "MODEL_NAME", None) or self.llm.model
        async for chunk in self.llm.chat_stream(messages, model=model, **extra):
            parts.append(chunk)
            yield chunk
        reply = self._after_plugins(plugs, "".join(parts))

        if self.history and self.history[-1]["role"] == "assistant":
            self.history[-1]["content"] = reply
        else:
            self.history.append({"role": "assistant", "content": reply})
        self._remember("assistant", reply)
        self._trim_history()
        self._schedule_summary()

    async def generate_proactive(self, instruction: str) -> str:
        """Короткий пинг без записи пользовательской реплики в историю."""
        cid = (
            self.app.get_active_character()
            if hasattr(self.app, "get_active_character")
            else getattr(self.app.config, "ACTIVE_CHARACTER", "default")
        )
        card = ""
        try:
            from character_catalog import read_character_card
            card = (read_character_card(str(cid)) or "").strip()
        except Exception:
            card = ""
        system = card or self.system_prompt or "Ты живой ассистент."
        system += "\n" + assistant_mode.system_addendum(assistant_mode.get_mode(self.app))
        system += "\nОдно короткое сообщение от себя. Без канцелярита, без «как ИИ»."
        if assistant_mode.is_work(self.app):
            system += " Рабочий режим: без флирта."

        try:
            raw = await self.llm.chat_once(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": instruction},
                ],
                temperature=0.7,
                max_tokens=120,
            )
        except Exception as e:
            print(f"proactive: {e}", flush=True)
            return ""
        text = raw or ""
        for pl in self._active_plugins():
            try:
                if hasattr(pl, "on_after_llm"):
                    text = pl.on_after_llm(text, self.app) or text
            except Exception as e:
                print(f"proactive plugin {getattr(pl, 'id', '?')}: {e}", flush=True)
        return self._strip_anim_for_chat(text)
