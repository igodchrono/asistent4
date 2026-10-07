# -*- coding: utf-8 -*-
"""Ядро: splash → LLM chat → плагины → GUI."""
from __future__ import annotations
import sys
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import Qt, QCoreApplication
try:
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
except Exception:
    pass
try:
    from PyQt5 import QtWebEngineWidgets  # noqa: F401  # до QApplication
except Exception:
    QtWebEngineWidgets = None  # type: ignore
from PyQt5 import QtWidgets
from qasync import QEventLoop

import config
from settings_manager import apply_to_config
from core.plugin_api import AppContext
from core.plugin_loader import PluginLoader
from core.llm_client import LLMClient
from core.chat_engine import ChatEngine
from gui import ChatWindow

try:
    from splash import BootSplash
except Exception:
    BootSplash = None


def main() -> None:
    applied = apply_to_config(config)
    print(f"settings keys: {len(applied)}", flush=True)
    try:
        from character_catalog import list_character_ids
        ids = list_character_ids()
        active = getattr(config, "ACTIVE_CHARACTER", None)
        if ids:
            if not active or active not in ids:
                config.ACTIVE_CHARACTER = "лисичка" if "лисичка" in ids else ids[0]
            print(f"characters: {ids} active={config.ACTIVE_CHARACTER}", flush=True)
        else:
            print("characters: (нет папок personas/characters/) — вкладка «Персонаж» скрыта", flush=True)
    except Exception as e:
        print(f"characters: {e}", flush=True)

    # Qt WebEngine: атрибут и импорт ДО QApplication
    try:
        from PyQt5.QtCore import Qt, QCoreApplication
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    except Exception as e:
        print(f"Qt AA_ShareOpenGLContexts: {e}", flush=True)
    try:
        # обязательный ранний импорт, иначе WebEngine из плагина падает
        from PyQt5 import QtWebEngineWidgets  # noqa: F401
        print("QtWebEngineWidgets: preloaded", flush=True)
    except Exception as e:
        print(f"QtWebEngineWidgets preload: {e}", flush=True)

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Lisichka Core")
    app.setStyle("Fusion")

    # тёмная палитра
    from PyQt5 import QtGui
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.Window, QtGui.QColor(30, 30, 30))
    palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor(240, 240, 240))
    palette.setColor(QtGui.QPalette.Base, QtGui.QColor(45, 45, 45))
    palette.setColor(QtGui.QPalette.Text, QtGui.QColor(240, 240, 240))
    palette.setColor(QtGui.QPalette.Button, QtGui.QColor(58, 58, 58))
    palette.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(240, 240, 240))
    app.setPalette(palette)

    splash = None
    if BootSplash is not None:
        try:
            splash = BootSplash(app)
            splash.say("конфиг", 5)
        except Exception as e:
            print(f"splash: {e}", flush=True)
            splash = None

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    if splash:
        splash.say("ядро", 15)

    ctx = AppContext(config)
    ctx.llm = LLMClient.from_config(config)
    try:
        from core.mode import get_mode
        ctx.state["assistant_mode"] = get_mode(config)
    except Exception as e:
        print(f"mode boot: {e}", flush=True)

    if splash:
        splash.say("плагины", 40)
    loader = PluginLoader(ctx)
    loader.load_all()

    if splash:
        splash.say("чат", 70)
    engine = ChatEngine(ctx, ctx.llm)

    async def _ping():
        try:
            ok = await ctx.llm.ping()
            print(f"LLM {config.API_URL}: {'online' if ok else 'offline'}", flush=True)
        except Exception as e:
            print(f"LLM ping: {e}", flush=True)

    if splash:
        splash.say("окно", 90)
    win = ChatWindow(engine, loader)
    ctx.window = win
    ctx.state["engine"] = engine
    try:
        mem = ctx.plugins.get("memory")
        if mem is not None and hasattr(mem, "hydrate_engine"):
            rows = mem.hydrate_engine(engine) or []
            n = len(rows)
            note = "Новый диалог." if not n else f"Продолжаю диалог «{getattr(config, 'ACTIVE_CHARACTER', '')}»: {n} реплик в рабочем окне."
            win.show_dialog_resume(rows, note)
    except Exception as e:
        print(f"memory boot: {e}", flush=True)

    async def _boot_diary():
        try:
            mem = ctx.plugins.get("memory")
            if mem is not None and hasattr(mem, "maybe_summarize"):
                await mem.maybe_summarize(engine.llm)
        except Exception as e:
            print(f"memory boot diary: {e}", flush=True)

    if splash:
        splash.finish(win)
    else:
        win.show()

    win.show()
    print("CORE READY", flush=True)

    with loop:
        loop.create_task(_ping())
        loop.create_task(_boot_diary())
        loop.run_forever()


if __name__ == "__main__":
    main()
