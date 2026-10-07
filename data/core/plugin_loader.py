# -*- coding: utf-8 -*-
from __future__ import annotations
import importlib
import pkgutil
from typing import Dict, List, Optional, Type
from .plugin_api import AppContext, Plugin

class PluginLoader:
    def __init__(self, app: AppContext):
        self.app = app
        self._order: List[str] = []

    def discover(self) -> List[str]:
        ids: List[str] = []
        try:
            import plugins as pkg
            for m in pkgutil.iter_modules(pkg.__path__):
                if m.name.startswith("_"):
                    continue
                ids.append(m.name)
        except Exception as e:
            print(f"⚠️ discover plugins: {e}", flush=True)
        return sorted(ids)

    def load_all(self) -> Dict[str, Plugin]:
        for pid in self.discover():
            if not self.app.is_plugin_enabled(pid):
                print(f"🔌 skip (off): {pid}", flush=True)
                continue
            try:
                self.load_one(pid)
            except Exception as e:
                print(f"⚠️ plugin {pid}: {e}", flush=True)
        return self.app.plugins

    def load_one(self, plugin_id: str) -> Optional[Plugin]:
        mod = importlib.import_module(f"plugins.{plugin_id}.plugin")
        cls: Optional[Type[Plugin]] = getattr(mod, "PluginImpl", None)
        if cls is None:
            for v in vars(mod).values():
                if isinstance(v, type) and issubclass(v, Plugin) and v is not Plugin:
                    cls = v
                    break
        if cls is None:
            raise RuntimeError(f"No PluginImpl in plugins.{plugin_id}.plugin")
        if getattr(cls, "hidden", False):
            print(f"🔌 skip (hidden): {plugin_id}", flush=True)
            return None
        inst = cls()
        inst.id = getattr(inst, "id", None) or plugin_id
        inst.on_load(self.app)
        try:
            inst.register_tools(self.app)
        except Exception as e:
            print(f"⚠️ register_tools {plugin_id}: {e}", flush=True)
        self.app.plugins[inst.id] = inst
        self._order.append(inst.id)
        print(f"🔌 loaded: {inst.id} — {inst.name}", flush=True)
        return inst

    def shutdown_all(self) -> None:
        for pid in reversed(self._order):
            pl = self.app.plugins.get(pid)
            if not pl:
                continue
            try:
                pl.on_shutdown(self.app)
            except Exception as e:
                print(f"⚠️ shutdown {pid}: {e}", flush=True)
        self._order.clear()
