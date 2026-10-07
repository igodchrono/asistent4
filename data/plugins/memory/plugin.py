# -*- coding: utf-8 -*-
"""Memory tools + вкладка настроек: список фактов, удаление."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.plugin_api import AppContext, HookResult, Plugin, SettingField

MemoryStore = None  # type: ignore
try:
    from plugins.memory.store import CharacterMemoryStore as _MS
    MemoryStore = _MS
except Exception:
    try:
        from plugins.memory.store import MemoryStore as _MS
        MemoryStore = _MS
    except Exception:
        try:
            from .store import CharacterMemoryStore as _MS
            MemoryStore = _MS
        except Exception as _e:
            print(f"memory: store import failed: {_e}", flush=True)


class PluginImpl(Plugin):
    id = "memory"
    name = "Память персонажа"
    version = "3.0.0"
    description = "Диалог + дневник + факты (по персонажу, переживает рестарт)."
    settings_tab = "own"
    settings_tab_title = "Память"
    settings_schema = [
        SettingField("enabled", "Включить", "bool", True),
        SettingField("max_inject", "Фактов в prompt", "int", 8, min_value=1, max_value=30),
        SettingField("history_tail", "Реплик в рабочем окне", "int", 40, min_value=12, max_value=80),
        SettingField("diary_days", "Дней дневника в prompt", "int", 10, min_value=1, max_value=30),
    ]

    def __init__(self) -> None:
        self.store = None
        self.app = None
        self._ui = {}  # widgets for settings tab
        self._sum_busy = False

    def on_load(self, app: AppContext) -> None:
        self.app = app
        self._open_store(app)

    def on_character_changed(self, character_id: str, previous_id: str, app: AppContext) -> None:
        if str(character_id or "") == str(previous_id or "") and self.store is not None:
            self._refresh_list_ui()
            return
        self._open_store(app)
        self._refresh_list_ui()

    def _open_store(self, app: AppContext) -> None:
        if MemoryStore is None:
            print("memory: no MemoryStore", flush=True)
            self.store = None
            return
        cid = (
            app.get_active_character()
            if hasattr(app, "get_active_character")
            else getattr(app.config, "ACTIVE_CHARACTER", "default")
        )
        cid = str(cid)
        if self.store is not None and str(getattr(self.store, "character_id", "")) == cid:
            return
        try:
            if self.store is not None:
                try:
                    self.store.close()
                except Exception:
                    pass
                self.store = None
            root = Path(getattr(app.config, "DATA_DIR", Path("data")))
            char_dir = root / "personas" / "characters" / str(cid)
            char_dir.mkdir(parents=True, exist_ok=True)
            self.store = MemoryStore(char_dir, character_id=str(cid))
            n = self.store.count() if hasattr(self.store, "count") else "?"
            nm = self.store.message_count() if hasattr(self.store, "message_count") else "?"
            print(f"🧠 memory: {cid} → {self.store.db_path} (facts={n} msgs={nm})", flush=True)
        except Exception as e:
            print(f"memory: open failed: {e}", flush=True)
            self.store = None

    def _tail_n(self, app: Optional[AppContext] = None) -> int:
        app = app or self.app
        try:
            from core.mode import history_tail
            return history_tail(app, 40)
        except Exception:
            if app is None:
                return 40
            try:
                return int(app.get_plugin_setting(self.id, "history_tail", 40) or 40)
            except (TypeError, ValueError):
                return 40


    def record(self, role: str, content: str) -> None:
        if self.store is None:
            return
        try:
            cid = None
            if self.app is not None:
                raw = self.app.state.get("chat_id")
                if raw is not None and str(raw).isdigit():
                    cid = int(raw)
            self.store.append_message(role, content, chat_id=cid)
        except Exception as e:
            print(f"memory record: {e}", flush=True)

    def hydrate_engine(self, engine) -> List[Dict[str, Any]]:
        if self.store is None or engine is None:
            return []
        try:
            cid = self.store.ensure_current_chat()
            if self.app is not None:
                self.app.state["chat_id"] = cid
            rows = self.store.recent_messages(limit=self._tail_n(), chat_id=cid)
        except Exception as e:
            print(f"memory hydrate: {e}", flush=True)
            return []
        engine.history = [{"role": str(r.get("role") or "user"), "content": str(r.get("content") or "")} for r in rows]
        return rows

    async def maybe_summarize(self, llm) -> None:
        if self._sum_busy or self.store is None or llm is None:
            return
        try:
            groups = self.store.pending_summary_groups(tail=self._tail_n())
        except Exception as e:
            print(f"memory pending: {e}", flush=True)
            return
        if not groups:
            return
        self._sum_busy = True
        try:
            for g in groups:
                await self._summarize_group(llm, g)
        except Exception as e:
            print(f"memory summarize: {e}", flush=True)
        finally:
            self._sum_busy = False

    async def _summarize_group(self, llm, group: Dict[str, Any]) -> None:
        day = str(group.get("day") or "")
        msgs: List[Dict[str, Any]] = list(group.get("messages") or [])
        if not day or not msgs:
            return
        prev = self.store.get_summary(day) if self.store else None
        old = ((prev or {}).get("text") or "").strip()
        lines = []
        for m in msgs[-40:]:
            who = "Пользователь" if m.get("role") == "user" else "Персонаж"
            body = re.sub(r"\s+", " ", str(m.get("content") or "")).strip()[:400]
            if body:
                lines.append(f"{who}: {body}")
        if not lines:
            return
        prompt = (
            "Собери дневник дня для персонажа. Ответь ТОЛЬКО JSON без markdown:\n"
            '{"diary":"5-8 предложений: о чём говорили, имена, договорённости, настроение, открытые темы",'
            '"facts":[{"category":"profile|preference|fact|relation|open_loop","key":"user.name","content":"...","importance":0.8}]}\n'
            "Правила: не выдумывай; facts — только устойчивое о пользователе/мире, не пересказ всего дня; "
            "если новых фактов нет — facts=[]. "
            "diary на русском, от первого лица персонажа («я…»), 4–6 предложений.\n"
        )
        if old:
            prompt += f"\nУже записано про этот день (дополни/сожми, не потеряй важное):\n{old}\n"
        prompt += "\nРеплики:\n" + "\n".join(lines)
        raw = await llm.chat_once(
            [
                {"role": "system", "content": "Ты архивариус памяти персонажа. Только JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=400,
        )
        data: Dict[str, Any] = {}
        cleaned = re.sub(r"```(?:json)?", "", raw or "", flags=re.I).strip()
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, dict):
                    data = parsed
            except Exception:
                data = {}
        diary = str(data.get("diary") or "").strip()
        if not diary:
            diary = (raw or "").strip()[:2000]
        if not diary:
            return
        self.store.upsert_summary(day, diary, int(msgs[0]["id"]), int(msgs[-1]["id"]))
        facts = data.get("facts") if isinstance(data.get("facts"), list) else []
        allowed = {"profile", "preference", "fact", "relation", "open_loop"}
        n = 0
        for f in facts:
            if not isinstance(f, dict) or n >= 5:
                continue
            content = str(f.get("content") or "").strip()
            if len(content) < 3:
                continue
            cat = str(f.get("category") or "fact").strip() or "fact"
            if cat not in allowed:
                cat = "fact"
            key = str(f.get("key") or "").strip() or None
            try:
                imp = float(f.get("importance") or 0.6)
            except Exception:
                imp = 0.6
            self.store.add(content, category=cat, key=key, importance=min(1.0, max(0.3, imp)))
            n += 1
        print(f"memory diary {self.store.character_id} {day}: {len(diary)} chars facts+={n}", flush=True)

    def register_tools(self, app: AppContext) -> None:
        app.tools["memory_add"] = self.tool_add
        app.tools["memory_list"] = self.tool_list
        app.tools["memory_forget"] = self.tool_forget

    # ---- tools ----
    def tool_add(self, app: AppContext, text: str = "", **kwargs) -> str:
        text = (text or kwargs.get("query") or "").strip()
        if not text:
            return "Нечего запоминать — пустой текст."
        if self.store is None:
            self._open_store(app)
        if self.store is None:
            return "Память недоступна."
        try:
            mid = self.store.add(text, category="longterm")
            cid = getattr(app.config, "ACTIVE_CHARACTER", "")
            self._refresh_list_ui()
            return f"Записала в память «{cid}» (#{mid}): {text}"
        except Exception as e:
            self._open_store(app)
            try:
                mid = self.store.add(text, category="longterm")
                self._refresh_list_ui()
                return f"Записала в память (#{mid}): {text}"
            except Exception as e2:
                return f"Не удалось записать: {e2}"

    def tool_list(self, app: AppContext, **kwargs) -> str:
        if self.store is None:
            self._open_store(app)
        if self.store is None:
            return "Память недоступна."
        try:
            items = self.store.list_all(limit=50)
        except Exception:
            self._open_store(app)
            try:
                items = self.store.list_all(limit=50)
            except Exception as e2:
                return f"Ошибка чтения: {e2}"
        if not items:
            return "Долговременная память пока пустая."
        cid = getattr(app.config, "ACTIVE_CHARACTER", "")
        lines = [f"Долговременная память «{cid}»: {len(items)}"]
        for it in items:
            lines.append(f"📌 #{it.get('id')} {it.get('content') or it.get('text') or ''}")
        return "\n".join(lines)

    def tool_forget(self, app: AppContext, text: str = "", **kwargs) -> str:
        text = (text or kwargs.get("query") or "").strip()
        if self.store is None:
            self._open_store(app)
        if self.store is None:
            return "Память недоступна."
        if not text:
            return "Укажи, что забыть."
        try:
            items = self.store.list_all(limit=200)
            deleted = 0
            low = text.lower()
            # "только beta" / id
            only = low.replace("только", "").replace("про", "").strip()
            for it in items:
                content = str(it.get("content") or it.get("text") or "")
                if only and only in content.lower():
                    if self.store.delete(int(it["id"])):
                        deleted += 1
                elif low in content.lower() or content.lower() in low:
                    if self.store.delete(int(it["id"])):
                        deleted += 1
            self._refresh_list_ui()
            return f"Удалила записей: {deleted}." if deleted else "Ничего подходящего не нашла."
        except Exception as e:
            return f"Ошибка удаления: {e}"

    def on_user_message(self, text: str, app: AppContext):
        if not app.get_plugin_setting(self.id, "enabled", True):
            return None
        low = (text or "").strip().lower()
        if low.startswith("запомни:") or low.startswith("запомни "):
            body = text.split(":", 1)[-1].strip() if ":" in text else text.split(" ", 1)[-1]
            return HookResult(True, self.tool_add(app, text=body))
        if "что ты помнишь" in low or low in ("память", "что помнишь"):
            return HookResult(True, self.tool_list(app))
        if low.startswith("забудь"):
            body = text.split(" ", 1)[-1] if " " in text else ""
            body = body.replace("про ", "").strip()
            return HookResult(True, self.tool_forget(app, text=body))
        return None

    def on_before_llm(self, messages: List[Dict[str, Any]], app: AppContext) -> List[Dict[str, Any]]:
        if not app.get_plugin_setting(self.id, "enabled", True):
            return messages
        if self.store is None:
            self._open_store(app)
        if self.store is None:
            return messages
        q = ""
        for m in reversed(messages or []):
            if m.get("role") == "user":
                q = str(m.get("content") or "")
                break
        try:
            n = int(app.get_plugin_setting(self.id, "max_inject", 8) or 8)
            items = self.store.recall_for_prompt(q, limit=n)
            facts = self.store.format_for_prompt(items, max_chars=1600)
        except Exception:
            facts = ""
        try:
            days = int(app.get_plugin_setting(self.id, "diary_days", 7) or 7)
            tail = self.store.recent_messages(limit=self._tail_n(app))
            tail_ids = {int(r["id"]) for r in tail if r.get("id") is not None}
            diary = self.store.format_diary(limit_days=days, max_chars=3500, tail_ids=tail_ids)
        except Exception:
            diary = ""
        inj = ""
        if facts:
            inj += "\n\n[ПАМЯТЬ ПЕРСОНАЖА]\n" + facts
        if diary:
            inj += "\n\n" + diary
        if not inj:
            return messages
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = str(messages[0].get("content") or "") + inj
        return messages

    # ---- Settings UI: список + удаление ----
    def setup_settings_tab(self, tab, app: AppContext) -> bool:
        try:
            from PyQt5 import QtWidgets, QtCore
        except ImportError:
            return False
        self.app = app
        if self.store is None:
            self._open_store(app)

        # clear tab
        if tab.layout() is not None:
            while tab.layout().count():
                item = tab.layout().takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
            layout = tab.layout()
        else:
            layout = QtWidgets.QVBoxLayout(tab)

        cid = getattr(app.config, "ACTIVE_CHARACTER", "?")
        layout.addWidget(QtWidgets.QLabel(
            f"<b>Долговременная память</b> — персонаж: <code>{cid}</code><br>"
            f"Путь: <code>{getattr(getattr(self, 'store', None), 'db_path', '—')}</code>"
        ))

        # schema fields
        form = QtWidgets.QFormLayout()
        values = {}
        try:
            from plugin_catalog import plugin_settings_block
            values = plugin_settings_block(self.id) or {}
        except Exception:
            pass
        self._ui = {}
        for field in self.settings_schema:
            key = field.key
            val = values.get(key, field.default)
            if field.type == "bool":
                w = QtWidgets.QCheckBox(field.label)
                w.setChecked(bool(val))
            elif field.type == "int":
                w = QtWidgets.QSpinBox()
                if field.min_value is not None:
                    w.setMinimum(int(field.min_value))
                if field.max_value is not None:
                    w.setMaximum(int(field.max_value))
                w.setValue(int(val if val is not None else field.default or 0))
            else:
                w = QtWidgets.QLineEdit(str(val or ""))
            self._ui[key] = w
            form.addRow(field.label if field.type != "bool" else "", w)
        layout.addLayout(form)

        layout.addWidget(QtWidgets.QLabel("<b>Записи в памяти</b> (текущий персонаж):"))
        lst = QtWidgets.QListWidget()
        lst.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._ui["list"] = lst
        layout.addWidget(lst, 1)

        row = QtWidgets.QHBoxLayout()
        btn_ref = QtWidgets.QPushButton("Обновить")
        btn_del = QtWidgets.QPushButton("Удалить выбранные")
        btn_clear = QtWidgets.QPushButton("Очистить всё")
        btn_add = QtWidgets.QPushButton("Добавить…")
        row.addWidget(btn_ref)
        row.addWidget(btn_del)
        row.addWidget(btn_clear)
        row.addWidget(btn_add)
        layout.addLayout(row)

        def refresh():
            self._refresh_list_ui()

        def delete_selected():
            if self.store is None:
                return
            items = lst.selectedItems()
            n = 0
            for it in items:
                mid = it.data(QtCore.Qt.UserRole)
                if mid is not None:
                    try:
                        if self.store.delete(int(mid)):
                            n += 1
                    except Exception as e:
                        print(f"memory ui delete: {e}", flush=True)
            refresh()
            QtWidgets.QMessageBox.information(tab, "Память", f"Удалено: {n}")

        def clear_all():
            if self.store is None:
                return
            r = QtWidgets.QMessageBox.question(
                tab, "Память", "Удалить ВСЕ записи этого персонажа?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if r != QtWidgets.QMessageBox.Yes:
                return
            try:
                if hasattr(self.store, "clear"):
                    n = self.store.clear(only_unpinned=False)
                else:
                    n = 0
                    for it in self.store.list_all(limit=5000):
                        if self.store.delete(int(it["id"])):
                            n += 1
            except Exception as e:
                QtWidgets.QMessageBox.warning(tab, "Память", str(e))
                return
            refresh()
            QtWidgets.QMessageBox.information(tab, "Память", f"Очищено: {n}")

        def add_fact():
            text, ok = QtWidgets.QInputDialog.getText(tab, "Память", "Новый факт:")
            if ok and text.strip():
                self.tool_add(app, text=text.strip())
                refresh()

        btn_ref.clicked.connect(refresh)
        btn_del.clicked.connect(delete_selected)
        btn_clear.clicked.connect(clear_all)
        btn_add.clicked.connect(add_fact)
        refresh()
        return True

    def collect_settings_tab(self) -> Dict[str, Any]:
        out = {}
        for field in self.settings_schema:
            w = self._ui.get(field.key)
            if w is None:
                continue
            if field.type == "bool":
                out[field.key] = w.isChecked()
            elif field.type == "int":
                out[field.key] = w.value()
            else:
                out[field.key] = w.text()
        return out

    def _refresh_list_ui(self) -> None:
        def go():
            self._refresh_list_ui_now()
        win = getattr(self.app, "window", None) if self.app else None
        if win is not None and hasattr(win, "post"):
            win.post(go)
        else:
            go()

    def _refresh_list_ui_now(self) -> None:
        lst = self._ui.get("list")
        if lst is None:
            return
        try:
            from PyQt5 import QtCore, QtWidgets
        except ImportError:
            return
        lst.clear()
        if self.store is None and self.app is not None:
            self._open_store(self.app)
        if self.store is None:
            lst.addItem("(память недоступна)")
            return
        try:
            items = self.store.list_all(limit=200)
        except Exception as e:
            lst.addItem(f"(ошибка: {e})")
            return
        if not items:
            lst.addItem("(пусто)")
            return
        for it in items:
            mid = it.get("id")
            content = it.get("content") or it.get("text") or ""
            row = QtWidgets.QListWidgetItem(f"#{mid}  {content}")
            row.setData(QtCore.Qt.UserRole, mid)
            lst.addItem(row)


def register():
    return PluginImpl()
