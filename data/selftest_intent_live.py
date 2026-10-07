# -*- coding: utf-8 -*-
"""
selftest_intent_live — проверка INTENT-архитектуры через ChatEngine.
Не дергает on_user_message плагинов напрямую (кроме подтверждений).
Требует: LM Studio online для classify + ответов.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

PAUSE = 0.35


class Log:
    def __init__(self, path: Path):
        self.path = path
        self.ok = self.fail = self.warn = self.skip = 0
        self._fp = path.open("w", encoding="utf-8")

    def line(self, s: str = "") -> None:
        msg = f"[{datetime.now():%H:%M:%S}] {s}"
        print(msg, flush=True)
        self._fp.write(msg + "\n")
        self._fp.flush()

    def result(self, section: str, name: str, status: str, detail: str = "") -> None:
        status = status.upper()
        if status == "OK":
            self.ok += 1
            mark = "✅"
        elif status == "FAIL":
            self.fail += 1
            mark = "❌"
        elif status == "SKIP":
            self.skip += 1
            mark = "⏭"
        else:
            self.warn += 1
            mark = "⚠️"
        self.line(f"{mark} [{section}] {name} → {status}" + (f" {detail[:180]}" if detail else ""))

    def close(self) -> None:
        self._fp.close()


def process_events() -> None:
    try:
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.processEvents()
    except Exception:
        pass


async def run_engine(engine, text: str) -> str:
    parts = []
    async for chunk in engine.handle_user(text):
        parts.append(chunk)
    return "".join(parts).strip()


def capture_prints(fn):
    """Не используем — intent печатает сам в stdout."""
    return fn()


async def main_async(args) -> int:
    log = Log(ROOT / "selftest_intent_live.log")
    log.line("=" * 60)
    log.line("selftest_intent_live — через ChatEngine")
    log.line(f"cwd={ROOT}")

    import config
    from core.plugin_api import AppContext
    from core.plugin_loader import PluginLoader
    from core.chat_engine import ChatEngine
    from core.llm_client import LLMClient

    app = AppContext(config)
    # минимальное окно-заглушка
    class W:
        _busy = False
        engine = None
        def isVisible(self):
            return True
        def publish_assistant_message(self, t):
            pass
    app.window = W()

    loader = PluginLoader(app)
    # skip voice
    ids = [i for i in loader.discover() if i != "voice"]
    for pid in ids:
        if not app.is_plugin_enabled(pid):
            continue
        try:
            loader.load_one(pid)
        except Exception as e:
            log.result("load", pid, "WARN", str(e))
    log.line(f"loaded: {list(app.plugins.keys())}")
    log.line(f"tools: {sorted(app.tools.keys())}")

    llm = LLMClient.from_config(config)
    engine = ChatEngine(app, llm)
    app.window.engine = engine

    # ping
    try:
        models, err = await llm.list_models()
        if err:
            log.result("llm", "ping", "WARN", str(err))
        else:
            log.result("llm", "ping", "OK", f"models={models[:3] if models else '?'}")
    except Exception as e:
        log.result("llm", "ping", "FAIL", str(e))

    async def check(section: str, phrase: str, expect_intent_substr: str = "", expect_in_reply: str = ""):
        process_events()
        # перехват intent print уже в консоли; смотрим ответ
        try:
            reply = await run_engine(engine, phrase)
        except Exception as e:
            log.result(section, phrase[:40], "FAIL", f"{type(e).__name__}: {e}")
            return
        detail = reply[:120].replace("\n", " ")
        ok = True
        if expect_in_reply and expect_in_reply.lower() not in reply.lower():
            ok = False
            detail = f"no '{expect_in_reply}' in: {detail}"
        log.result(section, phrase[:50], "OK" if ok else "WARN", detail)
        time.sleep(PAUSE)

    # --- MEMORY via intent ---
    log.line("")
    log.line("===== INTENT: MEMORY =====")
    await check("memory", "запомни: intent_alpha любимый напиток чай", expect_in_reply="")
    await check("memory", "запомни: intent_beta любимый цвет синий")
    await check("memory", "запомни: intent_gamma любимое число семь")
    await check("memory", "что ты помнишь", expect_in_reply="intent_")
    await check("memory", "забудь про intent_beta")
    await check("memory", "что ты помнишь")

    # --- SCREEN ---
    log.line("")
    log.line("===== INTENT: SCREEN =====")
    await check("screen", "что у меня сейчас на экране")
    # context should be filled
    desc = str(app.state.get("screen_vision_last_desc") or "")
    log.result("screen", "state last_desc", "OK" if desc else "WARN", desc[:100] or "empty")

    # --- SIMILAR (must NOT search literal phrase) ---
    log.line("")
    log.line("===== INTENT: SEARCH SIMILAR =====")
    await check("similar", "найди похожий сайт")
    await check("similar", "найди похожие картинки")

    # --- PC ---
    log.line("")
    log.line("===== INTENT: PC =====")
    await check("pc", "открой калькулятор")
    await check("pc", "закрой калькулятор")
    await check("pc", "найди папку asistent3")
    await check("pc", "открой найденное")
    await check("pc", "закрой последнее открытое")
    await check("pc", "создай текстовый файл intent_note.txt")
    await check("pc", "удали файл intent_note.txt в корзину")

    # --- BROWSER ---
    log.line("")
    log.line("===== INTENT: BROWSER =====")
    await check("browser", "найди python asyncio documentation")

    # --- DEEP / CHAT ---
    log.line("")
    log.line("===== INTENT: CHAT / DEEP =====")
    await check("chat", "привет")
    await check("deep", "объясни подробно что такое asyncio")

    # tools registered
    log.line("")
    log.line("===== TOOLS REGISTRY =====")
    need = [
        "web_search", "search_similar", "describe_screen",
        "memory_add", "memory_list", "memory_forget",
        "pc_open", "pc_close", "pc_search_folders",
        "note_add", "reminder_list",
    ]
    for name in need:
        log.result("tools", name, "OK" if name in app.tools else "FAIL", "registered" if name in app.tools else "MISSING")

    # memory store
    mem = app.plugins.get("memory")
    if mem and getattr(mem, "store", None):
        try:
            n = mem.store.count()
            log.result("memory", "store.count", "OK", f"n={n}")
        except Exception as e:
            log.result("memory", "store.count", "FAIL", str(e))
    else:
        log.result("memory", "store", "FAIL", "no MemoryStore")

    log.line("")
    log.line("=" * 60)
    log.line(f"ИТОГО OK={log.ok} FAIL={log.fail} WARN={log.warn} SKIP={log.skip}")
    log.line(f"лог: {log.path}")
    log.close()
    return 1 if log.fail else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", default=True)
    args = ap.parse_args()
    try:
        code = asyncio.run(main_async(args))
    except Exception:
        traceback.print_exc()
        code = 2
    return code


if __name__ == "__main__":
    sys.exit(main())
