# -*- coding: utf-8 -*-
"""Быстрые правила намерений. None = пусть решит LLM.

Запуск проверки: python intents.py  (из папки data/core или python -c)
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

_TYPOS = (
    (r"\bнати\b", "найди"),
    (r"\bнайти\b", "найди"),
    (r"кортин", "картин"),
    (r"изображене", "изображение"),
    (r"девочь", "девоч"),
    (r"интерент", "интернет"),
    (r"гугол", "google"),
    (r"\bплз\b", "пожалуйста"),
    (r"пажалуста", "пожалуйста"),
    (r"симпотичн", "симпатичн"),
    (r"посмари", "посмотри"),
    (r"выбири", "выбери"),
)

_WEB_VERBS = (
    "найди в интернете", "поищи в интернете", "погугли", "загугли",
    "в гугле", "в google", "в яндексе", "найди в сети",
)
_IMG = ("картин", "фото", "изображ", "обои", "арт ", "art", "image", "wallpaper", "скрин")
_VID = ("видео", "youtube", "ютуб", "ролик", "клип")
_GEN_VERBS = (
    "нарисуй", "нарисовать", "нарисуй-ка", "сгенерируй", "сгенерировать",
    "создай картин", "сделай картин", "сделай изображ", "сделай арт",
    "изобрази", "представь в виде", "draw ", "draw a", "generate an image",
    "generate a picture", "create an image",
)
_EDIT_IMG = (
    "переделай", "дорисуй", "перекрась", "измени картин", "добавь на картин",
    "поправь картин", "отредактируй картин",
)
_EDIT_TEXT = (
    "перепиши", "отредактируй", "сократи", "исправь ошибки", "исправь текст",
    "переведи", "измени стиль", "официальном стиле", "официальный стиль",
)
_SEARCH_FIRST = (
    "найди", "поищи", "погугли", "загугли", "скинь", "где найти", "покажи примеры",
)
_DISK = re.compile(
    r"(?:диск[аеу]?\s+[A-Za-z]\b|\b[A-Za-z]:(?:\\|/|\s|$)|в\s+проводнике|на\s+компьютере|локальн)",
    re.I,
)
_FILE_HINT = ("файл", "папк", "на диск", "на диске", "в папке", "на компьютере")

KNOWN_INTENTS = {
    "chat",
    "describe_screen",
    "web_search",
    "search_similar",
    "download_image",
    "fetch_page",
    "fetch_url",
    "open_last_search",
    "imggen",
    "imggen_edit",
    "text_edit",
    "file_list",
    "file_get",
    "read_uploaded",
    "save_file",
    "send_file",
    "memory_add",
    "memory_list",
    "memory_forget",
    "note_add",
    "note_list",
    "note_find",
    "reminder_add",
    "reminder_list",
    "pc_open",
    "pc_close",
    "pc_volume",
    "pc_type",
    "pc_search_files",
    "pc_search_folders",
    "pc_open_found",
    "pc_close_last",
    "pc_create_text",
    "pc_recycle",
    "pc_empty_recycle",
    "deep_think",
}

_SEARCH_START = re.compile(
    r"^(?:пожалуйста[, ]+|можешь |можете )*"
    r"(?:найди|поищи|погугли|загугли|search for|google)\b",
    re.I,
)
_NOT_WEB = re.compile(
    r"^(?:пожалуйста[, ]+|можешь |можете )*найди\s+(меня|себе|нам|в себе)\b",
    re.I,
)


def normalize_text(text: str) -> str:
    t = (text or "").strip()
    t = t.replace("ё", "е")
    for pat, repl in _TYPOS:
        t = re.sub(pat, repl, t, flags=re.I)
    t = re.sub(r"^\s*(так|ну|слушай|короче|эй|блин|типа)\s*[,:]?\s+", "", t, flags=re.I)
    return " ".join(t.split())


def _low(text: str) -> str:
    return normalize_text(text).lower()


def disk_letter(text: str) -> str:
    t = text or ""
    m = re.search(r"(?:диск[аеу]?\s+|на\s+диске\s+)([A-Za-z])\b", t, re.I)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b([A-Za-z]):(?:\\|/|\s|$)", t)
    return m.group(1).upper() if m else ""


def is_local_search(low: str) -> bool:
    if _DISK.search(low):
        return True
    if any(w in low for w in ("найди файл", "найди папк", "открой файл", "открой папк")):
        return True
    if any(w in low for w in _FILE_HINT) and any(v in low for v in ("найди", "покажи", "открой")):
        return True
    return False


def is_new_similar(low: str) -> bool:
    return any(w in low for w in (
        "найди похож", "поищи похож", "найди такие", "поищи такие",
        "такие же но", "такие же, но", "еще такие", "ещё такие",
        "найди похожие", "покажи похож",
    ))


def is_pick(low: str, has_last_search: bool) -> bool:
    if is_new_similar(low):
        return False
    look = any(w in low for w in (
        "посмотри", "глянь", "посмотр", "на них", "на эти", "на картин",
        "на выдач", "на экран", "на монитор", "на результат", "повнимательн",
    ))
    pick = any(w in low for w in (
        "выбери", "выбрать", "самую", "самого", "лучш", "симпатичн",
        "какая лучше", "какой нравит", "какая нравит", "тебе нравит",
        "больше нравит", "какой мил", "какая мил", "похож",
    ))
    if look and pick:
        return True
    if has_last_search and pick:
        return True
    if has_last_search and any(w in low for w in ("на них", "на эти", "на выдач", "на результат", "на эти картин")):
        return True
    return False


def is_describe_screen(low: str) -> bool:
    screen = any(w in low for w in ("экран", "монитор", "дисплей"))
    ask = any(w in low for w in ("что", "опиши", "покажи что", "посмотри", "глянь", "посмотр", "повнимательн"))
    if screen and ask:
        return True
    return any(w in low for w in (
        "что я смотрю", "что открыто",
    ))


def is_web_search(low: str) -> bool:
    if is_local_search(low):
        return False
    if is_new_similar(low):
        return False
    if any(w in low for w in _WEB_VERBS):
        return True
    if any(w in low for w in ("погугли", "загугли")):
        return True
    if _SEARCH_START.match(low) and not _NOT_WEB.match(low):
        return True
    if any(v in low for v in ("найди", "поищи", "покажи", "скинь")) and (
        any(w in low for w in _IMG + _VID + ("в интернете", "в сети", "статью", "информац", "пример"))
    ):
        return True
    if re.search(r"\b(курс\s+доллара|погода\s+(сегодня|завтра)|новости)\b", low):
        return True
    if re.search(r"\b(найди|поищи)\s+(что-?то|чего-?нибудь|интересн)", low):
        return True
    return False


def sanitize_intent(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Whitelist intent. Мусор от маленькой модели → chat."""
    if not isinstance(data, dict):
        return {"intent": "chat", "args": {}, "speak": ""}
    intent = str(data.get("intent") or "chat").strip()
    if intent not in KNOWN_INTENTS:
        intent = "chat"
    args = data.get("args") if isinstance(data.get("args"), dict) else {}
    speak = str(data.get("speak") or "").strip()
    return {"intent": intent, "args": args, "speak": speak}


