# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

@dataclass
class HookResult:
    handled: bool = False
    reply: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

@dataclass
class SettingField:
    key: str
    label: str
    type: str = "bool"  # bool|int|float|str|choice
    default: Any = None
    choices: Optional[List[str]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    help: str = ""

class Plugin:
    id: str = "plugin"
    name: str = "Plugin"
    version: str = "1.0.0"
    description: str = ""
    settings_tab: str = "plugins"  # plugins | own
    settings_tab_title: str = ""
    settings_schema: List[SettingField] = []

    def get_settings_schema(self) -> List[SettingField]:
        return list(self.settings_schema or [])

    def setup_settings_tab(self, tab, app: "AppContext") -> bool:
        return False

    def collect_settings_tab(self) -> Dict[str, Any]:
        return {}

    def on_load(self, app: "AppContext") -> None:
        pass

    def on_shutdown(self, app: "AppContext") -> None:
        pass

    def on_user_message(self, text: str, app: "AppContext") -> Optional[HookResult]:
        return None

    def on_before_llm(self, messages: List[Dict[str, Any]], app: "AppContext") -> List[Dict[str, Any]]:
        return messages

    def on_after_llm(self, reply: str, app: "AppContext") -> str:
        return reply

    def on_character_changed(
        self, character_id: str, previous_id: str, app: "AppContext"
    ) -> None:
        """Активный персонаж сменился (ядро или настройки)."""

    def register_tools(self, app: "AppContext") -> None:
        pass

class AppContext:
    def __init__(self, config_mod: Any = None):
        self.config = config_mod
        self.llm = None
        self.engine = None
        self.plugins: Dict[str, Plugin] = {}
        self.tools: Dict[str, Callable] = {}
        self.state: Dict[str, Any] = {}
        self.window = None
        self.gui = None

    def iter_plugins(self):
        """Уникальные инстансы (без тройного persona через чужие id)."""
        seen = set()
        for pl in list(self.plugins.values()):
            i = id(pl)
            if i in seen:
                continue
            seen.add(i)
            yield pl

    def is_plugin_enabled(self, plugin_id: str) -> bool:
        if not getattr(self.config, "PLUGINS_ENABLED", True):
            return False
        m = getattr(self.config, "PLUGINS", None) or {}
        if isinstance(m, dict) and plugin_id in m:
            if not m.get(plugin_id):
                return False
        store = getattr(self.config, "PLUGIN_SETTINGS", None) or {}
        block = store.get(plugin_id) if isinstance(store, dict) else None
        if isinstance(block, dict) and "enabled" in block:
            return bool(block.get("enabled"))
        if isinstance(m, dict) and plugin_id in m:
            return bool(m.get(plugin_id))
        return True

    def get_plugin_setting(self, plugin_id: str, key: str, default: Any = None) -> Any:
        store = getattr(self.config, "PLUGIN_SETTINGS", None) or {}
        block = store.get(plugin_id) if isinstance(store, dict) else None
        if not isinstance(block, dict):
            return default
        return block.get(key, default)

    def register_tool(self, name: str, fn: Callable) -> None:
        self.tools[name] = fn

    def get_active_character(self) -> str:
        return str(
            getattr(self.config, "ACTIVE_CHARACTER", None)
            or "default"
        )

    def get_character_dir(self, character_id: str | None = None) -> "Path":
        from pathlib import Path
        cid = character_id or self.get_active_character()
        base = Path(getattr(self.config, "DATA_DIR", Path(".")))
        return base / "personas" / "characters" / cid

    def get_engine(self):
        if getattr(self, "engine", None) is not None:
            return self.engine
        win = getattr(self, "window", None)
        if win is not None:
            return getattr(win, "engine", None)
        return self.state.get("engine")

    def get_gui(self):
        return (
            getattr(self, "window", None)
            or getattr(self, "gui", None)
            or self.state.get("gui")
        )

    def set_active_character(self, character_id: str) -> None:
        """Сменить персонажа и перезагрузить память/кадры без рестарта."""
        character_id = (character_id or "").strip() or "default"
        prev = self.get_active_character()
        if character_id == prev:
            return
        setattr(self.config, "ACTIVE_CHARACTER", character_id)
        for pl in list(self.iter_plugins()):
            pid = getattr(pl, "id", "?")
            try:
                pl.on_character_changed(character_id, prev, self)
            except Exception as e:
                print(f"[plugin {pid}] on_character_changed: {e}", flush=True)
        print(f"🎭 character: {prev} → {character_id}", flush=True)
        self._reload_after_character_change(prev, character_id)

    def _reload_after_character_change(self, prev: str, character_id: str) -> None:
        gui = self.get_gui()
        if gui is not None and hasattr(gui, "_append_sys"):
            gui._append_sys(f"Персонаж: {prev} → {character_id}")
        if gui is not None and hasattr(gui, "refresh_chrome"):
            try:
                gui.refresh_chrome()
            except Exception as e:
                print(f"gui refresh: {e}", flush=True)
        engine = self.get_engine()
        if engine is not None:
            engine.history = []
        mem = self.plugins.get("memory") if self.plugins else None
        loaded: List[Any] = []
        if mem is not None and engine is not None and hasattr(mem, "hydrate_engine"):
            try:
                loaded = mem.hydrate_engine(engine) or []
            except Exception as e:
                print(f"memory hydrate: {e}", flush=True)
        if gui is not None and hasattr(gui, "show_dialog_resume"):
            try:
                gui.show_dialog_resume(
                    loaded,
                    f"Персонаж: {prev} → {character_id}. Продолжаю её диалог.",
                )
            except Exception as e:
                print(f"gui resume: {e}", flush=True)
        try:
            from character_catalog import invalidate_card_cache
            invalidate_card_cache()
        except Exception as e:
            print(f"character cache: {e}", flush=True)
        self._reload_rag()

    def _reload_rag(self) -> None:
        rag = getattr(self, "rag", None) or self.state.get("rag") or self.plugins.get("rag")
        if rag is None:
            return
        import asyncio

        async def _reindex():
            for name in (
                "prune_inactive_personas_async",
                "prune_inactive_personas",
                "auto_index_from_config_async",
                "auto_index_from_config",
            ):
                fn = getattr(rag, name, None)
                if not callable(fn):
                    continue
                try:
                    res = fn()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as e:
                    print(f"RAG {name}: {e}", flush=True)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_reindex())
            else:
                loop.run_until_complete(_reindex())
        except Exception as e:
            print(f"RAG reload: {e}", flush=True)
