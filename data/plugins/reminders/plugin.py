# -*- coding: utf-8 -*-
"""Напоминания: SQLite + UI список/добавить/удалить."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from core.plugin_api import AppContext, HookResult, Plugin, SettingField


class PluginImpl(Plugin):
    id = "reminders"
    name = "Напоминания"
    version = "2.3.0"
    settings_tab = "own"
    settings_tab_title = "Напоминания"
    settings_schema = [SettingField("enabled", "Включить", "bool", True)]

    def __init__(self) -> None:
        self._ui: Dict[str, Any] = {}
        self.app = None
        self._timer = None
        self._fired: set = set()

    def on_load(self, app: AppContext) -> None:
        self.app = app
        self._db(app)
        try:
            from PyQt5 import QtCore
            self._timer = QtCore.QTimer()
            self._timer.timeout.connect(lambda: self._tick(app))
            self._timer.start(30 * 1000)
        except Exception as e:
            print(f"reminders: timer {e}", flush=True)
        print("⏰ reminders: due-check 30s", flush=True)

    def on_shutdown(self, app: AppContext) -> None:
        if self._timer is not None:
            try:
                self._timer.stop()
            except Exception:
                pass
            self._timer = None

    def on_user_message(self, text, app):
        if not app.get_plugin_setting(self.id, "enabled", True):
            return None
        low = (text or "").strip().lower()
        if low.startswith("напомни"):
            import re
            if re.match(r"^напомни(шь)?\s+(как|кто|почему|зачем|что\s+я|что\s+ты|мне\s+как|мне\s+что)\b", low):
                return None
            return HookResult(True, self.tool_add(app, text=text))
        if low in ("напоминания", "список напоминаний", "покажи напоминания"):
            return HookResult(True, self.tool_list(app))
        return None

    def register_tools(self, app: AppContext) -> None:
        app.tools["reminder_add"] = self.tool_add
        app.tools["reminder_list"] = self.tool_list
        app.tools["reminder_delete"] = self.tool_delete

    def _db(self, app: AppContext) -> Path:
        root = Path(getattr(app.config, "DATA_DIR", Path("data")))
        p = root / "reminders.db"
        con = sqlite3.connect(str(p))
        con.execute(
            "CREATE TABLE IF NOT EXISTS reminders ("
            "id INTEGER PRIMARY KEY, text TEXT, due TEXT, "
            "done INTEGER DEFAULT 0, created_at REAL, trigger_at REAL)"
        )
        cols = {r[1] for r in con.execute("PRAGMA table_info(reminders)").fetchall()}
        for col, typ in (
            ("due", "TEXT"), ("done", "INTEGER"), ("created_at", "REAL"),
            ("trigger_at", "REAL"), ("text", "TEXT"),
        ):
            if col not in cols:
                try:
                    con.execute(f"ALTER TABLE reminders ADD COLUMN {col} {typ}")
                except Exception:
                    pass
        con.commit()
        con.close()
        return p

    def tool_add(self, app: AppContext, text: str = "", **kw) -> str:
        text = (text or kw.get("query") or "").strip() or "напоминание"
        import re
        text = re.sub(r"^\s*напомни(ть)?\s*", "", text, flags=re.I).strip() or text
        due_dt, label = self._parse_when(text)
        now = time.time()
        due = due_dt.isoformat(timespec="minutes")
        trigger = due_dt.timestamp()
        p = self._db(app)
        con = sqlite3.connect(str(p))
        try:
            con.execute(
                "INSERT INTO reminders(text, due, done, created_at, trigger_at) VALUES(?,?,0,?,?)",
                (text, due, now, trigger),
            )
            con.commit()
        except Exception as e:
            con.close()
            return f"Не удалось создать напоминание: {e}"
        con.close()
        self._refresh_ui(app)
        return f"Напоминание на {label}: {text}"

    @staticmethod
    def _parse_when(text: str):
        """через 10 минут / 2 часа / завтра / в 18:00; иначе +1 час."""
        import re
        now = datetime.now()
        t = (text or "").lower()
        m = re.search(r"через\s+(\d+)\s*(минут|мин)\b", t)
        if m:
            n = int(m.group(1))
            dt = now + timedelta(minutes=max(1, n))
            return dt, dt.strftime("%H:%M")
        m = re.search(r"через\s+(\d+)\s*(час|часа|часов)\b", t)
        if m:
            n = int(m.group(1))
            dt = now + timedelta(hours=max(1, n))
            return dt, dt.strftime("%d.%m %H:%M")
        if re.search(r"через\s+час\b", t):
            dt = now + timedelta(hours=1)
            return dt, dt.strftime("%H:%M")
        if "завтра" in t:
            dt = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
            return dt, dt.strftime("%d.%m %H:%M")
        m = re.search(r"\bв\s+(\d{1,2})[:.](\d{2})\b", t)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            if 0 <= h <= 23 and 0 <= mi <= 59:
                dt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
                if dt <= now:
                    dt += timedelta(days=1)
                return dt, dt.strftime("%d.%m %H:%M")
        dt = now + timedelta(hours=1)
        return dt, "~1ч (" + dt.strftime("%H:%M") + ")"

    def _tick(self, app: AppContext) -> None:
        p = self._db(app)
        now = time.time()
        con = sqlite3.connect(str(p))
        try:
            rows = con.execute(
                "SELECT id, text, IFNULL(due,'') FROM reminders "
                "WHERE IFNULL(done,0)=0 AND IFNULL(trigger_at,0)>0 AND trigger_at<=? "
                "ORDER BY trigger_at ASC LIMIT 5",
                (now,),
            ).fetchall()
            for rid, text, due in rows:
                if rid in self._fired:
                    continue
                self._fired.add(rid)
                con.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
                self._announce(app, rid, text, due)
            con.commit()
        except Exception as e:
            print(f"reminders tick: {e}", flush=True)
        finally:
            con.close()
        self._refresh_ui(app)

    def _announce(self, app: AppContext, rid: int, text: str, due: str) -> None:
        msg = f"Напоминание: {text}"
        win = getattr(app, "window", None) or app.state.get("gui")
        if win is not None and hasattr(win, "publish_assistant_message"):
            try:
                win.publish_assistant_message(msg)
                return
            except Exception:
                pass
        print(f"⏰ {msg} (до {due})", flush=True)

    def tool_list(self, app: AppContext, **kw) -> str:
        rows = self._rows(app)
        if not rows:
            return "Активные напоминания:\nнет"
        return "Активные напоминания:\n" + "\n".join(f"#{i} {t} (до {d})" for i, t, d in rows)

    def tool_delete(self, app: AppContext, text: str = "", id: int = 0, **kw) -> str:
        p = self._db(app)
        con = sqlite3.connect(str(p))
        n = 0
        try:
            if id:
                con.execute("DELETE FROM reminders WHERE id=?", (int(id),))
                n = con.total_changes
            elif text:
                con.execute("DELETE FROM reminders WHERE text LIKE ?", (f"%{text}%",))
                n = con.total_changes
            con.commit()
        except Exception as e:
            con.close()
            return f"Ошибка удаления: {e}"
        con.close()
        self._refresh_ui(app)
        return f"Удалено напоминаний: {n}"

    def _rows(self, app: AppContext):
        p = self._db(app)
        con = sqlite3.connect(str(p))
        try:
            rows = con.execute(
                "SELECT id, text, IFNULL(due,'') FROM reminders WHERE IFNULL(done,0)=0 ORDER BY id DESC LIMIT 50"
            ).fetchall()
        except Exception:
            rows = []
        con.close()
        return rows

    def setup_settings_tab(self, tab, app: AppContext) -> bool:
        try:
            from PyQt5 import QtWidgets, QtCore
        except ImportError:
            return False
        self.app = app
        layout = tab.layout() or QtWidgets.QVBoxLayout(tab)
        while layout.count():
            it = layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        layout.addWidget(QtWidgets.QLabel("<b>Напоминания</b>"))
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
            ids = []
            for it in list(lst.selectedItems()):
                try:
                    mid = it.data(QtCore.Qt.UserRole)
                except RuntimeError:
                    continue
                if mid is not None:
                    ids.append(int(mid))
            n = 0
            for mid in ids:
                self.tool_delete(app, id=mid)
                n += 1
            refresh()
            QtWidgets.QMessageBox.information(tab, "Напоминания", f"Удалено: {n}")

        def add():
            text, ok = QtWidgets.QInputDialog.getText(tab, "Напоминания", "Текст (через ~1ч):")
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
        rows = self._rows(app)
        if not rows:
            lst.addItem("(пусто)")
            return
        for i, t, d in rows:
            it = QtWidgets.QListWidgetItem(f"#{i}  {t}  (до {d})")
            it.setData(QtCore.Qt.UserRole, i)
            lst.addItem(it)


def register():
    return PluginImpl()
