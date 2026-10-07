# -*- coding: utf-8 -*-
from __future__ import annotations
import importlib
from pathlib import Path
from typing import Any, Dict, List, Optional
import config

def plugins_root() -> Path:
    return Path(getattr(config, "DATA_DIR", Path(__file__).resolve().parent)) / "plugins"

def discover_plugin_ids() -> List[str]:
    root = plugins_root()
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if p.is_dir() and not p.name.startswith("_") and (p / "plugin.py").is_file():
            out.append(p.name)
    return out

def load_plugin_meta(plugin_id: str) -> Dict[str, Any]:
    try:
        mod = importlib.import_module(f"plugins.{plugin_id}.plugin")
        cls = getattr(mod, "PluginImpl", None)
        if cls is None:
            return {"id": plugin_id, "name": plugin_id, "version": "?", "description": "",
                    "settings_tab": "plugins", "settings_tab_title": plugin_id, "schema": [],
                    "error": "No PluginImpl"}
        inst = cls()
        schema = []
        for f in inst.get_settings_schema() or []:
            schema.append({
                "key": f.key, "label": f.label, "type": f.type, "default": f.default,
                "choices": f.choices, "min_value": f.min_value, "max_value": f.max_value,
                "help": f.help,
            })
        return {
            "id": getattr(inst, "id", plugin_id) or plugin_id,
            "name": getattr(inst, "name", plugin_id),
            "version": getattr(inst, "version", "1.0.0"),
            "description": getattr(inst, "description", ""),
            "settings_tab": getattr(inst, "settings_tab", "plugins") or "plugins",
            "settings_tab_title": getattr(inst, "settings_tab_title", "") or getattr(inst, "name", plugin_id),
            "schema": schema, "error": None,
        }
    except Exception as e:
        return {"id": plugin_id, "name": plugin_id, "version": "?", "description": "",
                "settings_tab": "plugins", "settings_tab_title": plugin_id, "schema": [], "error": str(e)}

def list_plugins_meta() -> List[Dict[str, Any]]:
    return [load_plugin_meta(i) for i in discover_plugin_ids()]

def is_enabled(plugin_id: str) -> bool:
    m = getattr(config, "PLUGINS", None) or {}
    if not isinstance(m, dict) or plugin_id not in m:
        return True
    return bool(m.get(plugin_id))

def plugin_settings_block(plugin_id: str) -> Dict[str, Any]:
    store = getattr(config, "PLUGIN_SETTINGS", None) or {}
    block = store.get(plugin_id) if isinstance(store, dict) else None
    return dict(block) if isinstance(block, dict) else {}

def prune_orphaned_plugin_config(config_mod=None) -> dict:
    import config as cfg
    config_mod = config_mod or cfg
    alive = set(discover_plugin_ids())
    changed = {"removed_enabled": [], "removed_settings": []}
    mapping = getattr(config_mod, "PLUGINS", None)
    if isinstance(mapping, dict):
        new_map = {k: v for k, v in mapping.items() if k in alive}
        removed = [k for k in mapping if k not in alive]
        if removed:
            config_mod.PLUGINS = new_map
            changed["removed_enabled"] = removed
    store = getattr(config_mod, "PLUGIN_SETTINGS", None)
    if isinstance(store, dict):
        new_store = {k: v for k, v in store.items() if k in alive}
        removed_s = [k for k in store if k not in alive]
        if removed_s:
            config_mod.PLUGIN_SETTINGS = new_store
            changed["removed_settings"] = removed_s
    return changed
