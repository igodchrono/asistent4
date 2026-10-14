# -*- coding: utf-8 -*-
"""Разбор поисковой фразы: намерение, тема, тип, фильтры. Без NLP-библиотек."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ParsedQuery:
    raw: str
    intent: str = "find_info"
    subject: str = ""
    content_type: str = "any"
    quantity: str = "one"
    filters: Dict[str, str] = field(default_factory=dict)
    modifiers: List[str] = field(default_factory=list)
    wants_description: bool = True
    wants_download: bool = False
    language: str = "auto"


_IMAGE = (
    "картин", "изображен", "фото", "фотк", "обои", "picture", "image", "wallpaper", "арт",
)
_VIDEO = ("видео", "ютуб", "youtube", "ролик", "клип", "mp4")
_LINK = ("ссылк", "link", "статью", "статья", "где почитать", "где читать")
_INFO = ("что такое", "кто такой", "кто такая", "как сделать", "объясни", "расскажи", "почему")
_CODE = ("пример кода", "кусок кода", "исходник", "github")
_DOWNLOAD = ("скачай", "скачать", "сохрани", "в чат", "в чате", "пришли", "скинь", "закинь")
_DESCRIBE = ("опиши", "описание", "что на", "расскажи про", "поясни")

_ONE = ("один", "одну", "одно", "лучш", "самую", "самого", "самый")
_FEW = ("несколько", "пару", "пара ", "2-3", "две", "два", "три ")
_MANY = ("много", "список", "побольше", "все подряд", "подборк")

_MODS = (
    ("смешн", "смешные"),
    ("мил", "милые"),
    ("высок", "высокое качество"),
    ("профессионал", "профессиональные"),
    ("минимал", "минималистичные"),
    ("реалист", "реалистичные"),
)

_COLORS = (
    ("черно-бел", "mono"),
    ("чёрно-бел", "mono"),
    ("ч/б", "mono"),
    ("черн", "black"),
    ("красн", "red"),
    ("син", "blue"),
    ("зелен", "green"),
    ("бел", "white"),
    ("цветн", "color"),
)
_SIZES = (("4k", "4k"), ("hd", "hd"), ("больш", "large"), ("маленьк", "small"), ("крупн", "large"))
_FORMATS = ("png", "jpg", "jpeg", "gif", "webp", "mp4")
_SOURCES = (
    ("википед", "wikipedia.org"),
    ("ютуб", "youtube.com"),
    ("youtube", "youtube.com"),
    ("github", "github.com"),
    ("stackoverflow", "stackoverflow.com"),
)


class QueryParser:
    def parse(self, text: str) -> ParsedQuery:
        raw = (text or "").strip()
        low = raw.lower().replace("ё", "е")
        low = " ".join(low.split())
        pq = ParsedQuery(raw=raw)
        pq.content_type = self._content_type(low)
        pq.intent = self._intent(low, pq.content_type)
        pq.quantity = self._quantity(low)
        pq.filters = self._filters(low)
        pq.modifiers = self._modifiers(low)
        pq.wants_download = any(w in low for w in _DOWNLOAD)
        pq.wants_description = any(w in low for w in _DESCRIBE) or pq.intent in ("find_info", "find_link", "find_code")
        pq.language = self._language(low, raw)
        pq.subject = self._subject(low)
        if pq.language == "ru":
            pq.filters.setdefault("language", "ru")
        elif pq.language == "en":
            pq.filters.setdefault("language", "en")
        return pq

    @staticmethod
    def _content_type(low: str) -> str:
        if any(w in low for w in _VIDEO):
            return "video"
        if any(w in low for w in _IMAGE):
            return "image"
        if any(w in low for w in _CODE) or re.search(r"\bкод\b", low):
            return "code"
        if any(w in low for w in _LINK):
            return "article"
        if any(w in low for w in _INFO):
            return "article"
        return "any"

    @staticmethod
    def _intent(low: str, content: str) -> str:
        if content == "image":
            return "find_image"
        if content == "video":
            return "find_link"
        if content == "code":
            return "find_code"
        if any(w in low for w in _LINK):
            return "find_link"
        if any(w in low for w in _INFO):
            return "find_info"
        if any(w in low for w in ("найди", "поищи", "погугли", "загугли")):
            return "find_link"
        return "find_info"

    @staticmethod
    def _quantity(low: str) -> str:
        if any(w in low for w in _MANY):
            return "many"
        if any(w in low for w in _FEW):
            return "few"
        if any(w in low for w in _ONE):
            return "one"
        return "one"

    @staticmethod
    def _filters(low: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for needle, name in _COLORS:
            if needle in low:
                out["color"] = name
                break
        for needle, name in _SIZES:
            if needle in low:
                out["size"] = name
                break
        for fmt in _FORMATS:
            if re.search(r"\b" + fmt + r"\b", low):
                out["format"] = fmt
                break
        for needle, host in _SOURCES:
            if needle in low:
                out["source"] = host
                break
        if any(w in low for w in ("свеж", "новые", "новый", "последн")):
            out["date"] = "recent"
        year = re.search(r"\b(20\d{2})\b", low)
        if year:
            out["date"] = year.group(1)
        if "на русском" in low or "по-русски" in low:
            out["language"] = "ru"
        elif "на английском" in low or "по-английски" in low:
            out["language"] = "en"
        return out

    @staticmethod
    def _modifiers(low: str) -> List[str]:
        found = []
        for needle, label in _MODS:
            if needle in low and label not in found:
                found.append(label)
        return found

    @staticmethod
    def _language(low: str, raw: str) -> str:
        if "на русском" in low or "по-русски" in low:
            return "ru"
        if "на английском" in low or "по-английски" in low:
            return "en"
        letters = re.findall(r"[A-Za-zА-Яа-я]", raw or "")
        if not letters:
            return "auto"
        cyr = sum(1 for ch in letters if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
        return "ru" if cyr >= len(letters) / 2 else "en"

    @staticmethod
    def _subject(low: str) -> str:
        t = low
        patterns = [
            r"^\s*(пожалуйста|можешь|можете|ну|так|слушай)\s+",
            r"\b(найди|найти|поищи|погугли|загугли|покажи|скинь|пришли|скачай|скачать|сохрани)\b",
            r"\b(в интернете|в гугле|в google|в сети|в чат|в чате|мне|для меня)\b",
            r"\b(картинку|картинки|картинка|фотографию|фото|изображение|изображения|видео|статью|ссылку)\b",
            r"\b(один|одну|одно|несколько|пару|много|список|побольше|лучшую|лучшее|лучший)\b",
            r"\b(смешн\w*|мил\w*|профессиональн\w*|реалистичн\w*)\b",
            r"\b(на русском|на английском|по-русски|по-английски)\b",
            r"\b(свежие|свежих|новые|новый|последние)\b",
            r"\b(что такое|кто такой|кто такая|как сделать|где почитать)\b",
            r"\b(пожалуйста)\b",
        ]
        prev = None
        while prev != t:
            prev = t
            for p in patterns:
                t = re.sub(p, " ", t, flags=re.I)
            t = " ".join(t.split())
        t = t.strip(" .,!?:;—-")
        return t or low


if __name__ == "__main__":
    samples = [
        "найди одну смешную картинку рыжих котиков",
        "скинь фото токио в чат",
        "найди статью про asyncio",
        "что такое список в python",
        "несколько картинок заката hd",
        "список ссылок про rust",
    ]
    for s in samples:
        p = QueryParser().parse(s)
        print(f"{s!r}\n  intent={p.intent} type={p.content_type} q={p.quantity} sub={p.subject!r} mods={p.modifiers} dl={p.wants_download} f={p.filters}")
