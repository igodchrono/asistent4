# -*- coding: utf-8 -*-
"""Минимальный конфиг ядра. Плагины добавляют свои ключи через settings.json."""
import os
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
BASE_DIR = DATA_DIR

API_URL = os.environ.get("LISICHKA_API_URL", "http://127.0.0.1:1234/v1")
API_KEY = os.environ.get("LISICHKA_API_KEY", "lm-studio")
MODEL_NAME = "local-model"
TEMPERATURE = 0.75
MAX_TOKENS = 1000
LLM_TIMEOUT = 300
LLM_CONNECT_TIMEOUT = 5
LLM_ECONOMY_MODE = False  # True = генерация только по запросу пользователя, без авто-суммаризации и проактива
LLM_CONTEXT_MODE = "full"  # full = карточка и история, lean = в модель только фраза пользователя

# Сохранённые профили подключения к LLM
SAVED_PROFILES = []

SYSTEM_PROMPT = (
    "Ты живой ассистент. Характер, стиль и границы — только из карточки персонажа. "
    "Не сжимай ответ ради краткости, если персонаж или пользователь просят иначе."
)

# плагины
PLUGINS_ENABLED = True
PLUGINS = {}
PLUGIN_SETTINGS = {}

# UI
WINDOW_TITLE = "Лисичка"
WINDOW_WIDTH = 920
WINDOW_HEIGHT = 720

SETTINGS_FILE = str(DATA_DIR / "settings.json")

# персонаж
ACTIVE_CHARACTER = "лисичка"
ACTIVE_USER = "default"

# companion = 18+ личный, work = задача сначала, без флирта
ASSISTANT_MODE = "companion"
HISTORY_TAIL = 40
