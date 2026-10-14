# -*- coding: utf-8 -*-
"""Ранжирование уже собранных ссылок и картинок. Сеть остаётся в плагине."""
from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from core.query_parser import ParsedQuery
from core.search_settings import load as load_search_settings


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-zA-Zа-яА-Я0-9]{3,}", (text or "").lower())}


def title_similarity(subject: str, title: str) -> float:
    a, b = _tokens(subject), _tokens(title)
    if not a or not b:
        return 0.2
    return len(a & b) / len(a | b)


def url_relevance(subject: str, url: str) -> float:
    host_path = (url or "").lower()
    hits = 0
    words = _tokens(subject)
    if not words:
        return 0.3
    for w in words:
        if w in host_path:
            hits += 1
    return min(1.0, hits / max(1, len(words)))


def modifier_match(modifiers: List[str], text: str) -> float:
    if not modifiers:
        return 0.5
    low = (text or "").lower()
    got = sum(1 for m in modifiers if m.split()[0][:5] in low)
    return got / len(modifiers)


def filter_match(filters: Dict[str, str], item: Dict[str, Any]) -> float:
    if not filters:
        return 0.5
    blob = " ".join(str(item.get(k) or "") for k in ("title", "url", "description", "extract")).lower()
    ok = 0
    for key, val in filters.items():
        if key == "source" and val and val.split(".")[0] in (item.get("url") or ""):
            ok += 1
        elif key == "format" and val and val in blob:
            ok += 1
        elif key == "date" and val and (val in blob or val == "recent"):
            ok += 1
        elif key == "color" and val and val in blob:
            ok += 1
        elif key == "size" and val and (val in blob or item.get("width")):
            ok += 1
        elif key == "language":
            ok += 0.5
    return ok / max(1, len(filters))


def source_quality(url: str, table: Dict[str, float]) -> float:
    host = urlparse(url or "").netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    for domain, score in table.items():
        if host == domain or host.endswith("." + domain):
            return float(score)
    return 0.55


def resolution_score(item: Dict[str, Any]) -> float:
    try:
        w = int(item.get("width") or 0)
        h = int(item.get("height") or 0)
    except (TypeError, ValueError):
        w = h = 0
    if w <= 0 or h <= 0:
        return 0.45
    return max(0.1, min(1.0, (w * h) / float(1920 * 1080)))


def recency_score(item: Dict[str, Any]) -> float:
    blob = " ".join(str(item.get(k) or "") for k in ("title", "description", "date"))
    year = re.search(r"20[2-3]\d", blob)
    if not year:
        return 0.4
    return 0.7 if int(year.group(0)) >= 2024 else 0.5


