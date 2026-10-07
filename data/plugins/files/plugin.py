# -*- coding: utf-8 -*-
"""Загрузки текста: ingest вложений, правка LLM, ссылка file://."""
from __future__ import annotations

import json
import re
import shutil
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.plugin_api import AppContext, HookResult, Plugin, SettingField

_FENCE = re.compile(r"```([a-zA-Z0-9_+-]*)\r?\n(.*?)```", re.S)
_SAVE_PHRASES = (
    "скинь файлом", "дай файлом", "выдай файлом", "отправь файлом", "в виде файла",
    "сохрани в файл", "сохрани как файл", "сохрани файл", "сохрани это", "сохрани код",
    "сохрани воркфлоу", "создай файл", "создать файл", "собери файл", "сделай файл",
    "запиши в файл", "положи в файл", "создай воркфлоу", "собери воркфлоу",
    "сделай воркфлоу", "сохрани json",
)
_DOC_EXT = {".docx", ".pdf"}
_TEXT_EXT = {".txt", ".md", ".json", ".csv", ".log", ".py", ".ini", ".yaml", ".yml", ".xml", ".html", ".css", ".js"}
_OK_EXT = _TEXT_EXT | _DOC_EXT


class PluginImpl(Plugin):
    id = "files"
    name = "Файлы"
    version = "1.0.0"
    description = "Загруженные тексты: список, правка, ссылка"
    settings_schema = [
        SettingField("enabled", "Включить", "bool", True),
        SettingField("max_chars", "Макс. символов на правку", "int", 20000, min_value=1000, max_value=80000),
    ]

    def on_user_message(self, text, app):
        if not app.get_plugin_setting(self.id, "enabled", True):
            return None
        if not self._wants_saved_file(text or ""):
            return None
        name = self._name_from_request(text)
        blocks = self._history_blocks(app)
        body, lang = self._pick_block(blocks, name)
        if not body:
            body = self._assemble(app, text)
            lang = ""
        if not body:
            return HookResult(True, "не из чего собрать файл: в чате нет кода, и модель ничего не вернула.")
        if not name:
            name = self._name_for(lang, text)
        saved = self.tool_send(app, name=name, content=body)
        return HookResult(True, saved)

    @staticmethod
    def _wants_saved_file(text: str) -> bool:
        low = (text or "").lower()
        if any(w in low for w in ("картин", "фото", "изображен")) and not any(
            w in low for w in ("файл", "воркфлоу", "код", "json")
        ):
            return False
        if any(p in low for p in _SAVE_PHRASES):
            return True
        return bool(re.search(r"сохрани\s+\S+\.[a-z0-9]{1,8}", low))

    @staticmethod
    def _name_from_request(text: str) -> str:
        m = re.search(r"([A-Za-z0-9_\-]+\.[A-Za-z0-9]{1,8})", text or "")
        return m.group(1) if m else ""

    def _history_blocks(self, app: AppContext):
        gui = getattr(app, "window", None) or app.state.get("gui")
        engine = getattr(gui, "engine", None) if gui is not None else None
        history = list(getattr(engine, "history", []) or [])
        found = []
        for msg in history[:-1]:
            if not isinstance(msg, dict):
                continue
            for lang, body in _FENCE.findall(str(msg.get("content") or "")):
                body = body.strip("\n")
                if body.strip():
                    found.append((lang.lower(), body))
        return found

    @staticmethod
    def _pick_block(blocks, name: str):
        if not blocks:
            return "", ""
        ext = Path(name).suffix.lower().lstrip(".") if name else ""
        aliases = {"py": "python", "js": "javascript", "md": "markdown", "sh": "bash"}
        want = {ext, aliases.get(ext, "")} - {""}
        if want:
            for lang, body in reversed(blocks):
                if lang in want:
                    return body, lang
        lang, body = max(blocks, key=lambda item: len(item[1]))
        return body, lang

    def _name_for(self, lang: str, text: str) -> str:
        low = (text or "").lower()
        ext = {
            "python": ".py", "py": ".py", "javascript": ".js", "js": ".js",
            "json": ".json", "html": ".html", "css": ".css", "markdown": ".md",
        }.get(lang or "", "")
        if not ext:
            if "json" in low or "воркфлоу" in low or "workflow" in low:
                ext = ".json"
            elif "python" in low or ".py" in low:
                ext = ".py"
            else:
                ext = ".txt"
        stem = "workflow" if ext == ".json" else "note"
        return stem + ext

    def _assemble(self, app: AppContext, request: str) -> str:
        gui = getattr(app, "window", None) or app.state.get("gui")
        engine = getattr(gui, "engine", None) if gui is not None else None
        history = list(getattr(engine, "history", []) or [])
        lines = []
        for msg in history[-12:]:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role") or "user"
            body = str(msg.get("content") or "").strip()
            if body:
                lines.append(f"{role}: {body[:1500]}")
        transcript = "\n".join(lines)[-7000:]
        raw = self._llm_rewrite(
            app,
            "Собери один готовый файл по просьбе и контексту. Верни только содержимое файла.",
            f"Просьба:\n{request}\n\nЧат:\n{transcript}",
        )
        m = _FENCE.search(raw or "")
        if m:
            raw = m.group(2)
        return (raw or "").strip()

    def on_load(self, app: AppContext) -> None:
        app.state.setdefault("uploads", [])
        app.state.setdefault("last_upload_id", "")
        print("📄 files 1.0: data/uploads", flush=True)

    def register_tools(self, app: AppContext) -> None:
        app.tools["read_uploaded"] = self.tool_read
        app.tools["edit_uploaded"] = self.tool_edit
        app.tools["list_uploads"] = self.tool_list
        app.tools["get_file_link"] = self.tool_link
        app.tools["send_file"] = self.tool_send

    def on_before_llm(self, messages: List[Dict[str, Any]], app: AppContext) -> List[Dict[str, Any]]:
        self._ingest(app)
        return messages

    def _root(self, app: AppContext) -> Path:
        data = Path(getattr(app.config, "DATA_DIR", Path(".")))
        user = str(getattr(app.config, "ACTIVE_USER", "") or "default").strip() or "default"
        p = data / "uploads" / user
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _ingest(self, app: AppContext) -> None:
        files = list(app.state.get("pending_attachments") or []) + list(app.state.get("last_attachments") or [])
        known = {str(x.get("path")) for x in (app.state.get("uploads") or []) if isinstance(x, dict)}
        for raw in files:
            p = Path(str(raw))
            if not p.is_file() or p.suffix.lower() not in _OK_EXT:
                continue
            if str(p.resolve()) in known:
                rec = next(x for x in app.state["uploads"] if str(Path(x["path"]).resolve()) == str(p.resolve()))
                app.state["last_upload_id"] = rec["id"]
                continue
            dest = self._root(app) / f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}_{p.name}"
            if dest.resolve() != p.resolve():
                try:
                    shutil.copy2(p, dest)
                except Exception:
                    dest = p
            rec = {
                "id": dest.name,
                "name": p.name,
                "path": str(dest),
                "size": dest.stat().st_size if dest.exists() else 0,
            }
            ups = list(app.state.get("uploads") or [])
            ups.append(rec)
            app.state["uploads"] = ups[-40:]
            app.state["last_upload_id"] = rec["id"]
            known.add(str(dest.resolve()))

    def _find(self, app: AppContext, file_id: str = "") -> Optional[Dict[str, Any]]:
        fid = (file_id or app.state.get("last_upload_id") or "").strip()
        for rec in reversed(list(app.state.get("uploads") or [])):
            if not isinstance(rec, dict):
                continue
            if not fid or rec.get("id") == fid or rec.get("name") == fid:
                return rec
        if fid:
            p = self._root(app) / fid
            if p.is_file():
                return {"id": p.name, "name": p.name, "path": str(p), "size": p.stat().st_size}
        return None

    def _read_text(self, path: Path, limit: int) -> str:
        ext = path.suffix.lower()
        if ext in _TEXT_EXT:
            return path.read_text(encoding="utf-8", errors="replace")[:limit]
        if ext == ".docx":
            try:
                import zipfile
                import xml.etree.ElementTree as ET
                with zipfile.ZipFile(path) as z:
                    xml = z.read("word/document.xml")
                root = ET.fromstring(xml)
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                parts = [t.text or "" for t in root.findall(".//w:t", ns)]
                return "".join(parts)[:limit]
            except Exception as e:
                return f"[docx не прочитан: {e}]"
        if ext == ".pdf":
            try:
                import pypdf
                r = pypdf.PdfReader(str(path))
                return "\n".join((p.extract_text() or "") for p in r.pages)[:limit]
            except Exception:
                try:
                    import PyPDF2
                    r = PyPDF2.PdfReader(str(path))
                    return "\n".join((p.extract_text() or "") for p in r.pages)[:limit]
                except Exception as e:
                    return f"[pdf не прочитан: {e}]"
        return f"[формат {ext} не поддерживается]"

    def tool_read(self, app: AppContext, file_id: str = "", target: str = "", **kw) -> str:
        self._ingest(app)
        rec = self._find(app, file_id or target or kw.get("name") or "")
        if not rec:
            return "Нет загруженного файла. Прикрепи txt/md/docx/pdf кнопкой вложения."
        limit = int(app.get_plugin_setting(self.id, "max_chars", 20000) or 20000)
        text = self._read_text(Path(rec["path"]), limit)
        return f"{rec['name']} ({rec.get('size') or 0} байт)\n\n{text}"

    def tool_list(self, app: AppContext, **kw) -> str:
        self._ingest(app)
        ups = list(app.state.get("uploads") or [])
        if not ups:
            root = self._root(app)
            for p in sorted(root.glob("*")):
                if p.is_file():
                    ups.append({"id": p.name, "name": p.name, "path": str(p), "size": p.stat().st_size})
            app.state["uploads"] = ups
        if not ups:
            return "Загрузок нет."
        lines = []
        for rec in ups[-20:]:
            lines.append(f"- {rec.get('id')}  ({rec.get('size') or 0} байт)")
        last = app.state.get("last_upload_id") or ""
        if last:
            lines.append(f"последний: {last}")
        return "Загрузки:\n" + "\n".join(lines)

    def tool_link(self, app: AppContext, file_id: str = "", name: str = "", **kw) -> str:
        rec = self._find(app, file_id or name or "")
        if not rec:
            return "Файл не найден."
        return f"{rec.get('name')}: [файл: {rec['path']}]"

    def tool_send(self, app: AppContext, name: str = "", content: str = "", text: str = "", **kw) -> str:
        raw = (content or text or kw.get("query") or "").strip()
        if not raw:
            return "Нечего класть в файл — нет текста."
        fence = None
        lang = ""
        m = re.search(r"```([a-zA-Z0-9_+-]*)\r?\n?(.*?)```", raw, re.S)
        if m:
            lang = (m.group(1) or "").lower()
            fence = m.group(2)
            raw = fence.strip("\n") if fence is not None else raw
        fname = self._safe_name(name or kw.get("file") or "")
        if not fname:
            ext = {
                "python": ".py", "py": ".py", "js": ".js", "javascript": ".js",
                "ts": ".ts", "html": ".html", "css": ".css", "json": ".json",
                "md": ".md", "bash": ".sh", "sh": ".sh", "cpp": ".cpp", "c": ".c",
            }.get(lang, ".txt")
            fname = f"note_{datetime.now().strftime('%H%M%S')}{ext}"
        dest = self._root(app) / f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}_{fname}"
        dest.write_text(raw, encoding="utf-8")
        rec = {
            "id": dest.name,
            "name": fname,
            "path": str(dest),
            "size": dest.stat().st_size,
        }
        ups = list(app.state.get("uploads") or [])
        ups.append(rec)
        app.state["uploads"] = ups[-40:]
        app.state["last_upload_id"] = rec["id"]
        return f"Файл в чате: {fname} ({rec['size']} байт)\n[файл: {dest}]"

    @staticmethod
    def _safe_name(name: str) -> str:
        n = Path(str(name or "").replace("\\", "/")).name.strip()
        n = "".join(ch for ch in n if ch.isalnum() or ch in "._- ").strip(" .")
        return n[:80]

    def tool_edit(self, app: AppContext, instruction: str = "", target: str = "last_upload", file_id: str = "", **kw) -> str:
        self._ingest(app)
        rec = self._find(app, file_id or ("" if target == "last_upload" else target))
        if not rec:
            return "Нет загруженного текста. Сначала прикрепи файл."
        instruction = (instruction or kw.get("text") or kw.get("query") or "").strip()
        if not instruction:
            return "Напиши, что сделать с текстом (перепиши / сократи / исправь)."
        limit = int(app.get_plugin_setting(self.id, "max_chars", 20000) or 20000)
        src = self._read_text(Path(rec["path"]), limit)
        if src.startswith("[") and "не прочитан" in src:
            return src
        new = self._llm_rewrite(app, instruction, src)
        if not new:
            return "LLM не вернула текст. Проверь LM Studio."
        dest = self._root(app) / f"edited_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}_{Path(rec['name']).name}"
        if dest.suffix.lower() not in _TEXT_EXT:
            dest = dest.with_suffix(".txt")
        dest.write_text(new, encoding="utf-8")
        out = {
            "id": dest.name,
            "name": dest.name,
            "path": str(dest),
            "size": dest.stat().st_size,
        }
        ups = list(app.state.get("uploads") or [])
        ups.append(out)
        app.state["uploads"] = ups[-40:]
        app.state["last_upload_id"] = out["id"]
        preview = new[:300] + ("…" if len(new) > 300 else "")
        return (
            f"Готово. Изменённый текст в чате.\n"
            f"Превью:\n---\n{preview}\n---\n"
            f"[файл: {dest}]"
        )

    def _llm_rewrite(self, app: AppContext, instruction: str, text: str) -> str:
        try:
            from core.llm_client import normalize_api_url
            api = normalize_api_url(str(getattr(app.config, "API_URL", "http://127.0.0.1:1234/v1")))
        except Exception:
            api = "http://127.0.0.1:1234/v1"
        key = str(getattr(app.config, "API_KEY", "lm-studio") or "lm-studio")
        model = str(getattr(app.config, "MODEL_NAME", "local-model") or "local-model")
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "Ты редактор текста. Верни только изменённый текст, без преамбулы и кавычек.",
                },
                {"role": "user", "content": f"Задание: {instruction}\n\nТекст:\n{text}"},
            ],
            "temperature": 0.3,
            "max_tokens": 4000,
        }
        req = urllib.request.Request(
            api + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            raw = urllib.request.urlopen(req, timeout=180).read().decode("utf-8")
            data = json.loads(raw)
            return str((((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or "").strip()
        except Exception as e:
            print(f"files llm: {e}", flush=True)
            return ""
