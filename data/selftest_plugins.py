# -*- coding: utf-8 -*-
"""Selftest всех плагинов asistent3 — по 2 прохода на каждый + лог.

Запуск из папки data:
  python selftest_plugins.py
  python selftest_plugins.py --browser --llm
  python selftest_plugins.py --rounds 2 --quick
  python selftest_plugins.py --plugin emotion --plugin notes

Логи:
  selftest_plugins.log   — полный прогон
  selftest_human.log     — копия для bat-скрипта
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

LOG_MAIN = ROOT / "selftest_plugins.log"
LOG_HUMAN = ROOT / "selftest_human.log"
PAUSE = 0.25

# Без QApplication плагины auto_messages/reminders/persona
# падают: "QWidget: Must construct a QApplication before a QWidget"
_QT_APP = None


def ensure_qt_app():
    """Создать QApplication до загрузки плагинов (иначе краш Qt)."""
    global _QT_APP
    if _QT_APP is not None:
        return _QT_APP
    # offscreen — можно гонять без монитора; на Windows с GUI тоже ок
    os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM", "windows"))
    try:
        from PyQt5 import QtWidgets, QtCore

        # если уже есть (редко) — переиспользуем
        existing = QtWidgets.QApplication.instance()
        if existing is not None:
            _QT_APP = existing
            return _QT_APP
        _QT_APP = QtWidgets.QApplication(sys.argv[:1] + ["--selftest"])
        # не показываем окна по умолчанию
        try:
            _QT_APP.setQuitOnLastWindowClosed(False)
        except Exception:
            pass
        return _QT_APP
    except Exception as e:
        print(f"[selftest] Qt init failed (плагины с QTimer могут упасть): {e}", flush=True)
        return None

# id плагина → список тестовых фраз (2 прохода = фразы гоняются дважды)
PLUGIN_PHRASES: Dict[str, List[str]] = {
    "persona": [
        "люблю тебя",
        "бесит меня всё",
        "мне грустно",
        "ура отлично",
        "что у меня на экране",
    ],
    "screen": [
        "что у меня на экране",
        "посмотри на экран",
        "что видно на мониторе",
        "опиши экран",
    ],
    "notes": [
        "запиши: selftest заметка проверка",
        "покажи заметки",
        "найди в заметках selftest",
    ],
    "reminders": [
        "напомни через 120 секунд selftest напоминание",
        "список напоминаний",
    ],
    "memory": [
        "запомни: selftest факт памяти",
        "меня зовут Тестер",
        "что ты помнишь",
    ],
    "browser_search": [
        "найди котиков",
        "найди картинки лис",
    ],
    "pc_control": [
        "открой калькулятор",
        "закрой калькулятор",
        "громче",
        "тише",
    ],
    "deep_think": [
        "объясни подробно что такое плагин",
        "разложи по шагам как работает память",
    ],
    "rag": [
        "что написано в карточке персонажа",
        "расскажи о себе по файлам",
    ],
    "auto_messages": [
        "привет",
    ],
    "voice": [
        "привет",
    ],
    "character_log": [
        "привет",
    ],
}

# общие фразы на каждый проход после плагинов
GLOBAL_PHRASES = [
    "привет",
    "как дела",
]


class Logger:
    def __init__(self, *paths: Path):
        self.paths = paths
        for p in paths:
            try:
                if p.is_file():
                    p.unlink()
            except Exception:
                pass
            try:
                p.write_text("", encoding="utf-8")
            except Exception:
                pass
        self.ok = 0
        self.fail = 0
        self.skip = 0
        self.rows: List[dict] = []

    def log(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        for p in self.paths:
            try:
                with p.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    def result(self, plugin: str, pass_n: int, phrase: str, status: str, detail: str = "") -> None:
        mark = {"OK": "✅", "FAIL": "❌", "SKIP": "⏭", "WARN": "⚠️"}.get(status, "·")
        self.log(f"{mark} [{plugin}] pass={pass_n} «{phrase[:60]}» → {status} {detail[:160]}")
        self.rows.append(
            {"plugin": plugin, "pass": pass_n, "phrase": phrase, "status": status, "detail": detail}
        )
        if status == "OK":
            self.ok += 1
        elif status == "FAIL":
            self.fail += 1
        else:
            self.skip += 1


def build_app(log: Optional["Logger"] = None):
    """Минимальный AppContext + загрузка плагинов как в main."""
    # КРИТИЧНО: Qt до plugin on_load (QTimer / AvatarWindow)
    qt = ensure_qt_app()
    if log:
        log.log(f"Qt QApplication: {'ok' if qt is not None else 'MISSING'}")

    import config

    try:
        from settings_manager import apply_to_config

        apply_to_config(config)
    except Exception as e:
        if log:
            log.log(f"settings apply: {e}")

    from core.plugin_api import AppContext
    from core.plugin_loader import PluginLoader

    app = AppContext(config)
    if not getattr(config, "ACTIVE_CHARACTER", None) or config.ACTIVE_CHARACTER == "default":
        try:
            from character_catalog import list_character_ids

            ids = list_character_ids()
            if ids:
                config.ACTIVE_CHARACTER = ids[0]
        except Exception:
            config.ACTIVE_CHARACTER = "лисичка"

    loader = PluginLoader(app)
    plugins: Dict[str, Any] = {}
    try:
        discovered = loader.discover()
    except Exception as e:
        if log:
            log.log(f"discover FAIL: {e}")
        discovered = []

    if log:
        log.log(f"discover: {discovered}")

    for pid in discovered:
        if not app.is_plugin_enabled(pid):
            if log:
                log.log(f"🔌 skip (off): {pid}")
            continue
        try:
            pl = loader.load_one(pid)
            if pl is not None:
                plugins[getattr(pl, "id", pid)] = pl
        except Exception as e:
            if log:
                log.log(f"🔌 load FAIL {pid}: {e}")
                for line in traceback.format_exc().splitlines()[-5:]:
                    log.log("    " + line)
        # прокрутить Qt-очередь, чтобы таймеры не копились
        try:
            from PyQt5 import QtWidgets

            app_qt = QtWidgets.QApplication.instance()
            if app_qt is not None:
                app_qt.processEvents()
        except Exception:
            pass

    app.plugins = plugins
    if log:
        log.log(f"loaded: {sorted(plugins.keys())}")
    if not plugins:
        raise RuntimeError("ни один плагин не загрузился")
    return app, plugins


def run_phrase_on_plugin(pl, phrase: str, app) -> Tuple[str, str]:
    """on_user_message → optional on_before_llm. Возвращает (status, detail)."""
    from core.plugin_api import HookResult

    detail_parts = []
    try:
        hr = pl.on_user_message(phrase, app)
    except Exception as e:
        return "FAIL", f"on_user_message: {e}"

    if isinstance(hr, HookResult):
        if hr.handled:
            reply = hr.reply or ""
            # типичный баг response vs reply
            if not reply and getattr(hr, "response", None):
                return "FAIL", "HookResult.handled but empty reply (used response=?)"
            detail_parts.append(f"handled reply={str(reply)[:80]!r}")
        else:
            detail_parts.append("handled=False")
    elif hr is not None:
        detail_parts.append(f"return={type(hr).__name__}")

    # before_llm
    try:
        messages = [
            {"role": "system", "content": "test system"},
            {"role": "user", "content": phrase},
        ]
        out = pl.on_before_llm(messages, app)
        if not isinstance(out, list):
            return "FAIL", "on_before_llm did not return list"
        detail_parts.append("before_llm=ok")
    except Exception as e:
        return "FAIL", f"on_before_llm: {e}"

    # after_llm
    try:
        r = pl.on_after_llm("тестовый ответ ассистента", app)
        if r is None:
            return "WARN", "on_after_llm returned None; " + "; ".join(detail_parts)
        detail_parts.append("after_llm=ok")
    except Exception as e:
        return "FAIL", f"on_after_llm: {e}"

    return "OK", "; ".join(detail_parts)


def test_plugin_load(pl, app, log: Logger) -> bool:
    try:
        # already loaded; re-call on_load is optional — skip to avoid double timers
        schema = pl.get_settings_schema() if hasattr(pl, "get_settings_schema") else []
        log.log(
            f"  id={getattr(pl,'id','?')} name={getattr(pl,'name','?')} "
            f"ver={getattr(pl,'version','?')} schema={len(schema or [])}"
        )
        return True
    except Exception as e:
        log.log(f"  meta FAIL: {e}")
        return False


def test_llm_ping(log: Logger) -> bool:
    try:
        import config
        from core.llm_client import LLMClient
        import asyncio

        client = LLMClient.from_config(config)

        async def _ping():
            return await client.ping()

        ok = asyncio.run(_ping())
        log.log(f"LLM {config.API_URL}: {'online' if ok else 'offline'}")
        return bool(ok)
    except Exception as e:
        log.log(f"LLM ping ERR: {e}")
        return False


def test_browser_optional(log: Logger, enable: bool) -> None:
    if not enable:
        log.log("browser: skipped (--browser не указан)")
        return
    try:
        import webbrowser

        webbrowser.open("https://www.google.com/search?q=selftest+lisichka")
        log.log("browser: opened google search")
    except Exception as e:
        log.log(f"browser ERR: {e}")


def run_passes(
    app,
    plugins: Dict[str, Any],
    log: Logger,
    rounds: int,
    only: Optional[List[str]],
    browser: bool,
    llm: bool,
    quick: bool,
) -> None:
    ids = sorted(plugins.keys())
    if only:
        ids = [i for i in ids if i in only]

    log.log(f"Плагинов к тесту: {len(ids)} → {ids}")
    log.log(f"Проходов на плагин: {rounds}")

    for pass_n in range(1, rounds + 1):
        log.log("")
        log.log(f"========== ПРОХОД {pass_n}/{rounds} ==========")
        for pid in ids:
            pl = plugins[pid]
            log.log(f"--- plugin: {pid} ---")
            test_plugin_load(pl, app, log)
            phrases = list(PLUGIN_PHRASES.get(pid, ["привет"]))
            if quick:
                phrases = phrases[:2]
            # browser_search: реальный браузер только на 1-м проходе
            for phrase in phrases:
                if pid == "browser_search" and not browser:
                    log.result(pid, pass_n, phrase, "SKIP", "нужен --browser для реального поиска")
                    # всё равно дергаем on_user_message (может handled)
                if pid == "pc_control" and "калькулятор" in phrase and quick and pass_n > 1:
                    log.result(pid, pass_n, phrase, "SKIP", "quick skip second open")
                    continue
                status, detail = run_phrase_on_plugin(pl, phrase, app)
                if status == "OK" and "handled=False" in detail and pid in (
                    "auto_messages",
                    "voice",
                    "character_log",
                ):
                    detail = detail + " (passive ok)"
                log.result(pid, pass_n, phrase, status, detail)
                try:
                    from PyQt5 import QtWidgets

                    qapp = QtWidgets.QApplication.instance()
                    if qapp is not None:
                        qapp.processEvents()
                except Exception:
                    pass
                time.sleep(PAUSE)

        # глобальные фразы через все плагины по очереди (как chat_engine)
        log.log("--- pipeline all plugins ---")
        for phrase in GLOBAL_PHRASES:
            say = phrase
            log.log(f"👤 {say}")
            handled = False
            for pid, pl in plugins.items():
                if only and pid not in only:
                    continue
                try:
                    from core.plugin_api import HookResult

                    hr = pl.on_user_message(say, app)
                    if isinstance(hr, HookResult) and hr.handled:
                        log.log(f"  → {pid} handled: {str(hr.reply or '')[:100]}")
                        handled = True
                        break
                except Exception as e:
                    log.log(f"  → {pid} ERR: {e}")
                    log.fail += 1
            if not handled:
                log.log("  → никто не handled (уйдёт в LLM)")
            time.sleep(PAUSE)

        if llm and pass_n == 1:
            test_llm_ping(log)
        if browser and pass_n == 1:
            test_browser_optional(log, True)


def print_summary(log: Logger) -> int:
    log.log("")
    log.log("========== ИТОГ ==========")
    log.log(f"OK={log.ok}  FAIL={log.fail}  SKIP/WARN={log.skip}")
    # сводка по плагинам
    by: Dict[str, Dict[str, int]] = {}
    for r in log.rows:
        b = by.setdefault(r["plugin"], {"OK": 0, "FAIL": 0, "SKIP": 0, "WARN": 0})
        b[r["status"]] = b.get(r["status"], 0) + 1
    for pid, st in sorted(by.items()):
        log.log(f"  {pid}: {st}")
    log.log(f"Лог: {LOG_MAIN}")
    log.log(f"Лог (human): {LOG_HUMAN}")
    return 1 if log.fail else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Selftest плагинов asistent3")
    ap.add_argument("--rounds", type=int, default=2, help="Число проходов на каждый плагин (default 2)")
    ap.add_argument("--plugin", action="append", default=[], help="Только эти id (можно несколько)")
    ap.add_argument("--browser", action="store_true", help="Реальный браузер/поиск")
    ap.add_argument("--llm", action="store_true", help="Пинг LLM API")
    ap.add_argument("--quick", action="store_true", help="Укороченные списки фраз")
    ap.add_argument("--list", action="store_true", help="Только список плагинов")
    args = ap.parse_args(argv)

    log = Logger(LOG_MAIN, LOG_HUMAN)
    log.log("=" * 60)
    log.log("asistent3 plugin selftest")
    log.log(f"cwd={os.getcwd()}")
    log.log(f"root={ROOT}")
    log.log(f"rounds={args.rounds} browser={args.browser} llm={args.llm} quick={args.quick}")
    log.log("=" * 60)

    try:
        app, plugins = build_app(log)
    except Exception as e:
        log.log(f"FATAL load: {e}")
        log.log(traceback.format_exc())
        return 2

    log.log(f"ACTIVE_CHARACTER={getattr(app.config, 'ACTIVE_CHARACTER', '?')}")
    log.log(f"loaded plugins: {sorted(plugins.keys())}")

    if args.list:
        for pid, pl in sorted(plugins.items()):
            log.log(f"  - {pid}: {getattr(pl,'name',pid)} v{getattr(pl,'version','?')}")
        return 0

    only = args.plugin or None
    try:
        run_passes(
            app,
            plugins,
            log,
            rounds=max(1, int(args.rounds)),
            only=only,
            browser=args.browser,
            llm=args.llm,
            quick=args.quick,
        )
    except Exception as e:
        log.log(f"FATAL run: {e}")
        log.log(traceback.format_exc())
        return 2
    finally:
        # shutdown plugins
        for pl in list(plugins.values()):
            try:
                pl.on_shutdown(app)
            except Exception:
                pass

    return print_summary(log)


if __name__ == "__main__":
    sys.exit(main())
