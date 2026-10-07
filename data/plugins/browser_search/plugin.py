# -*- coding: utf-8 -*-
"""Поиск + парсер выдачи + скачивание картинок/текста/файлов в чат."""
from __future__ import annotations

import json
import re
import subprocess
import time
import webbrowser
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request
from urllib.error import HTTPError, URLError

from core.plugin_api import AppContext, Plugin, SettingField

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


class _TextExtract(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "iframe"):
            self._skip += 1
        if tag in ("p", "br", "li", "h1", "h2", "h3", "tr", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "iframe") and self._skip:
            self._skip -= 1
        if tag in ("p", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        t = " ".join((data or "").split())
        if t:
            self.parts.append(t + " ")


class PluginImpl(Plugin):
    id = "browser_search"
    name = "Браузер и поиск"
    version = "4.2.0"
    description = "Парсер выдачи + скачивание картинок/текста/файлов в чат"
    settings_tab = "own"
    settings_tab_title = "Браузер"
    settings_schema = [
        SettingField("enabled", "Включить", "bool", True),
        SettingField(
            "search_engine", "Поисковая система (вкладка)", "choice", "google",
            choices=["google", "yandex", "bing", "duckduckgo"],
        ),
        SettingField(
            "browser", "Браузер", "choice", "default",
            choices=["default", "chrome", "edge", "firefox", "opera", "brave"],
        ),
        SettingField("open_browser", "Открывать вкладку поиска", "bool", True),
        SettingField("parse_results", "Парсить выдачу (ссылки/картинки)", "bool", True),
        SettingField("auto_download_first", "После поиска картинок сразу 1-ю в чат", "bool", True),
        SettingField("open_each_query", "Несколько запросов → несколько вкладок", "bool", True),
        SettingField("max_results", "Сколько результатов держать", "int", 8, min_value=3, max_value=20),
        SettingField("max_text_chars", "Символов текста страницы в чат", "int", 3500, min_value=500, max_value=12000),
    ]

    def on_load(self, app: AppContext) -> None:
        print("🌐 browser_search 4.2: parse + download to chat", flush=True)

    def register_tools(self, app: AppContext) -> None:
        app.tools["web_search"] = self.tool_web_search
        app.tools["search_similar"] = self.tool_search_similar
        app.tools["open_last_search"] = self.tool_open_last
        app.tools["download_image"] = self.tool_download_image
        app.tools["fetch_page"] = self.tool_fetch_page
        app.tools["save_search_result"] = self.tool_download_image
        app.tools["fetch_url"] = self.tool_fetch_url

    def on_user_message(self, text, app):
        # фразы разбирает intents.py, инструменты идут в worker
        return None

    def tool_web_search(
        self, app: AppContext, query: str = "", mode: str = "web", **kwargs
    ) -> str:
        if not app.get_plugin_setting(self.id, "enabled", True):
            return "Поиск выключен."
        mode = (mode or "web").lower()
        queries = kwargs.get("queries") or []
        if isinstance(queries, str):
            queries = [queries]
        queries = [str(x).strip() for x in queries if str(x).strip()]
        if not queries:
            q0 = (query or kwargs.get("text") or "").strip()
            if q0:
                queries = self._split_user_queries(q0)
        if not queries:
            return "Пустой запрос."
        queries = [self._normalize_query(q) or q for q in queries]
        if mode not in ("web", "images", "video"):
            mode = self._guess_mode(queries[0])

        multi = bool(app.get_plugin_setting(self.id, "open_each_query", True))
        to_open = queries if (multi and len(queries) > 1) else [queries[0]]
        q = to_open[0]

        if bool(app.get_plugin_setting(self.id, "open_browser", True)):
            for item in to_open:
                url = self._url(item, mode, app)
                print(f"browser_search: open mode={mode} q={item!r}", flush=True)
                try:
                    self._open(url, app)
                except Exception as e:
                    print(f"browser_search: open fail {e}", flush=True)

        app.state["last_search_query"] = q
        app.state["last_search_queries"] = to_open
        app.state["last_search_mode"] = mode
        app.state["last_search_url"] = self._url(q, mode, app)
        self._emotion(app, q)

        results: List[Dict[str, Any]] = []
        if bool(app.get_plugin_setting(self.id, "parse_results", True)):
            n = int(app.get_plugin_setting(self.id, "max_results", 8) or 8)
            try:
                if mode == "images":
                    results = self._parse_images(q, n)
                elif mode == "video":
                    results = [{"title": q, "url": self._url(q, "video", app), "kind": "video"}]
                else:
                    results = self._parse_web(q, n)
            except Exception as e:
                print(f"browser_search: parse fail {e}", flush=True)
        app.state["last_search_results"] = results
        print(f"browser_search: parsed n={len(results)} mode={mode} q={q!r}", flush=True)

        lines = [f"Нашла по запросу «{q}» ({mode})."]
        if results:
            lines.append("Скажи «скачай 1» / «открой её» / «текст со 2».")
            for i, r in enumerate(results[:8], 1):
                title = (r.get("title") or r.get("url") or "")[:90]
                lines.append(f"{i}. {title}")
            extract0 = str((results[0] or {}).get("extract") or "")
            if extract0:
                lines.append("")
                lines.append(extract0[:900])
        else:
            lines.append("Вкладка поиска открыта. Парсер ничего не вытащил — дай прямую ссылку.")

        if (
            mode == "images"
            and results
            and bool(app.get_plugin_setting(self.id, "auto_download_first", True))
        ):
            extra = self.tool_download_image(app, index=1)
            lines.append("")
            lines.append(extra)
        return "\n".join(lines)

    def tool_search_similar(self, app: AppContext, kind: str = "generic", **kwargs) -> str:
        q = self._similar_query(app, kind) or str(kwargs.get("query") or "").strip()
        if not q:
            return "Нет контекста, что искать похожее."
        mode = "images" if kind in ("image", "images") else "web"
        return self.tool_web_search(app, query=q, mode=mode)

    def tool_open_last(self, app: AppContext, text: str = "", browser_only: bool = False, **kwargs):
        if browser_only:
            url = str(app.state.get("last_search_url") or "")
            q = str(app.state.get("last_search_query") or "")
            if not url and q:
                url = self._url(q, str(app.state.get("last_search_mode") or "web"), app)
            if not url:
                return "Нет последнего поиска."
            self._open(url, app)
            return f"Открыла вкладку поиска: {q or url}"
        mode = str(app.state.get("last_search_mode") or "web")
        idx = self._index_from_text(text) or int(kwargs.get("index") or 1)
        if mode == "images":
            return self.tool_download_image(app, index=idx, text=text)
        return self.tool_fetch_page(app, index=idx, text=text)

    def tool_fetch_url(self, app: AppContext, url: str = "", **kw) -> str:
        src = (url or kw.get("href") or "").strip()
        if not src:
            return "Нет ссылки."
        try:
            from core.net_guard import blocked_url_reason
            reason = blocked_url_reason(src)
            if reason:
                return f"Ссылка заблокирована ({reason}). Качаю только публичные http(s)."
            data, ctype, final = self._http_get(src)
        except Exception as e:
            return f"Не скачать: {e}"
        ext = self._ext_from(data, ctype, final)
        if (ctype or "").startswith("image/") or ext in _IMG_EXT:
            return self.tool_download_image(app, url=final or src)
        dest_dir = self._inbox(app)
        if "pdf" in (ctype or "") or (final or src).lower().endswith(".pdf"):
            dest = dest_dir / f"doc_{int(time.time())}.pdf"
            dest.write_bytes(data)
            app.state["last_downloaded"] = str(dest)
            return f"Скачала PDF ({len(data)} байт)\n[файл: {dest}]"
        if "json" in (ctype or "") or (final or src).lower().endswith(".json"):
            dest = dest_dir / f"data_{int(time.time())}.json"
            dest.write_bytes(data)
            snippet = data.decode("utf-8", "replace")[:2000]
            app.state["last_downloaded"] = str(dest)
            return f"JSON:\n{snippet}\n\n[файл: {dest}]"
        if len(data) > 6_000_000:
            dest = dest_dir / f"bin_{int(time.time())}.bin"
            dest.write_bytes(data[:6_000_000])
            return f"Большой файл, сохранила кусок: {dest}"
        html = data.decode("utf-8", "replace")
        body = self._html_text(html)
        lim = int(app.get_plugin_setting(self.id, "max_text_chars", 3500) or 3500)
        if len(body) > lim:
            body = body[:lim] + "…"
        dest = dest_dir / f"page_{int(time.time())}.txt"
        dest.write_text(body, encoding="utf-8")
        app.state["last_downloaded"] = str(dest)
        return f"Текст со страницы «{final or src}»:\n\n{body}\n\n[файл: {dest}]"

    def tool_download_image(self, app: AppContext, index: int = 1, url: str = "", text: str = "", **kw) -> str:
        results = list(app.state.get("last_search_results") or [])
        q = str(app.state.get("last_search_query") or "")
        if not results and q:
            try:
                results = self._parse_images(q, int(app.get_plugin_setting(self.id, "max_results", 8) or 8))
                app.state["last_search_results"] = results
            except Exception as e:
                print(f"browser_search: reparse images {e}", flush=True)
        idx = int(index or kw.get("n") or 1)
        if idx < 1:
            idx = 1
        src = (url or kw.get("href") or "").strip()
        title = ""
        if not src and results:
            if idx > len(results):
                idx = 1
            hit = results[idx - 1]
            src = str(hit.get("image") or hit.get("url") or "")
            title = str(hit.get("title") or "")
        if not src:
            return "Нет картинки для скачивания. Сначала найди изображения."
        if src.startswith("//"):
            src = "https:" + src
        try:
            data, ctype, final = self._http_get(src)
        except Exception as e:
            return f"Не скачать картинку: {e}"
        if len(data) < 80:
            return "Файл слишком маленький — это не картинка."
        ext = self._ext_from(data, ctype, final)
        if ext not in _IMG_EXT:
            return f"По ссылке не картинка ({ctype or ext}). Дай прямую ссылку на jpg/png."
        dest = self._inbox(app) / f"web_{int(time.time())}_{idx}{ext}"
        dest.write_bytes(data)
        app.state["last_downloaded"] = str(dest)
        app.state["phone_media_last"] = str(dest)
        # НЕ ставим last_attachments — только phone_media_last для генерации
        # иначе потом stale скачанная картинка просачивается в генерацию как референс
        print(f"browser_search: saved image {dest} ({len(data)} b)", flush=True)
        label = title or q or dest.name
        return f"Скачала в чат: {label}\n[фото: {dest}]"

    def tool_fetch_page(self, app: AppContext, index: int = 1, url: str = "", text: str = "", **kw) -> str:
        results = list(app.state.get("last_search_results") or [])
        q = str(app.state.get("last_search_query") or "")
        if not results and q and str(app.state.get("last_search_mode") or "web") != "images":
            try:
                results = self._parse_web(q, int(app.get_plugin_setting(self.id, "max_results", 8) or 8))
                app.state["last_search_results"] = results
            except Exception as e:
                print(f"browser_search: reparse web {e}", flush=True)
        idx = int(index or 1)
        src = (url or "").strip()
        title = ""
        if not src and results:
            if idx < 1 or idx > len(results):
                idx = 1
            hit = results[idx - 1]
            src = str(hit.get("url") or "")
            title = str(hit.get("title") or "")
        if not src:
            return "Нет страницы. Сначала поиск или прямая ссылка."
        return self.tool_fetch_url(app, url=src)

    def _parse_web(self, query: str, n: int) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        try:
            wiki = self._wiki_hit(query)
            if wiki:
                out.append(wiki)
                seen.add(wiki["url"])
        except Exception as e:
            print(f"browser_search: wiki {e}", flush=True)
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
        try:
            raw, _, _ = self._http_get(url)
            html = raw.decode("utf-8", "replace")
        except Exception as e:
            print(f"browser_search: ddg html {e}", flush=True)
            return out
        for m in re.finditer(
            r'<a[^>]+href="([^"]+)"[^>]*class="[^"]*result__a[^"]*"[^>]*>(.*?)</a>'
            r'|<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            re.I | re.S,
        ):
            href = self._unwrap_ddg(m.group(1) or m.group(3) or "")
            title_html = m.group(2) or m.group(4) or ""
            title = re.sub(r"<[^>]+>", "", title_html)
            title = unescape(" ".join(title.split()))
            if not href.startswith("http"):
                continue
            if any(x in href for x in ("duckduckgo.com/y.js", "advert")):
                continue
            if href in seen:
                continue
            seen.add(href)
            out.append({"title": title or href, "url": href, "kind": "web"})
            if len(out) >= n:
                break
        return out

    def _wiki_hit(self, query: str) -> Optional[Dict[str, Any]]:
        api = (
            "https://ru.wikipedia.org/w/api.php?action=opensearch&limit=3&format=json&search="
            + quote_plus(query)
        )
        try:
            raw, _, _ = self._http_get(api, timeout=12)
            data = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return None
        titles = data[1] if len(data) > 1 else []
        descs = data[2] if len(data) > 2 else []
        links = data[3] if len(data) > 3 else []
        if not links:
            return None
        title = titles[0] if titles else query
        extract = descs[0] if descs else ""
        href = links[0]
        extract = extract or self._wiki_summary(title)
        return {
            "title": f"{title} — {extract}"[:160],
            "url": href,
            "kind": "web",
            "extract": extract,
        }

    def _wiki_summary(self, title: str) -> str:
        slug = quote_plus(title.replace(" ", "_"))
        url = f"https://ru.wikipedia.org/api/rest_v1/page/summary/{slug}"
        try:
            raw, _, _ = self._http_get(url, timeout=10)
            data = json.loads(raw.decode("utf-8", "replace"))
            return str(data.get("extract") or "")[:800]
        except Exception:
            return ""

    def _parse_images(self, query: str, n: int) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        for fn in (self._images_openverse, self._images_wikimedia, self._images_bing, self._images_ddg):
            try:
                hits = fn(query, n)
            except Exception as e:
                print(f"browser_search: {fn.__name__} {e}", flush=True)
                hits = []
            for h in hits:
                key = str(h.get("image") or h.get("url") or "")
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(h)
                if len(merged) >= n:
                    return merged
        return merged

    def _images_openverse(self, query: str, n: int) -> List[Dict[str, Any]]:
        url = (
            "https://api.openverse.org/v1/images/"
            f"?q={quote_plus(query)}&page_size={max(n, 5)}"
        )
        raw, _, _ = self._http_get(url)
        data = json.loads(raw.decode("utf-8", "replace"))
        out = []
        for r in data.get("results") or []:
            img = r.get("url") or r.get("thumbnail")
            if not img:
                continue
            out.append({
                "title": r.get("title") or query,
                "url": r.get("foreign_landing_url") or img,
                "image": img,
                "thumb": r.get("thumbnail") or img,
                "kind": "image",
            })
            if len(out) >= n:
                break
        return out

    def _images_wikimedia(self, query: str, n: int) -> List[Dict[str, Any]]:
        url = (
            "https://commons.wikimedia.org/w/api.php?action=query&format=json"
            "&generator=search&gsrsearch=" + quote_plus(query)
            + f"&gsrnamespace=6&gsrlimit={n}&prop=imageinfo&iiprop=url|size"
        )
        raw, _, _ = self._http_get(url)
        data = json.loads(raw.decode("utf-8", "replace"))
        pages = ((data.get("query") or {}).get("pages") or {})
        out = []
        for p in pages.values():
            info = (p.get("imageinfo") or [{}])[0]
            img = info.get("url")
            if not img:
                continue
            out.append({
                "title": p.get("title") or query,
                "url": img,
                "image": img,
                "kind": "image",
            })
            if len(out) >= n:
                break
        return out

    def _images_bing(self, query: str, n: int) -> List[Dict[str, Any]]:
        url = (
            "https://www.bing.com/images/async?q="
            + quote_plus(query)
            + "&first=0&count=%d" % max(n, 8)
        )
        raw, _, _ = self._http_get(url)
        html = raw.decode("utf-8", "replace")
        out: List[Dict[str, Any]] = []
        seen = set()
        pat = re.compile(r"https?://[^\s\"\'<>]+\.(?:jpg|jpeg|png|gif|webp)", re.I)
        html = unescape(html)
        for m in pat.finditer(html):
            img = unescape(m.group(0))
            if "bing.com" in img or img in seen:
                continue
            seen.add(img)
            out.append({"title": query, "url": img, "image": img, "kind": "image"})
            if len(out) >= n:
                break
        return out

    def _images_ddg(self, query: str, n: int) -> List[Dict[str, Any]]:
        home = "https://duckduckgo.com/?q=" + quote_plus(query) + "&iax=images&ia=images"
        raw, _, _ = self._http_get(home)
        html = raw.decode("utf-8", "replace")
        m = re.search(r"vqd=([\"']?)([0-9-]+)\1", html)
        if not m:
            m = re.search(r"vqd=([0-9-]+)", html)
        if not m:
            return []
        vqd = m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1)
        api = (
            "https://duckduckgo.com/i.js?l=wt-wt&o=json&q="
            + quote_plus(query)
            + "&vqd=" + vqd + "&f=,,,"
        )
        raw2, _, _ = self._http_get(api)
        data = json.loads(raw2.decode("utf-8", "replace"))
        out = []
        for r in data.get("results") or []:
            img = r.get("image") or r.get("thumbnail")
            if not img:
                continue
            out.append({
                "title": r.get("title") or query,
                "url": r.get("url") or img,
                "image": img,
                "kind": "image",
            })
            if len(out) >= n:
                break
        return out

    def _http_get(self, url: str, timeout: int = 25) -> Tuple[bytes, str, str]:
        from core.net_guard import blocked_url_reason, guarded_urlopen

        reason = blocked_url_reason(url)
        if reason:
            raise URLError(f"blocked url: {reason}")
        req = Request(
            url,
            headers={
                "User-Agent": _UA,
                "Accept": "*/*",
                "Accept-Language": "ru,en;q=0.8",
            },
        )
        with guarded_urlopen(url, req=req, timeout=timeout) as resp:
            data = resp.read()
            ctype = ""
            try:
                ctype = resp.headers.get_content_type() or ""
            except Exception:
                ctype = str(resp.headers.get("Content-Type") or "")
            return data, ctype, resp.geturl()

    def _http_get_safe(self, url: str, timeout: int = 25) -> Tuple[bytes, str, str]:
        try:
            return self._http_get(url, timeout=timeout)
        except (HTTPError, URLError, TimeoutError, OSError):
            return b"", "", url

    def _inbox(self, app: AppContext) -> Path:
        root = Path(__file__).resolve().parents[2] / "attachments" / datetime.now().strftime("%Y-%m-%d")
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _ext_from(data: bytes, ctype: str, url: str) -> str:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return ".png"
        if data[:3] == b"\xff\xd8\xff":
            return ".jpg"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return ".gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp"
        c = (ctype or "").lower()
        if "png" in c:
            return ".png"
        if "gif" in c:
            return ".gif"
        if "webp" in c:
            return ".webp"
        if "jpeg" in c or "jpg" in c:
            return ".jpg"
        path = urlparse(url).path.lower()
        for e in _IMG_EXT:
            if path.endswith(e):
                return e
        return ".bin"

    @staticmethod
    def _html_text(html: str) -> str:
        p = _TextExtract()
        try:
            p.feed(html)
            p.close()
        except Exception:
            pass
        t = "".join(p.parts)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()

    @staticmethod
    def _unwrap_ddg(href: str) -> str:
        href = unquote(href or "")
        if href.startswith("//"):
            href = "https:" + href
        if "uddg=" in href:
            q = parse_qs(urlparse(href).query)
            if q.get("uddg"):
                return unquote(q["uddg"][0])
        return href

    @staticmethod
    def _index_from_text(text: str) -> int:
        low = (text or "").lower()
        m = re.search(r"(?:^|\s)(?:номер\s*)?(\d{1,2})(?:\s|$)", low)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 20:
                return n
        words = {
            "перв": 1, "втор": 2, "трет": 3, "треть": 3,
            "четвёрт": 4, "четверт": 4, "пят": 5,
        }
        for w, n in words.items():
            if w in low:
                return n
        return 1

    @staticmethod
    def _split_user_queries(text: str) -> list:
        raw = (text or "").strip()
        if not raw:
            return []
        parts = re.split(
            r"\s+(?:и\s+ещ[её]|и\s+также|а\s+также|и\s+потом)\s+",
            raw,
            flags=re.I,
        )
        if len(parts) < 2:
            parts = re.split(r"\s+и\s+(?=найди|найти|поищи|погугли|картин)", raw, flags=re.I)
        out: List[str] = []
        for p in parts:
            p = re.sub(r"^(найди|найти|поищи|погугли|загугли)\s+", "", p.strip(), flags=re.I)
            p = re.sub(r"^(картинки|картинку|фото|изображения)\s+", "", p, flags=re.I)
            p = " ".join(p.split()).strip(" .,;:")
            if len(p) >= 2:
                out.append(p)
        return out or [raw]

    @staticmethod
    def _normalize_query(text: str) -> str:
        t = (text or "").strip()
        fluff = [
            r"^\s*(пожалуйста\s*[,:]?\s*)",
            r"^\s*(можешь|можете)\s+",
            r"^\s*(найди|найти|поищи|поискать|погугли|загугли|поиск)\s+",
            r"\b(в\s+интернете|в\s+гугле|в\s+google|в\s+сети|онлайн)\b",
            r"^\s*(мне|для\s+меня)\s+",
            r"^\s*(фотографию|фотография|фото|картинку|картинки|изображения|видео)\s+(по|про|с)?\s*",
        ]
        prev = None
        while prev != t:
            prev = t
            for p in fluff:
                t = re.sub(p, " ", t, flags=re.I)
            t = " ".join(t.split())
        return t.strip(" .,!?:;—-")

    def _similar_query(self, app: AppContext, kind: str) -> str:
        val = str(app.state.get("last_search_query") or "").strip()
        if not val:
            raw = str(app.state.get("screen_vision_search_query") or "").strip()
            junk = ("день", "вечер", "утро", "лови", "ищу", "жми")
            if raw and not any(j in raw.lower() for j in junk):
                val = raw
        words = []
        stop = {"монитор", "экран", "chrome", "google", "вижу", "хозяин", "окно"}
        for w in re.findall(r"[A-Za-zА-Яа-яЁё0-9]{3,}", val):
            lw = w.lower()
            if lw not in stop and lw not in words:
                words.append(w)
            if len(words) >= 8:
                break
        return " ".join(words)

    @staticmethod
    def _guess_mode(q: str) -> str:
        low = (q or "").lower()
        if any(w in low for w in ("картин", "фото", "изображ", "image", "обои")):
            return "images"
        if any(w in low for w in ("видео", "youtube", "ютуб")):
            return "video"
        return "web"

    def _url(self, query: str, mode: str, app: AppContext) -> str:
        engine = str(app.get_plugin_setting(self.id, "search_engine", "google") or "google").lower()
        enc = quote_plus(query)
        if mode == "images":
            if engine == "yandex":
                return f"https://yandex.ru/images/search?text={enc}"
            if engine == "bing":
                return f"https://www.bing.com/images/search?q={enc}"
            if engine == "duckduckgo":
                return f"https://duckduckgo.com/?q={enc}&iax=images&ia=images"
            return f"https://www.google.com/search?tbm=isch&q={enc}"
        if mode == "video":
            return f"https://www.youtube.com/results?search_query={enc}"
        if engine == "yandex":
            return f"https://yandex.ru/search/?text={enc}"
        if engine == "bing":
            return f"https://www.bing.com/search?q={enc}"
        if engine == "duckduckgo":
            return f"https://duckduckgo.com/?q={enc}"
        return f"https://www.google.com/search?q={enc}"

    def _browser_exe(self, app: AppContext) -> Optional[str]:
        import os
        name = str(app.get_plugin_setting(self.id, "browser", "default") or "default").lower()
        if name in ("", "default", "system", "embed"):
            return None
        local = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        candidates = {
            "chrome": [
                rf"{pf}\Google\Chrome\Application\chrome.exe",
                rf"{local}\Google\Chrome\Application\chrome.exe",
            ],
            "edge": [rf"{pf}\Microsoft\Edge\Application\msedge.exe"],
            "firefox": [rf"{pf}\Mozilla Firefox\firefox.exe"],
            "opera": [rf"{local}\Programs\Opera\opera.exe"],
            "brave": [rf"{local}\BraveSoftware\Brave-Browser\Application\brave.exe"],
        }
        for p in candidates.get(name, []):
            if os.path.isfile(p):
                return p
        return None

    def _open(self, url: str, app: AppContext) -> None:
        exe = self._browser_exe(app)
        if exe:
            subprocess.Popen([exe, url], shell=False)
            return
        try:
            if webbrowser.open(url):
                return
        except Exception as e:
            print(f"browser_search: webbrowser: {e}", flush=True)
        subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)

    def _emotion(self, app: AppContext, q: str) -> None:
        pl = app.plugins.get("persona") or app.plugins.get("emotion")
        if pl and hasattr(pl, "set_context"):
            try:
                pl.set_context(app, "searching", "web_search")
            except Exception:
                pass


def register():
    return PluginImpl()
