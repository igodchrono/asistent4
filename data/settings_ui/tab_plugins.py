# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Any, Dict
from PyQt5 import QtWidgets, QtCore

class PluginsTabMixin:
    def _setup_plugins_hub_tab(self, tab: QtWidgets.QWidget) -> None:
        layout = QtWidgets.QVBoxLayout(tab)
        layout.addWidget(QtWidgets.QLabel(
            "Список только из папок <b>plugins/</b>. Удалили папку — пункта нет."
        ))
        self.plugins_master_cb = QtWidgets.QCheckBox("PLUGINS_ENABLED")
        layout.addWidget(self.plugins_master_cb)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        self._plugins_hub_layout = QtWidgets.QVBoxLayout(inner)
        self._plugins_hub_layout.setAlignment(QtCore.Qt.AlignTop)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)
        self._plugin_enable_cbs: Dict[str, QtWidgets.QCheckBox] = {}
        self._plugin_setting_widgets: Dict[str, Dict[str, QtWidgets.QWidget]] = {}
        btn = QtWidgets.QPushButton("Обновить с диска")
        btn.clicked.connect(self._reload_plugins_hub)
        layout.addWidget(btn)
        self._reload_plugins_hub()

    def _reload_plugins_hub(self) -> None:
        try:
            from plugin_catalog import prune_orphaned_plugin_config, list_plugins_meta, is_enabled, plugin_settings_block
            import config
            from settings_manager import save_settings, load_settings
            changed = prune_orphaned_plugin_config(config)
            if changed.get("removed_enabled") or changed.get("removed_settings"):
                data = load_settings()
                data["PLUGINS"] = getattr(config, "PLUGINS", {}) or {}
                data["PLUGIN_SETTINGS"] = getattr(config, "PLUGIN_SETTINGS", {}) or {}
                save_settings(data)
            self.plugins_master_cb.setChecked(bool(getattr(config, "PLUGINS_ENABLED", True)))
            metas = list_plugins_meta()
        except Exception as e:
            while self._plugins_hub_layout.count():
                it = self._plugins_hub_layout.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            self._plugins_hub_layout.addWidget(QtWidgets.QLabel(str(e)))
            return

        while self._plugins_hub_layout.count():
            it = self._plugins_hub_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._plugin_enable_cbs.clear()
        self._plugin_setting_widgets.clear()

        if not metas:
            self._plugins_hub_layout.addWidget(QtWidgets.QLabel("plugins/ пуст — плагинов нет."))
            return

        for meta in metas:
            pid = meta["id"]
            box = QtWidgets.QGroupBox(f"{meta.get('name')} ({pid}) v{meta.get('version')}")
            v = QtWidgets.QVBoxLayout(box)
            if meta.get("description"):
                v.addWidget(QtWidgets.QLabel(meta["description"]))
            if meta.get("error"):
                v.addWidget(QtWidgets.QLabel(f"⚠ {meta['error']}"))
            cb = QtWidgets.QCheckBox("Включён")
            cb.setChecked(is_enabled(pid))
            self._plugin_enable_cbs[pid] = cb
            v.addWidget(cb)
            widgets = {}
            if (meta.get("settings_tab") or "plugins") == "plugins" and meta.get("schema"):
                form = QtWidgets.QFormLayout()
                values = plugin_settings_block(pid)
                for field in meta["schema"]:
                    w = self._make_setting_widget(field, values.get(field["key"], field.get("default")))
                    widgets[field["key"]] = w
                    form.addRow(field.get("label") or field["key"], w)
                v.addLayout(form)
            self._plugin_setting_widgets[pid] = widgets
            self._plugins_hub_layout.addWidget(box)

    def _make_setting_widget(self, field: Dict[str, Any], value: Any) -> QtWidgets.QWidget:
        ftype = (field.get("type") or "bool").lower()
        if ftype == "bool":
            w = QtWidgets.QCheckBox()
            w.setChecked(bool(value if value is not None else field.get("default")))
            return w
        if ftype == "choice":
            w = QtWidgets.QComboBox()
            for c in field.get("choices") or []:
                w.addItem(str(c))
            if value is not None:
                i = w.findText(str(value))
                if i >= 0:
                    w.setCurrentIndex(i)
            return w
        if ftype == "int":
            w = QtWidgets.QSpinBox()
            w.setRange(int(field.get("min_value") or -10**9), int(field.get("max_value") or 10**9))
            w.setValue(int(value if value is not None else (field.get("default") or 0)))
            return w
        if ftype == "float":
            w = QtWidgets.QDoubleSpinBox()
            w.setRange(float(field.get("min_value") or -1e9), float(field.get("max_value") or 1e9))
            w.setValue(float(value if value is not None else (field.get("default") or 0)))
            return w
        w = QtWidgets.QLineEdit()
        w.setText("" if value is None else str(value))
        return w

    def _read_setting_widget(self, field: Dict[str, Any], w: QtWidgets.QWidget) -> Any:
        ftype = (field.get("type") or "bool").lower()
        if ftype == "bool":
            return w.isChecked()
        if ftype == "choice":
            return w.currentText()
        if ftype in ("int", "float"):
            return w.value()
        return w.text()

    def _setup_plugin_own_tab(self, tab: QtWidgets.QWidget, meta: Dict[str, Any]) -> None:
        layout = QtWidgets.QVBoxLayout(tab)
        pid = meta["id"]
        layout.addWidget(QtWidgets.QLabel(f"<b>{meta.get('name')}</b> — {meta.get('description') or ''}"))
        cb = QtWidgets.QCheckBox("Включён")
        from plugin_catalog import is_enabled, plugin_settings_block
        cb.setChecked(is_enabled(pid))
        self._plugin_enable_cbs[pid] = cb
        layout.addWidget(cb)
        form = QtWidgets.QFormLayout()
        widgets = {}
        values = plugin_settings_block(pid)
        for field in meta.get("schema") or []:
            w = self._make_setting_widget(field, values.get(field["key"], field.get("default")))
            widgets[field["key"]] = w
            form.addRow(field.get("label") or field["key"], w)
        layout.addLayout(form)
        layout.addStretch(1)
        self._plugin_setting_widgets[pid] = widgets

    def collect_plugins_settings(self) -> dict:
        from plugin_catalog import list_plugins_meta, discover_plugin_ids, is_enabled
        metas = {m["id"]: m for m in list_plugins_meta()}
        alive = set(discover_plugin_ids())
        enabled = {}
        for pid in alive:
            cb = getattr(self, "_plugin_enable_cbs", {}).get(pid)
            if cb is not None:
                try:
                    enabled[pid] = bool(cb.isChecked())
                except Exception:
                    enabled[pid] = is_enabled(pid)
            else:
                enabled[pid] = is_enabled(pid)
        store = {}
        for pid, widgets in getattr(self, "_plugin_setting_widgets", {}).items():
            if pid not in alive:
                continue
            schema = {f["key"]: f for f in (metas.get(pid) or {}).get("schema") or []}
            block = {}
            for key, w in widgets.items():
                field = schema.get(key) or {"type": "str"}
                block[key] = self._read_setting_widget(field, w)
            if pid in enabled:
                block["enabled"] = enabled[pid]
            if block:
                store[pid] = block
        for pid, on in enabled.items():
            store.setdefault(pid, {})["enabled"] = on
        return {
            "PLUGINS_ENABLED": self.plugins_master_cb.isChecked() if hasattr(self, "plugins_master_cb") else True,
            "PLUGINS": enabled,
            "PLUGIN_SETTINGS": store,
        }
