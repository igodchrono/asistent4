# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict

import config

ALLOWED_PREFIXES = ("PLUGIN", "VOICE_", "SEARCH_", "NSFW_", "GREETING_", "SCREEN_", "CHAT_")
ALLOWED_KEYS = {
    "API_URL", "API_KEY", "MODEL_NAME", "TEMPERATURE", "MAX_TOKENS",
    "SYSTEM_PROMPT", "PLUGINS_ENABLED", "PLUGINS", "PLUGIN_SETTINGS",
    "WINDOW_TITLE", "LLM_TIMEOUT", "ACTIVE_CHARACTER", "ACTIVE_USER",
    "ASSISTANT_MODE", "HISTORY_TAIL",
}

def settings_path() -> Path:
    return Path(getattr(config, "SETTINGS_FILE", None) or (Path(config.DATA_DIR) / "settings.json"))

def example_path() -> Path:
    return settings_path().with_name("settings.example.json")


def load_settings() -> Dict[str, Any]:
    p = settings_path()
    if not p.is_file():
        ex = example_path()
        if ex.is_file():
            try:
                data = json.loads(ex.read_text(encoding="utf-8"))
            except Exception:
                return {}
            try:
                p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except Exception:
                pass
            return data
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_settings(data: Dict[str, Any]) -> None:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    cur = load_settings()
    cur.update(data)
    p.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def apply_to_config(config_module=None) -> Dict[str, Any]:
    import config as cfg
    config_module = config_module or cfg
    data = load_settings()
    applied = {}
    for k, v in data.items():
        if k in ALLOWED_KEYS or k.startswith(ALLOWED_PREFIXES):
            setattr(config_module, k, v)
            applied[k] = v
    return applied
