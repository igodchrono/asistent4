# -*- coding: utf-8 -*-
from PyQt5 import QtWidgets
from settings_ui import MainTabMixin, PluginsTabMixin, PersonaTabMixin
from settings_manager import save_settings, apply_to_config
import config


class SettingsDialog(MainTabMixin, PluginsTabMixin, PersonaTabMixin, QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки ассистента")
        self.resize(640, 740)
        self._persona_tab_widget = None
        self._custom_plugin_uis = {}
        self.tabs = QtWidgets.QTabWidget()

        main = QtWidgets.QWidget()
        self._setup_main_tab(main)
        self.tabs.addTab(main, "Основные")

        if self._has_characters():
            self._add_persona_tab()

        plug = QtWidgets.QWidget()
        self._setup_plugins_hub_tab(plug)
        self.tabs.addTab(plug, "Плагины")

        try:
            from plugin_catalog import list_plugins_meta
            for meta in list_plugins_meta():
                if (meta.get("settings_tab") or "") != "own":
                    continue
                own = QtWidgets.QWidget()
                if not self._try_plugin_custom_tab(own, meta):
                    self._setup_plugin_own_tab(own, meta)
                else:
                    self._ensure_enable_cb(meta["id"])
                title = meta.get("settings_tab_title") or meta.get("name") or meta.get("id")
                self.tabs.addTab(own, str(title))
        except Exception as e:
            print("plugins own tabs:", e)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self.tabs)
        row = QtWidgets.QHBoxLayout()
        save_btn = QtWidgets.QPushButton("Сохранить")
        save_btn.clicked.connect(self._save)
        close_btn = QtWidgets.QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(save_btn)
        row.addWidget(close_btn)
        lay.addLayout(row)

        self.load_main_settings()
        if self._persona_tab_widget is not None:
            self.load_persona_settings()

    def _has_characters(self) -> bool:
        try:
            from character_catalog import list_character_ids
            return len(list_character_ids()) > 0
        except Exception:
            return False

    def _add_persona_tab(self) -> None:
        if self._persona_tab_widget is not None:
            return
        w = QtWidgets.QWidget()
        self._setup_persona_tab(w)
        self.tabs.insertTab(1, w, "🎭 Персонаж")
        self._persona_tab_widget = w

    def _ensure_enable_cb(self, pid: str) -> None:
        if not hasattr(self, "_plugin_enable_cbs"):
            self._plugin_enable_cbs = {}
        if pid in getattr(self, "_plugin_enable_cbs", {}):
            return
        from plugin_catalog import is_enabled
        cb = QtWidgets.QCheckBox()
        cb.setChecked(is_enabled(pid))
        self._plugin_enable_cbs[pid] = cb

    def _try_plugin_custom_tab(self, tab, meta) -> bool:

        """Кастомный UI плагина (например список памяти)."""
        try:
            import importlib
            pid = meta.get("id")
            if not pid:
                return False
            parent = self.parent()
            engine = getattr(parent, "engine", None) if parent is not None else None
            app = getattr(engine, "app", None) if engine is not None else None
            target = None
            if app is not None:
                target = app.plugins.get(pid)
            if target is None:
                mod = importlib.import_module(f"plugins.{pid}.plugin")
                cls = getattr(mod, "PluginImpl", None)
                if cls is None:
                    return False
                target = cls()
            if app is None:
                from core.plugin_api import AppContext
                app = AppContext(config)
            if not hasattr(target, "setup_settings_tab"):
                return False
            ok = bool(target.setup_settings_tab(tab, app))
            if ok:
                self._custom_plugin_uis[pid] = target
            return ok
        except Exception as e:
            print(f"custom tab {meta.get('id')}: {e}")
            return False

    def _save(self):
        data = {}
        data.update(self.collect_main_settings())
        if self._persona_tab_widget is not None:
            data.update(self.collect_persona_settings())
        data.update(self.collect_plugins_settings())
        for pid, pl in self._custom_plugin_uis.items():
            try:
                extra = pl.collect_settings_tab() or {}
                if extra:
                    store = data.setdefault("PLUGIN_SETTINGS", {})
                    block = dict(store.get(pid) or {})
                    block.update(extra)
                    store[pid] = block
            except Exception as e:
                print(f"collect custom {pid}: {e}")
        prev = getattr(config, "ACTIVE_CHARACTER", "default")
        save_settings(data)
        apply_to_config(config)
        # runtime: выключить tools у отключённых плагинов
        try:
            self._apply_plugin_toggles_runtime(data.get("PLUGINS") or {})
        except Exception as e:
            print(f"runtime_plugin_toggle: {e}")
        new_ch = getattr(config, "ACTIVE_CHARACTER", prev)
        parent = self.parent()
        engine = getattr(parent, "engine", None) if parent is not None else None
        app_ctx = getattr(engine, "app", None) if engine is not None else None
        if app_ctx is not None and hasattr(app_ctx, "set_active_character"):
            if str(new_ch) != str(prev):
                setattr(config, "ACTIVE_CHARACTER", prev)
                app_ctx.set_active_character(str(new_ch))
                setattr(config, "ACTIVE_CHARACTER", str(new_ch))
        # После применения настроек — уведомить плагины о смене конфигурации,
        # чтобы они могли моментально применить новые параметры (без перезапуска).
        try:
            if app_ctx is not None:
                cid = str(getattr(config, "ACTIVE_CHARACTER", "default"))
                for pl in list(getattr(app_ctx, "iter_plugins", lambda: app_ctx.plugins.values())()):
                    try:
                        # Вызов on_character_changed с одинаковыми prev/current заставит плагин
                        # перечитать свои настройки и обновить UI.
                        pl.on_character_changed(cid, cid, app_ctx)
                    except Exception:
                        pass
        except Exception:
            pass
        QtWidgets.QMessageBox.information(self, "OK", "Сохранено.")
        self.accept()

    def _app_ctx(self):
        parent = self.parent()
        engine = getattr(parent, "engine", None) if parent is not None else None
        return getattr(engine, "app", None) if engine is not None else None

    def _apply_plugin_toggles_runtime(self, plugins_map) -> None:
        app = self._app_ctx()
        if app is None:
            return
        app.tools.clear()
        it = getattr(app, "iter_plugins", None)
        plugins = list(it()) if callable(it) else list(app.plugins.values())
        for pl in plugins:
            pid = getattr(pl, "id", "")
            on = True
            if isinstance(plugins_map, dict) and pid in plugins_map:
                on = bool(plugins_map.get(pid))
            if not on or not app.is_plugin_enabled(pid):
                print(f"🔌 runtime off: {pid}", flush=True)
                continue
            try:
                pl.register_tools(app)
            except Exception as e:
                print(f"runtime register_tools {pid}: {e}", flush=True)
        print(f"runtime tools: {sorted(app.tools)}", flush=True)