class SmartSearch:
    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        self.settings = settings or load_search_settings()

    def collect(self, calls: List[Callable[[], List[Dict[str, Any]]]], timeout: Optional[float] = None) -> List[Dict[str, Any]]:
        if not calls:
            return []
        search = self.settings.get("search") or {}
        timeout = float(timeout if timeout is not None else search.get("timeout_seconds") or 8)
        if not search.get("parallel", True) or len(calls) == 1:
            out: List[Dict[str, Any]] = []
            for fn in calls:
                try:
                    out.extend(fn() or [])
                except Exception as e:
                    print(f"smart_search: engine {e}", flush=True)
            return self._dedupe(out)
        out = []
        with ThreadPoolExecutor(max_workers=min(4, len(calls))) as pool:
            futures = [pool.submit(fn) for fn in calls]
            done, pending = wait(futures, timeout=timeout)
            for fut in pending:
                fut.cancel()
            for fut in done:
                try:
                    out.extend(fut.result() or [])
                except Exception as e:
                    print(f"smart_search: engine {e}", flush=True)
        return self._dedupe(out)

    @staticmethod
    def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        out = []
        for item in items:
            key = str(item.get("image") or item.get("url") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    def rank(self, candidates: List[Dict[str, Any]], query: ParsedQuery) -> List[Dict[str, Any]]:
        search = self.settings.get("search") or {}
        weights = search.get("weights") or {}
        quality = search.get("source_quality") or {}
        scored = []
        for raw in candidates or []:
            item = dict(raw)
            title = str(item.get("title") or "")
            url = str(item.get("url") or item.get("image") or "")
            blob = title + " " + str(item.get("description") or item.get("extract") or "")
            score = (
                float(weights.get("title_match", 0.3)) * title_similarity(query.subject, title)
                + float(weights.get("url_relevance", 0.15)) * url_relevance(query.subject, url)
                + float(weights.get("modifier_match", 0.15)) * modifier_match(query.modifiers, blob)
                + float(weights.get("filter_match", 0.15)) * filter_match(query.filters, item)
                + float(weights.get("source_quality", 0.1)) * source_quality(url, quality)
                + float(weights.get("resolution", 0.1)) * resolution_score(item)
                + float(weights.get("recency", 0.05)) * recency_score(item)
            )
            item["score"] = round(score, 4)
            item["source"] = item.get("source") or _host(url)
            if not item.get("description"):
                item["description"] = str(item.get("extract") or "")
            scored.append(item)
        scored.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
        return scored

    def limit(self, query: ParsedQuery) -> int:
        fmt = self.settings.get("response_format") or {}
        cap = int(fmt.get("max_links") or 5)
        if query.quantity == "many":
            return max(1, min(10, cap))
        if query.quantity == "few":
            return min(3, cap)
        return 1

    def pick(self, ranked: List[Dict[str, Any]], query: ParsedQuery) -> List[Dict[str, Any]]:
        return list(ranked or [])[: self.limit(query)]

    def why(self, item: Dict[str, Any], query: ParsedQuery) -> str:
        bits = []
        if query.subject:
            bits.append(f"ближе всего к «{query.subject}»")
        host = str(item.get("source") or "")
        if host:
            bits.append(f"источник {host}")
        if query.modifiers:
            bits.append(", ".join(query.modifiers))
        return ", ".join(bits) or "самый релевантный из выдачи"

    def format_answer(self, picked: List[Dict[str, Any]], query: ParsedQuery) -> str:
        if not picked:
            return "Ничего подходящего не нашла. Вкладка поиска открыта — можно дать прямую ссылку."
        if query.content_type == "image":
            return self._format_images(picked, query)
        if query.intent == "find_info" and picked[0].get("description"):
            return self._format_info(picked, query)
        return self._format_links(picked, query)

    def _format_images(self, picked: List[Dict[str, Any]], query: ParsedQuery) -> str:
        if len(picked) == 1:
            item = picked[0]
            title = item.get("title") or query.subject or "картинка"
            url = item.get("url") or item.get("image") or ""
            lines = [f"Нашла вот эту: «{title}»"]
            if url:
                lines.append(f"Источник: {item.get('source') or _host(url)} — {url}")
            lines.append("Почему выбрала: " + self.why(item, query))
            return "\n".join(lines)
        lines = [f"Нашла {len(picked)} лучших:"]
        for i, item in enumerate(picked, 1):
            title = item.get("title") or query.subject or "картинка"
            url = item.get("url") or item.get("image") or ""
            lines.append(f"{i}. {title}")
            if url:
                lines.append(f"   {item.get('source') or _host(url)} — {url}")
        return "\n".join(lines)

    def _format_links(self, picked: List[Dict[str, Any]], query: ParsedQuery) -> str:
        blocks = []
        if query.quantity == "one":
            blocks.append(f"Вот что ближе всего к «{query.subject or query.raw}».")
        else:
            blocks.append(f"Вот {len(picked)} лучших по «{query.subject or query.raw}».")
        for item in picked:
            blocks.append(self._one_link(item))
        return "\n\n".join(blocks)

    def _format_info(self, picked: List[Dict[str, Any]], query: ParsedQuery) -> str:
        lead = str(picked[0].get("description") or "").strip()
        lines = []
        if lead:
            lines.append(lead)
            lines.append("")
        lines.append("Источники:")
        for i, item in enumerate(picked, 1):
            title = item.get("title") or "страница"
            url = item.get("url") or ""
            desc = str(item.get("description") or "").strip()
            lines.append(f"{i}. {title}")
            if desc and desc != lead:
                lines.append(f"   {desc}")
            if url:
                lines.append(f"   {url}")
        return "\n".join(lines).strip()

    @staticmethod
    def _one_link(item: Dict[str, Any]) -> str:
        title = str(item.get("title") or "страница").strip()
        desc = str(item.get("description") or item.get("extract") or "").strip()
        if not desc:
            desc = "Описание на странице не нашлось — заголовок и адрес выше, без догадок."
        url = str(item.get("url") or "").strip()
        lines = [title, desc]
        if url:
            lines.append(url)
        return "\n".join(lines)


def _host(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "сайт"


def cache_file(cache_dir: Path, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return cache_dir / f"{digest}.json"


def cache_get(cache_dir: Path, key: str, ttl_hours: float) -> Optional[List[Dict[str, Any]]]:
    path = cache_file(cache_dir, key)
    if not path.is_file():
        return None
    try:
        age = time.time() - path.stat().st_mtime
        if age > float(ttl_hours) * 3600:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, list):
        return data
    return None


def cache_put(cache_dir: Path, key: str, items: List[Dict[str, Any]]) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        slim = []
        for item in items[:20]:
            slim.append({k: v for k, v in item.items() if k in (
                "title", "url", "image", "thumb", "kind", "description", "extract",
                "source", "score", "width", "height",
            )})
        cache_file(cache_dir, key).write_text(
            json.dumps(slim, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"smart_search: cache {e}", flush=True)


def log_perf(data_dir: Path, line: str) -> None:
    try:
        folder = data_dir / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "search_perf.log").open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except Exception:
        pass