def guess_mode(low: str) -> str:
    if any(w in low for w in _VID):
        return "video"
    if any(w in low for w in _IMG):
        return "images"
    return "web"


def strip_search_fluff(text: str) -> str:
    t = normalize_text(text)
    patterns = [
        r"^\s*(пожалуйста\s*[,:]?\s*)",
        r"^\s*(можешь\s+|можете\s+)",
        r"^\s*(найди|найти|поищи|поискать|погугли|загугли|поиск|поищу)\s+",
        r"^\s*(в\s+интернете|в\s+гугле|в\s+google|в\s+сети|онлайн)\s*",
        r"\s*(в\s+интернете|в\s+гугле|в\s+google|пожалуйста)\s*$",
        r"^\s*(мне\s+|для\s+меня\s+)",
        r"^\s*(картинки|картинку|фото|изображения|видео)\s+(по\s+|про\s+|с\s+)?",
        r"^\s*(как\s+найти)\s+",
        r"^\s*(покажи|скинь|хочу)\s+",
    ]
    prev = None
    while prev != t:
        prev = t
        for p in patterns:
            t = re.sub(p, " ", t, flags=re.I)
        t = " ".join(t.split())
    return t.strip(" .,!?:;—-")


def classify(text: str, ctx: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Уверенный intent или None (тогда LLM)."""
    ctx = ctx or {}
    raw = text or ""
    low = _low(raw)
    has_search = bool(ctx.get("last_search_query"))

    if not low:
        return {"intent": "chat", "args": {}, "speak": ""}

    if re.match(r"^(привет|хай|ку|здрасте|ты тут|ты здесь|ау)\b", low.rstrip("?!. ")):
        return {"intent": "chat", "args": {}, "speak": ""}

    if low in ("да", "давай", "ок", "окей", "yes", "ага", "угу", "хорошо", "го"):
        if ctx.get("pending_similar"):
            return {"intent": "search_similar", "args": {"kind": "generic"}, "speak": ""}
        if ctx.get("imggen_stage") in ("confirm", "drafting"):
            return None
        return {"intent": "chat", "args": {}, "speak": ""}

    if is_pick(low, has_search):
        return {"intent": "describe_screen", "args": {}, "speak": ""}
    if is_describe_screen(low):
        return {"intent": "describe_screen", "args": {}, "speak": ""}
    if is_new_similar(low):
        kind = "image" if any(w in low for w in _IMG) else "generic"
        return {"intent": "search_similar", "args": {"kind": kind}, "speak": ""}

    search_first = any(w in low for w in _SEARCH_FIRST) or (
        "покажи" in low and any(w in low for w in _IMG + _VID)
    )
    if any(w in low for w in _EDIT_IMG) or (
        "в стиле" in low and (ctx.get("has_last_image") or any(w in low for w in _IMG))
    ):
        if ctx.get("has_last_image") or any(w in low for w in _IMG):
            prompt = strip_search_fluff(raw)
            return {"intent": "imggen_edit", "args": {"prompt": prompt, "source": "last"}, "speak": ""}
    if (not search_first) and any(w in low for w in _GEN_VERBS):
        prompt = strip_search_fluff(raw)
        for v in (
            "можешь", "можете", "пожалуйста",
            "нарисуй", "нарисовать", "сгенерируй", "сгенерировать",
            "изобрази", "draw", "generate",
        ):
            prompt = re.sub(r"^" + re.escape(v) + r"\s+", "", prompt, flags=re.I)
        return {"intent": "imggen", "args": {"prompt": prompt or raw, "size": "square"}, "speak": ""}

    if any(w in low for w in (
        "скинь файлом", "дай файлом", "сохрани в файл", "сохрани как файл",
        "приложи файл", "в виде файла", "отправь файлом",
    )):
        name = ""
        m = re.search(r"(?:как|имя|назови)\s+([A-Za-z0-9._\-]+\.[A-Za-z0-9]+)", raw, re.I)
        if not m:
            m = re.search(r"([A-Za-z0-9._\-]+\.[A-Za-z0-9]{1,8})\s*$", raw)
        if m:
            name = m.group(1)
        return {"intent": "save_file", "args": {"name": name}, "speak": ""}
    if any(w in low for w in ("покажи мои файлы", "что я загружал", "список загрузок", "мои загрузки")):
        return {"intent": "file_list", "args": {}, "speak": ""}
    if ctx.get("has_upload") or ctx.get("last_upload_id"):
        if any(w in low for w in _EDIT_TEXT) or low.startswith("перепиши") or low.startswith("сократи"):
            return {
                "intent": "text_edit",
                "args": {"instruction": raw, "target": "last_upload"},
                "speak": "",
            }
        if any(w in low for w in ("дай ссылку", "ссылка на файл", "открой загруженн")):
            return {"intent": "file_get", "args": {"file_id": str(ctx.get("last_upload_id") or "")}, "speak": ""}

    if any(w in low for w in (
        "открой ее", "открой её", "открой эту", "открой выбранн",
        "открой в другой вкладк", "открой в новой вкладк",
        "открой найден", "открой то что", "открой ту что",
        "открой картин", "открой ссылк", "открой первую", "открой этот",
    )):
        return {"intent": "open_last_search", "args": {}, "speak": ""}
    if has_search and (low.startswith("открой") or low.startswith("открыть") or low in ("открой", "открыть")):
        if not is_local_search(low) and not any(w in low for w in (
            "блокнот", "калькулятор", "проводник", "папк", "файл ",
            "chrome", "firefox", "explorer",
        )):
            return {"intent": "open_last_search", "args": {}, "speak": ""}

    if is_local_search(low):
        disk = disk_letter(raw) or disk_letter(low)
        if "папк" in low:
            q = strip_search_fluff(re.sub(r"папк[уие]\s*", " ", low, flags=re.I))
            q = re.sub(r"на\s+диске?\s+[a-z]\b", "", q, flags=re.I).strip()
            return {"intent": "pc_search_folders", "args": {"query": q or "folder", "disk": disk}, "speak": ""}
        q = strip_search_fluff(low)
        q = re.sub(r"на\s+диске?\s+[a-z]\b", "", q, flags=re.I).strip()
        if any(w in low for w in _IMG):
            q = q or "картинки"
        return {"intent": "pc_search_files", "args": {"query": q or "*", "disk": disk}, "speak": ""}

    if is_web_search(low):
        q = strip_search_fluff(raw)
        mode = guess_mode(low)
        if len(q) < 2:
            q = "интересное" if "интересн" in low else raw
        return {"intent": "web_search", "args": {"query": q, "mode": mode}, "speak": ""}

    if low.startswith("запомни"):
        body = raw.split(":", 1)[-1].strip() if ":" in raw else (raw.split(" ", 1)[-1] if " " in raw else "")
        return {"intent": "memory_add", "args": {"text": body}, "speak": ""}
    if any(w in low for w in ("что ты помнишь", "что помнишь", "покажи память")):
        return {"intent": "memory_list", "args": {}, "speak": ""}
    if low.startswith("забудь"):
        body = raw.split(" ", 1)[-1] if " " in raw else ""
        return {"intent": "memory_forget", "args": {"text": body}, "speak": ""}

    if low.startswith("напомни"):
        # «напомни как тебя зовут / что я говорил» — разговор, не будильник
        if re.match(r"^напомни(шь)?\s+(как|кто|почему|зачем|что\s+я|что\s+ты|мне\s+как|мне\s+что)\b", low):
            return {"intent": "chat", "args": {}, "speak": ""}
        return {"intent": "reminder_add", "args": {"text": raw}, "speak": ""}
    if "список напоминаний" in low or low == "напоминания":
        return {"intent": "reminder_list", "args": {}, "speak": ""}

    url_m = re.search(r"https?://[^\s<>\"']+", raw, re.I)
    if url_m and any(w in low for w in ("скач", "сохрани", "текст со", "выдай", "в чат", "открой ссыл")):
        return {"intent": "fetch_url", "args": {"url": url_m.group(0)}, "speak": ""}

    if "покажи заметк" in low or low in ("заметки", "покажи заметки"):
        return {"intent": "note_list", "args": {}, "speak": ""}
    if low.startswith("запиши:") or low.startswith("заметка:") or low.startswith("запиши заметк"):
        body = raw.split(":", 1)[-1].strip() if ":" in raw else (raw.split(" ", 1)[-1] if " " in raw else raw)
        return {"intent": "note_add", "args": {"text": body}, "speak": ""}

    # управление ПК: открытие
    if low.startswith("открой ") or low.startswith("открыть "):
        tgt = re.sub(r"\s*(пожалуйста|плиз)\s*$", "", low.split(" ", 1)[-1], flags=re.I).strip(" .!?")
        # открыть найденное/этот/эту → pc_open без target, tool сам подставит pc_last_found
        if tgt.lower().strip(" .!?") in ("найденное", "найденную", "найденный", "найденного",
                                         "найденную папку", "найденный файл", "этот", "это", "эту"):
            return {"intent": "pc_open", "args": {"target": "найденное"}, "speak": ""}
        # любой целевой объект — пробуем pc_open, кроме URL
        if tgt and not tgt.startswith("http") and " " not in tgt:
            return {"intent": "pc_open", "args": {"target": tgt}, "speak": ""}

    if low.startswith("запусти ") or low.startswith("запускать ") or low.startswith("стартуй ") or low.startswith("старт "):
        tgt = low.split(" ", 1)[-1].strip(" .!?")
        if tgt and not tgt.startswith("http"):
            return {"intent": "pc_open", "args": {"target": tgt}, "speak": ""}

    # управление ПК: закрытие
    if low.startswith("закрой ") or low.startswith("закрыть ") or low.startswith("заверши "):
        tgt = low.split(" ", 1)[-1].strip(" .!?")
        if "последн" in tgt:
            return {"intent": "pc_close_last", "args": {}, "speak": ""}
        if tgt:
            return {"intent": "pc_close", "args": {"target": tgt}, "speak": ""}

    # управление ПК: громкость
    if re.search(r"\b(громче|погромче|сделай громче|прибавь звук|сделай громко|увеличь громкость)\b", low):
        return {"intent": "pc_volume", "args": {"direction": "up"}, "speak": ""}
    if re.search(r"\b(тише|потише|сделай тише|убавь звук|сделай тихо|уменьши громкость)\b", low):
        return {"intent": "pc_volume", "args": {"direction": "down"}, "speak": ""}

    # управление ПК: набор текста
    if low.startswith("напечатай ") or low.startswith("набери ") or low.startswith("введи ") or low.startswith("вбей "):
        text = low.split(" ", 1)[-1].strip()
        if len(text) > 1 and not any(w in low for w in ("стих", "рассказ", "сочинение", "письмо", "код")):
            return {"intent": "pc_type", "args": {"text": text}, "speak": ""}
    # «напиши в [программе] текст» — явное указание приложения
    m_in = re.search(r"\bнапиши\s+в\s+(\S+)\s+(.*)", low, re.I)
    if m_in and not any(w in m_in.group(2).lower() for w in ("стих", "рассказ", "сочинение", "письмо", "код")):
        text = m_in.group(2).strip()
        if len(text) > 1:
            return {"intent": "pc_type", "args": {"text": text}, "speak": ""}

    # управление ПК: очистка корзины
    if "очисти корзину" in low or "очистить корзину" in low:
        return {"intent": "pc_empty_recycle", "args": {}, "speak": ""}
    if "создай текстовый файл" in low or low.startswith("создай файл"):
        name = raw.split("файл", 1)[-1].strip() if "файл" in low else "note.txt"
        return {"intent": "pc_create_text", "args": {"name": name}, "speak": ""}

    if any(w in low for w in ("подробно", "максимально точно", "разбери подробно")) and len(low) > 12:
        return {"intent": "deep_think", "args": {}, "speak": ""}

    if any(w in low for w in ("что такое", "объясни", "напиши код", "кусок кода", "как сделать функцию")):
        return {"intent": "chat", "args": {}, "speak": ""}

    return None


if __name__ == "__main__":
    CASES = [
        ("ты тут?", "chat", {}),
        ("напиши кусок кода для плеера браузера", "chat", {}),
        ("что такое asyncio", "chat", {}),
        ("объясни подробно что такое список", "deep_think", {}),
        ("найди картинку аниме девочки акулы", "web_search", {}),
        ("так найди картинку аниме девочки акулы", "web_search", {}),
        ("нати кортинку акулы", "web_search", {}),
        ("покажи картинки кошек", "web_search", {}),
        ("погугли курс доллара", "web_search", {}),
        ("найди в интернете как настроить asyncio", "web_search", {}),
        ("найди видео про лис", "web_search", {}),
        ("найди картинки на диске E", "pc_search_files", {}),
        ("найди файл readme на диске D", "pc_search_files", {}),
        ("найди папку asistent", "pc_search_folders", {}),
        ("посмотри на них и выбери самую похожую", "describe_screen", {"last_search_query": "акула"}),
        ("какая тебе больше нравится", "describe_screen", {"last_search_query": "акула"}),
        ("выбери лучшую", "describe_screen", {"last_search_query": "акула"}),
        ("что у меня сейчас на экране", "describe_screen", {}),
        ("посмотри повнимательней на средний монитор", "describe_screen", {"last_search_query": "акула"}),
        ("найди похожие изображения", "search_similar", {"last_search_query": "акула"}),
        ("такие же но лисичек", "search_similar", {"last_search_query": "акула"}),
        ("открой её", "open_last_search", {"last_search_query": "акула"}),
        ("открой в другой вкладке", "open_last_search", {"last_search_query": "акула"}),
        ("открой найденное", "open_last_search", {"last_search_query": "акула"}),
        ("открой выбранную", "open_last_search", {"last_search_query": "акула"}),
        ("открой", "open_last_search", {"last_search_query": "акула"}),
        ("запомни меня зовут Иван", "memory_add", {}),
        ("что ты помнишь", "memory_list", {}),
        ("забудь про чай", "memory_forget", {}),
        ("напомни через час про чай", "reminder_add", {}),
        ("напомни как тебя зовут", "chat", {}),
        ("напомнишь что я говорил", "chat", {}),
        ("запиши: купить молоко", "note_add", {}),
        ("запиши стихотворение про лису", None, {}),
        ("громче", "pc_volume", {}),
        ("сделай погромче", "pc_volume", {}),
        ("открой калькулятор пожалуйста", "pc_open", {}),
        ("открой блокнот", "pc_open", {}),
        ("открой телеграм", "pc_open", {}),
        ("открой vs code", "pc_open", {}),
        ("открой telegram и напиши привет", None, {}),
        ("запусти дискорд", "pc_open", {}),
        ("запусти steam", "pc_open", {}),
        ("открой найденное", "pc_open", {}),
        ("открой этот", "pc_open", {}),
        ("открой найденный файл", "pc_open", {}),
        ("закрой калькулятор", "pc_close", {}),
        ("закрой телеграм", "pc_close", {}),
        ("заверши блокнот", "pc_close", {}),
        ("напечатай привет мир", "pc_type", {}),
        ("набери купить молоко", "pc_type", {}),
        ("введи для всех привет", "pc_type", {}),
        ("сделай громко", "pc_volume", {}),
        ("убавь звук", "pc_volume", {}),
        ("прибавь громкость", "pc_volume", {}),
        ("мне скучно", None, {}),
        ("найди что-то интересное", "web_search", {}),
        ("нарисуй рыжего кота", "imggen", {}),
        ("можешь нарисовать закат", "imggen", {}),
        ("сгенерировать картинку замка", "imggen", {}),
        ("сгенерируй картинку замка", "imggen", {}),
        ("сделай арт киберпанк город", "imggen", {}),
        ("изобрази закат над морем", "imggen", {}),
        ("draw a red car", "imggen", {}),
        ("найди картинки рыжих кошек", "web_search", {}),
        ("поищи фото Токио", "web_search", {}),
        ("скинь примеры логотипов", "web_search", {}),
        ("переделай последнюю картинку, добавь дождь", "imggen_edit", {"has_last_image": True}),
        ("перепиши в официальном стиле", "text_edit", {"has_upload": True, "last_upload_id": "x.txt"}),
        ("покажи мои файлы", "file_list", {"has_upload": True}),
        ("найди как настроить vscode", "web_search", {}),
        ("найди python asyncio tutorial", "web_search", {}),
        ("поищи документацию по rust", "web_search", {}),
        ("найди меня", None, {}),
        ("скинь файлом", "save_file", {}),

        ("сохрани в файл как player.js", "save_file", {}),
    ]
    fail = 0
    for phrase, expect, ctx in CASES:
        got = classify(phrase, ctx)
        intent = None if got is None else got.get("intent")
        if expect is None:
            ok = got is None or intent == "chat"
        else:
            ok = intent == expect
        if not ok:
            fail += 1
        extra = ""
        if got and got.get("args"):
            extra = " " + str(got["args"])
        print(f"  {'OK ' if ok else 'FAIL'} [{expect}] {phrase!r} → {intent}{extra}")
    print(f"fail={fail}/{len(CASES)}")
    raise SystemExit(fail)
