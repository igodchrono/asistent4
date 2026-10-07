# -*- coding: utf-8 -*-
"""Заметки: tools + UI список/добавить/удалить."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.plugin_api import AppContext, HookResult, Plugin, SettingField


class PluginImpl(Plugin):
    id = "notes"
    name = "Заметки"
    version = "2.1.0"
    settings_tab = "own"
    settings_tab_title = "Заметки"
    settings_schema = [SettingField("enabled", "Включить", "bool", True)]

    def __init__(self) -> None:
        self._ui: Dict[str, Any] = {}
        self.app = None

    def _path(self, app: AppContext) -> Path:
        root = Path(getattr(app.config, "DATA_DIR", Path("data")))
        cid = getattr(app.config, "ACTIVE_CHARACTER", "default") or "default"
        d = root / "personas" / "characters" / str(cid)
        d.mkdir(parents=True, exist_ok=True)
        p = d / "notes.md"
        if not p.exists():
            p.write_text("# Заметки\n\n", encoding="utf-8")
        return p

    def register_tools(self, app: AppContext) -> None:
        app.tools["note_add"] = self.tool_add
        app.tools["note_list"] = self.tool_list
        app.tools["note_find"] = self.tool_find
        app.tools["note_delete"] = self.tool_delete

    def on_user_message(self, text, app):
        if not app.get_plugin_setting(self.id, "enabled", True):
            return None
        low = (text or "").strip().lower()
        if low.startswith("запиши:") or low.startswith("заметка:") or low.startswith("запиши заметк"):
            body = text.split(":", 1)[-1].strip() if ":" in text else text.split(" ", 1)[-1]
            return HookResult(True, self.tool_add(app, text=body))
        if low in ("покажи заметки", "заметки", "покажи заметки."):
            return HookResult(True, self.tool_list(app))
        if low.startswith("найди в заметках"):
            q = text.split(" ", 3)[-1] if " " in text else ""
            return HookResult(True, self.tool_find(app, text=q))
        return None

    def tool_add(self, app: AppContext, text: str = "", **kw) -> str:
        text = (text or kw.get("query") or "").strip()
        if not text:
            return "Пустая заметка."
        p = self._path(app)
        line = f"- [{datetime.now():%Y-%m-%d %H:%M}] {text}\n"
        with p.open("a", encoding="utf-8") as f:
            f.write(line)
        self._refresh_ui(app)
        return f"Заметка сохранена: {text}"

    def tool_list(self, app: AppContext, **kw) -> str:
        p = self._path(app)
        body = p.read_text(encoding="utf-8", errors="replace")
        return "Заметки:\n" + body[-3000:]

    def tool_find(self, app: AppContext, text: str = "", **kw) -> str:
        q = (text or kw.get("query") or "").lower()
        p = self._path(app)
        hits = [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if q and q in ln.lower()]
        return "Найдено:\n" + ("\n".join(hits[:30]) if hits else "ничего")

    def tool_delete(self, app: AppContext, text: str = "", **kw) -> str:
        """Удалить строки, содержащие text (или точное совпадение строки)."""
        key = (text or kw.get("query") or "").strip()
        if not key:
            return "Укажи текст заметки для удаления."
        p = self._path(app)
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines(True)
        keep, n = [], 0
        for ln in lines:
            if key in ln and ln.strip().startswith("-"):
                n += 1
                continue
            keep.append(ln)
        p.write_text("".join(keep), encoding="utf-8")
        self._refresh_ui(app)
        return f"Удалено заметок: {n}"

    def on_character_changed(self, character_id, previous_id, app):
        self._refresh_ui(app)

    def setup_settings_tab(self, tab, app: AppContext) -> bool:
        try:
            from PyQt5 import QtWidgets, QtCore
        except ImportError:
            return False
        self.app = app
        if tab.layout() is None:
            layout = QtWidgets.QVBoxLayout(tab)
        else:
            layout = tab.layout()
            while layout.count():
                it = layout.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()

        cid = getattr(app.config, "ACTIVE_CHARACTER", "?")
        layout.addWidget(QtWidgets.QLabel(f"<b>Заметки</b> персонажа <code>{cid}</code>"))
        lst = QtWidgets.QListWidget()
        lst.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._ui["list"] = lst
        layout.addWidget(lst, 1)
        row = QtWidgets.QHBoxLayout()
        b1 = QtWidgets.QPushButton("Обновить")
        b2 = QtWidgets.QPushButton("Удалить выбранные")
        b3 = QtWidgets.QPushButton("Добавить…")
        row.addWidget(b1); row.addWidget(b2); row.addWidget(b3)
        layout.addLayout(row)

        def refresh():
            self._refresh_ui(app)

        def delete_sel():
            # сначала снять тексты, потом удалять из файла (не трогать items в цикле)
            keys = []
            for it in list(lst.selectedItems()):
                try:
                    key = it.data(QtCore.Qt.UserRole)
                except RuntimeError:
                    key = None
                if not key:
                    try:
                        key = it.text()
                    except RuntimeError:
                        continue
                if key and str(key).strip().startswith("-"):
                    keys.append(str(key).strip())
            n = 0
            p = self._path(app)
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines(True)
            keep = []
            for ln in lines:
                s = ln.strip()
                if s in keys or any(s == k or (k[1:].strip() and k[1:].strip() in s) for k in keys):
                    if s.startswith("-"):
                        n += 1
                        continue
                keep.append(ln)
            p.write_text("".join(keep), encoding="utf-8")
            refresh()
            QtWidgets.QMessageBox.information(tab, "Заметки", f"Удалено: {n}")

        def add():
            text, ok = QtWidgets.QInputDialog.getText(tab, "Заметки", "Текст:")
            if ok and text.strip():
                self.tool_add(app, text=text.strip())
                refresh()

        b1.clicked.connect(refresh)
        b2.clicked.connect(delete_sel)
        b3.clicked.connect(add)
        refresh()
        return True

    def collect_settings_tab(self) -> Dict[str, Any]:
        return {}

    def _refresh_ui(self, app: Optional[AppContext] = None) -> None:
        def go():
            self._refresh_ui_now(app)
        win = None
        try:
            a = app or self.app
            win = getattr(a, "window", None) if a else None
        except Exception:
            win = None
        if win is not None and hasattr(win, "post"):
            win.post(go)
        else:
            go()

    def _refresh_ui_now(self, app: Optional[AppContext] = None) -> None:
        lst = self._ui.get("list")
        if lst is None:
            return
        app = app or self.app
        if app is None:
            return
        try:
            from PyQt5 import QtCore, QtWidgets
        except ImportError:
            return
        lst.clear()
        p = self._path(app)
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = ln.strip()
            if s.startswith("-"):
                it = QtWidgets.QListWidgetItem(s)
                it.setData(QtCore.Qt.UserRole, s)
                lst.addItem(it)
        if lst.count() == 0:
            lst.addItem("(пусто)")


def register():
    return PluginImpl()
