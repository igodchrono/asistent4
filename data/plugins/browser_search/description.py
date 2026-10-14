# -*- coding: utf-8 -*-
"""Описание страницы: meta, первый абзац, обрезка. Без выдуманных фактов."""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser


class _MetaGrab(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = ""
        self.og = ""
        self.tw = ""
        self.paragraphs: list[str] = []
        self._skip = 0
        self._in_p = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        d = {str(k).lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            name = (d.get("name") or d.get("property") or "").lower()
            content = unescape(d.get("content") or "").strip()
            if name == "description" and not self.meta:
                self.meta = content
            elif name == "og:description" and not self.og:
                self.og = content
            elif name in ("twitter:description", "twitter:desc") and not self.tw:
                self.tw = content
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "p" and self._skip == 0:
            self._in_p = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        if tag == "p" and self._in_p:
            text = clean(" ".join(self._buf))
            if len(text) >= 80:
                self.paragraphs.append(text)
            self._in_p = False

    def handle_data(self, data):
        if self._in_p and self._skip == 0 and data:
            self._buf.append(data)


def clean(text: str) -> str:
    t = unescape(text or "")
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"(?i)\b(cookie|cookies|принять|подписаться на рассылку)\b.*$", "", t).strip()
    return t


def extract_meta_description(html: str) -> str:
    grab = _MetaGrab()
    try:
        grab.feed(html or "")
        grab.close()
    except Exception:
        return ""
    return clean(grab.meta or grab.og or grab.tw)


def extract_first_paragraph(html: str) -> str:
    grab = _MetaGrab()
    try:
        grab.feed(html or "")
        grab.close()
    except Exception:
        return ""
    return grab.paragraphs[0] if grab.paragraphs else ""


def summarize(text: str, max_sentences: int = 2, llm=None) -> str:
    src = clean(text)
    if not src:
        return ""
    if llm is not None:
        try:
            out = llm(src)
            out = clean(str(out or ""))
            if out:
                return out
        except Exception:
            pass
    parts = re.split(r"(?<=[.!?])\s+", src)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return src[:320]
    return " ".join(parts[: max(1, max_sentences)])[:500]


def describe_html(html: str) -> str:
    meta = extract_meta_description(html)
    if len(meta) >= 40:
        return summarize(meta)
    para = extract_first_paragraph(html)
    return summarize(para or meta)
