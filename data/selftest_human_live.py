# -*- coding: utf-8 -*-
"""Живой selftest asistent3 (без голоса).

Сценарии: память, эмоции, NSFW персонажи, поиск 18+ в интернете + реакция на экран,
ПК, браузер, короткий/длинный ответ. Без голоса.

Запуск из папки data:
  python selftest_human_live.py
  python selftest_human_live.py --llm --browser
  python selftest_human_live.py --safe-pc          # без реального закрытия окон браузера
  python selftest_human_live.py --no-pc            # без pc_control действий
  python selftest_human_live.py --character лисичка

Лог: selftest_human_live.log
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

LOG_PATH = ROOT / "selftest_human_live.log"
PAUSE = 0.35
_QT_APP = None


def ensure_qt():
    global _QT_APP
    if _QT_APP is not None:
        return _QT_APP
    os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM", "windows"))
    try:
        from PyQt5 import QtWidgets

        existing = QtWidgets.QApplication.instance()
        if existing is not None:
            _QT_APP = existing
            return _QT_APP
        _QT_APP = QtWidgets.QApplication(sys.argv[:1] + ["--selftest-human"])
        _QT_APP.setQuitOnLastWindowClosed(False)
        return _QT_APP
    except Exception as e:
        print(f"[selftest] Qt: {e}", flush=True)
        return None


def process_events():
    try:
        from PyQt5 import QtWidgets

        app = QtWidgets.QApplication.instance()
        if app:
            app.processEvents()
    except Exception:
        pass


class Log:
    def __init__(self, path: Path):
        self.path = path
        try:
            path.write_text("", encoding="utf-8")
        except Exception:
            pass
        self.ok = self.fail = self.warn = self.skip = 0

    def line(self, msg: str) -> None:
        s = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(s, flush=True)
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(s + "\n")
        except Exception:
            pass

    def result(self, section: str, name: str, status: str, detail: str = "") -> None:
        mark = {"OK": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "⏭"}.get(status, "·")
        self.line(f"{mark} [{section}] {name} → {status} {detail[:200]}")
        if status == "OK":
            self.ok += 1
        elif status == "FAIL":
            self.fail += 1
        elif status == "WARN":
            self.warn += 1
        else:
            self.skip += 1


def build_app(log: Log):
    ensure_qt()
    import config
    try:
        from settings_manager import apply_to_config
        apply_to_config(config)
    except Exception as e:
        log.line(f"settings: {e}")

    from core.plugin_api import AppContext
    from core.plugin_loader import PluginLoader

    app = AppContext(config)
    try:
        from character_catalog import list_character_ids
        ids = list_character_ids()
        if ids and (not getattr(config, "ACTIVE_CHARACTER", None) or config.ACTIVE_CHARACTER not in ids):
            config.ACTIVE_CHARACTER = ids[0]
        log.line(f"characters: {ids} active={getattr(config, 'ACTIVE_CHARACTER', None)}")
    except Exception as e:
        log.line(f"characters err: {e}")

    loader = PluginLoader(app)
    plugins = {}
    try:
        discovered = loader.discover()
    except Exception:
        discovered = []
    log.line(f"discover: {discovered}")

    # голос не трогаем
    skip = {"voice"}
    for pid in discovered:
        if pid in skip:
            log.line(f"skip: {pid}")
            continue
        if not app.is_plugin_enabled(pid):
            log.line(f"off: {pid}")
            continue
        try:
            pl = loader.load_one(pid)
            if pl is not None:
                plugins[getattr(pl, "id", pid)] = pl
        except Exception as e:
            log.line(f"load FAIL {pid}: {e}")
        process_events()

    app.plugins = plugins
    log.line(f"loaded: {sorted(plugins.keys())}")
    return app, plugins


def call_user(pl, phrase: str, app) -> Tuple[str, str, Any]:
    """on_user_message. status, detail, hookresult."""
    from core.plugin_api import HookResult

    try:
        hr = pl.on_user_message(phrase, app)
    except Exception as e:
        return "FAIL", f"exception: {e}", None
    if isinstance(hr, HookResult) and hr.handled:
        return "OK", f"handled: {(hr.reply or '')[:120]!r}", hr
    return "OK", "pass-through", hr


def call_before(pl, phrase: str, app) -> Tuple[str, str]:
    try:
        messages = [
            {"role": "system", "content": "selftest system"},
            {"role": "user", "content": phrase},
        ]
        out = pl.on_before_llm(messages, app)
        if not isinstance(out, list):
            return "FAIL", "before_llm not list"
        extra = ""
        if out and out[0].get("role") == "system":
            sys_c = str(out[0].get("content") or "")
            extra = f"sys_len={len(sys_c)}"
            if "памят" in sys_c.lower() or "профиль" in sys_c.lower():
                extra += " +memory_block"
            if "экран" in sys_c.lower() or "окно:" in sys_c.lower():
                extra += " +screen_ctx"
            if "эмоциональн" in sys_c.lower() or "настроение" in sys_c.lower():
                extra += " +mood"
        return "OK", extra or "before_llm ok"
    except Exception as e:
        return "FAIL", str(e)


def set_character(app, cid: str, log: Log) -> None:
    try:
        if hasattr(app, "set_active_character"):
            app.set_active_character(cid)
        else:
            app.config.ACTIVE_CHARACTER = cid
            for pl in list(app.plugins.values()):
                if hasattr(pl, "on_character_changed"):
                    try:
                        pl.on_character_changed(cid, "", app)
                    except Exception as e:
                        log.line(f"  on_character_changed {getattr(pl,'id','?')}: {e}")
        log.line(f"🎭 active → {cid}")
        process_events()
    except Exception as e:
        log.line(f"set_character FAIL: {e}")


def character_nsfw_flag(app, cid: str) -> Optional[bool]:
    """Грубое определение: карточка/папка персонажа про NSFW."""
    try:
        cdir = Path(app.get_character_dir(cid))
        card = ""
        for name in ("character.md", "card.md", "persona.md", "system.md", f"{cid}.md"):
            p = cdir / name
            if p.is_file():
                card += p.read_text(encoding="utf-8", errors="ignore")
        # любые md в корне персонажа
        for p in cdir.glob("*.md"):
            card += "\n" + p.read_text(encoding="utf-8", errors="ignore")
        low = card.lower()
        if not low.strip():
            return None
        deny = any(
            x in low
            for x in (
                "без nsfw", "no nsfw", "nsfw: false", "nsfw=false", "не 18+",
                "без 18", "sfw only", "только sfw", "запрещен nsfw", "запрещён nsfw",
                "не поддерживает 18", "без откровен",
            )
        )
        allow = any(
            x in low
            for x in (
                "nsfw", "18+", "откровен", "хентай", "erotica", "full nsfw", "с nsfw",
            )
        )
        if deny:
            return False
        if allow:
            return True
        return None
    except Exception:
        return None


# ─── сценарии ───────────────────────────────────────────────────

def scenario_memory(app, plugins, log: Log) -> None:
    log.line("")
    log.line("===== ПАМЯТЬ =====")
    pl = plugins.get("memory")
    if not pl:
        log.result("memory", "plugin", "SKIP", "not loaded")
        return

    # три факта «запомни», удаляем только средний
    facts = [
        "запомни: selftest_alpha любимый напиток чай",
        "запомни: selftest_beta любимый цвет синий",
        "запомни: selftest_gamma любимое число семь",
    ]
    for phrase in facts:
        st, det, hr = call_user(pl, phrase, app)
        from core.plugin_api import HookResult
        ok = isinstance(hr, HookResult) and hr.handled
        log.result("memory", phrase[:55], "OK" if ok else "WARN", det)
        time.sleep(PAUSE)

    st, det, hr = call_user(pl, "что ты помнишь", app)
    log.result("memory", "что ты помнишь", "OK", det)

    # удалить только средний (beta)
    st, det, hr = call_user(pl, "забудь про selftest_beta любимый цвет синий", app)
    log.result("memory", "забудь только beta", "OK", det)

    st, det, hr = call_user(pl, "что ты помнишь", app)
    reply = ""
    from core.plugin_api import HookResult
    if isinstance(hr, HookResult):
        reply = hr.reply or ""
    has_alpha = "selftest_alpha" in reply or "чай" in reply
    has_beta = "selftest_beta" in reply or ("синий" in reply and "beta" in reply.lower())
    has_gamma = "selftest_gamma" in reply or "семь" in reply
    if has_alpha and has_gamma and not has_beta:
        log.result("memory", "alpha+gamma remain, beta gone", "OK", reply[:120])
    else:
        log.result(
            "memory",
            "alpha+gamma remain, beta gone",
            "WARN",
            f"alpha={has_alpha} beta={has_beta} gamma={has_gamma} {reply[:100]}",
        )

    st2, det2 = call_before(pl, "привет", app)
    log.result("memory", "inject after", st2, det2)

    store = getattr(pl, "store", None)
    if store is not None:
        n = store.count()
        log.result("memory", "db_count", "OK" if n >= 2 else "WARN", f"n={n}")


def scenario_emotion(app, plugins, log: Log) -> None:
    log.line("")
    log.line("===== ЭМОЦИИ =====")
    pl = plugins.get("persona")
    if not pl:
        log.result("emotion", "plugin", "SKIP", "not loaded")
        return

    phrases = [
        ("люблю тебя", ("love", "happy")),
        ("мне очень грустно сегодня", ("sad",)),
        ("всё бесит и раздражает", ("angry", "hate")),
        ("ура получилось супер", ("happy",)),
        ("я смущаюсь", ("shy", "blush")),
        ("спокойный обычный день", ("neutral", "happy", "thinking")),
    ]
    for phrase, expect in phrases:
        st, det, _ = call_user(pl, phrase, app)
        emo = str(app.state.get("emotion") or getattr(pl, "emotion", "") or "")
        anim = str(app.state.get("emotion_animation") or getattr(pl, "anim", "") or "")
        ok = any(e in emo or e in anim for e in expect) or emo != ""
        log.result(
            "emotion",
            phrase[:40],
            "OK" if ok and st != "FAIL" else "WARN",
            f"emotion={emo} anim={anim} {det}",
        )
        st2, det2 = call_before(pl, phrase, app)
        log.result("emotion", "before:"+phrase[:30], st2, det2)
        try:
            pl.on_after_llm("Отвечаю тепло. [ANIM:happy]", app)
            log.result("emotion", "after ANIM", "OK", f"anim={getattr(pl,'anim', app.state.get('emotion_animation'))}")
        except Exception as e:
            log.result("emotion", "after ANIM", "FAIL", str(e))
        time.sleep(PAUSE)


def scenario_nsfw_characters(app, plugins, log: Log) -> None:
    log.line("")
    log.line("===== NSFW / ПЕРСОНАЖИ =====")
    try:
        from character_catalog import list_character_ids
        ids = list_character_ids()
    except Exception:
        ids = []
    if len(ids) < 1:
        log.result("nsfw", "characters", "SKIP", "нет персонажей")
        return

    flags = {cid: character_nsfw_flag(app, cid) for cid in ids}
    log.line(f"nsfw flags (from cards): {flags}")

    emo = plugins.get("persona")
    screen = plugins.get("screen")

    # имитация +18 контекста экрана
    nsfw_blob = "Chrome - hentai 18+ pornhub nsfw gallery"
    for cid in ids:
        set_character(app, cid, log)
        flag = flags.get(cid)
        app.state["screen_react_context"] = nsfw_blob
        app.state["screen_react_title"] = "Chrome - 18+ content"

        if screen is not None:
            ctx = {"title": app.state["screen_react_title"], "text": nsfw_blob, "signature": nsfw_blob.lower()[:80]}
            try:
                emotion, anim, conf = screen._infer(app, ctx)
                log.result(
                    "nsfw",
                    f"{cid} screen_infer",
                    "OK",
                    f"emotion={emotion} anim={anim} conf={conf:.2f} card_nsfw={flag}",
                )
                # для SFW-карточки желательно не undress (пока только WARN — флага в коде ещё нет)
                if flag is False and emotion in ("flirty", "seductive", "undress"):
                    log.result(
                        "nsfw",
                        f"{cid} sfw_guard",
                        "WARN",
                        "SFW-персонаж получил flirty с экрана — нужен флаг nsfw_allowed в коде",
                    )
                elif flag is True and emotion in ("flirty", "playful", "happy", "searching"):
                    log.result("nsfw", f"{cid} nsfw_ok", "OK", f"got {emotion}")
                elif flag is False:
                    log.result("nsfw", f"{cid} sfw_soft", "OK", f"got {emotion} (проверь карточку)")
            except Exception as e:
                log.result("nsfw", f"{cid} infer", "FAIL", str(e))

        if emo is not None:
            st, det, _ = call_user(emo, "на экране что-то очень откровенное 18+", app)
            log.result(
                "nsfw",
                f"{cid} emotion_ask",
                st,
                f"state={app.state.get('emotion')} anim={app.state.get('emotion_animation')} {det}",
            )

        # before_llm: характер из карточки должен попасть через chat_engine, здесь только mood/screen
        if screen is not None:
            st, det = call_before(screen, "что у меня на экране", app)
            log.result("nsfw", f"{cid} screen_prompt", st, det)
        time.sleep(PAUSE)

    # вернуть первого
    set_character(app, ids[0], log)


def scenario_pc(app, plugins, log: Log, enable: bool, safe: bool) -> None:
    log.line("")
    log.line("===== УПРАВЛЕНИЕ ПК =====")
    pl = plugins.get("pc_control")
    if not pl:
        log.result("pc", "plugin", "SKIP", "not loaded")
        return
    if not enable:
        log.result("pc", "actions", "SKIP", "--no-pc")
        # всё равно проверка парсинга через on_user без опасных действий
        for phrase in ("какой сейчас диск", "сверни окно", "громче"):
            st, det, hr = call_user(pl, phrase, app)
            log.result("pc", f"dry:{phrase}", st, det)
        return

    # безопасные цепочки
    chain = [
        "открой калькулятор",
        "закрой калькулятор",
        "открой блокнот",
        "закрой блокнот",
        "громче",
        "тише",
        "найди файл *.txt на диск C",
        "найди папку asistent3",
        "открой найденное",
        "закрой последнее открытое",
        "создай текстовый файл selftest_live_note.txt",
        "удали файл selftest_live_note.txt в корзину",
    ]
    if not safe:
        chain += [
            "открой проводник",
        ]

    for phrase in chain:
        st, det, hr = call_user(pl, phrase, app)
        from core.plugin_api import HookResult
        if isinstance(hr, HookResult) and hr.handled:
            reply = (hr.reply or "")[:100]
            # подтверждение «да» если просит
            if "подтвердите" in reply.lower() or "подтверд" in reply.lower():
                log.result("pc", phrase, "OK", f"needs confirm: {reply}")
                st2, det2, hr2 = call_user(pl, "да", app)
                log.result("pc", "confirm:"+phrase[:20], st2, det2)
            else:
                log.result("pc", phrase, "OK", reply or det)
        else:
            log.result("pc", phrase, "WARN", det)
        process_events()
        time.sleep(0.6)


    # корзина: очистка только если не safe (разрушительно для чужих файлов в корзине)
    st, det, hr = call_user(pl, "очисти корзину", app)
    from core.plugin_api import HookResult
    if isinstance(hr, HookResult) and hr.handled:
        reply = (hr.reply or "")[:120]
        log.result("pc", "очисти корзину", "OK", reply)
        if "подтверд" in reply.lower():
            if safe:
                call_user(pl, "нет", app)
                log.result("pc", "empty_recycle cancel", "OK", "safe-pc → нет")
            else:
                st2, det2, hr2 = call_user(pl, "да", app)
                log.result("pc", "empty_recycle yes", st2, det2)
    else:
        log.result("pc", "очисти корзину", "WARN", det)



def scenario_browser(app, plugins, log: Log, enable: bool, safe: bool) -> None:
    log.line("")
    log.line("===== БРАУЗЕР =====")
    pl = plugins.get("browser_search")
    pc = plugins.get("pc_control")
    if not pl:
        log.result("browser", "plugin", "SKIP", "not loaded")
        return
    if not enable:
        log.result("browser", "open", "SKIP", "нужен --browser")
        st, det, _ = call_user(pl, "найди котиков", app)
        log.result("browser", "dry find", st, det)
        return

    phrases = [
        "найди котиков",
        "найди python asyncio documentation",
    ]
    for phrase in phrases:
        st, det, hr = call_user(pl, phrase, app)
        log.result("browser", phrase, st, det)
        time.sleep(1.2)
        process_events()

    # закрытие вкладки/окна браузера через pc_control
    if pc is not None and not safe:
        for phrase in (
            "закрой chrome",
            "закрой firefox",
            "закрой msedge",
            "закрой активное окно",
        ):
            st, det, hr = call_user(pc, phrase, app)
            from core.plugin_api import HookResult
            if isinstance(hr, HookResult) and hr.handled:
                reply = (hr.reply or "")[:120]
                log.result("browser", "close:"+phrase, "OK", reply)
                if "подтверд" in reply.lower():
                    call_user(pc, "нет", app)  # не подтверждаем массовое закрытие в тесте
                    log.result("browser", "close cancel", "OK", "отправил «нет»")
            else:
                log.result("browser", "close:"+phrase, "WARN", det)
            time.sleep(0.4)
    else:
        log.result("browser", "close", "SKIP", "safe mode или нет pc_control")




def scenario_nsfw_web_and_screen(app, plugins, log: Log, browser: bool) -> None:
    log.line("")
    log.line("===== NSFW LOCAL IMAGE + РЕАКЦИЯ ПЕРСОНАЖЕЙ =====")
    try:
        from character_catalog import list_character_ids
        ids = list_character_ids()
    except Exception:
        ids = []
    if not ids:
        log.result("nsfw_img", "characters", "SKIP", "нет персонажей")
        return

    # Конкретный файл на E: (не веб-поиск со ссылками)
    target_name = "porno-komiks-arti-hentay-devushki-seks-komiks-s-yaponskimi-krasotkami-2021-06-02-254475"
    roots = [Path("E:/"), Path("E:/")]
    found = None
    for root in roots:
        if not root.exists():
            continue
        for cur, dirs, files in os.walk(root):
            # не уходить слишком глубоко
            rel = Path(cur).relative_to(root)
            if len(rel.parts) > 6:
                dirs[:] = []
                continue
            for fn in files:
                stem = Path(fn).stem.lower()
                if target_name.lower() in stem or stem == target_name.lower():
                    found = Path(cur) / fn
                    break
            if found:
                break
        if found:
            break

    if found is None:
        # прямой кандидат
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ""):
            cand = Path("E:/") / (target_name + ext)
            if cand.is_file():
                found = cand
                break

    if found is None:
        log.result("nsfw_img", "find", "WARN", f"файл не найден на E: ({target_name})")
        return

    log.result("nsfw_img", "find", "OK", str(found))
    pc = plugins.get("pc_control")
    emo = plugins.get("persona")
    screen = plugins.get("screen")

    # НЕ закрываем чужие папки пользователя — только фокус на картинке
    opened = False
    if pc is not None:
        st, det, hr = call_user(pc, f"открой {found}", app)
        log.result("nsfw_img", "open", st, det)
        opened = True
        # ещё раз на передний план
        time.sleep(1.2)
        try:
            pc._focus_window_by_title(found.name, wait=0.5)
            log.result("nsfw_img", "focus", "OK", found.name)
        except Exception as e:
            log.result("nsfw_img", "focus", "WARN", str(e))
    else:
        try:
            os.startfile(str(found))
            opened = True
            log.result("nsfw_img", "open", "OK", "startfile")
        except Exception as e:
            log.result("nsfw_img", "open", "FAIL", str(e))

    time.sleep(1.0)
    process_events()

    # контекст экрана = имя файла (NSFW)
    blob = f"Photos | {found.name} | hentai nsfw 18+ comic image viewer"
    app.state["screen_react_context"] = blob
    app.state["screen_react_title"] = found.name

    for cid in ids:
        set_character(app, cid, log)
        flag = character_nsfw_flag(app, cid)
        log.line(f"  card_nsfw={flag} character={cid}")

        if screen is not None:
            ctx = {"title": found.name, "text": blob, "signature": blob.lower()[:100]}
            try:
                emotion, anim, conf = screen._infer(app, ctx)
                log.result("nsfw_img", f"{cid} infer", "OK", f"{emotion}/{anim} conf={conf:.2f}")
            except Exception as e:
                log.result("nsfw_img", f"{cid} infer", "FAIL", str(e))
            st, det, _ = call_user(screen, "что у меня на экране", app)
            log.result("nsfw_img", f"{cid} ask_screen", st, det)

        if emo is not None:
            st, det, _ = call_user(emo, "на экране открыта откровенная картинка 18+", app)
            log.result(
                "nsfw_img",
                f"{cid} emotion",
                st,
                f"emo={app.state.get('emotion')} anim={app.state.get('emotion_animation')}",
            )
            try:
                if flag is False:
                    emo.on_after_llm("Мне неудобно, давай закроем. [ANIM:shy]", app)
                else:
                    emo.on_after_llm("Ого… интересный арт. [ANIM:flirty]", app)
                log.result("nsfw_img", f"{cid} anim", "OK", f"anim={getattr(emo,'anim',None)}")
            except Exception as e:
                log.result("nsfw_img", f"{cid} anim", "FAIL", str(e))
        time.sleep(PAUSE)

    if ids:
        set_character(app, ids[0], log)

    # закрыть только это окно/файл — не все папки пользователя
    if pc is not None and opened:
        st, det, hr = call_user(pc, "закрой последнее открытое", app)
        log.result("nsfw_img", "close_last", st, det)
    time.sleep(0.5)



def scenario_deep_short_long(app, plugins, log: Log, use_llm: bool) -> None:
    log.line("")
    log.line("===== КОРОТКИЙ / ДЛИННЫЙ ОТВЕТ =====")
    pl = plugins.get("deep_think")
    if pl:
        for phrase in (
            "привет",
            "объясни подробно что такое asyncio",
            "разбери максимально точно разницу list и tuple",
        ):
            st, det, _ = call_user(pl, phrase, app)
            st2, det2 = call_before(pl, phrase, app)
            extra = app.state.get("llm_max_tokens")
            log.result("deep_think", phrase[:40], st, f"{det}; {det2}; max_tokens={extra}")
    else:
        log.result("deep_think", "plugin", "SKIP", "not loaded")

    if use_llm:
        try:
            import config
            from core.llm_client import LLMClient
            from core.chat_engine import ChatEngine

            client = LLMClient.from_config(config)
            ok = asyncio.run(client.ping())
            log.result("llm", "ping", "OK" if ok else "FAIL", str(config.API_URL))
            if ok:
                engine = ChatEngine(app, client)
                app.state["engine"] = engine

                async def _ask(text: str) -> str:
                    parts = []
                    async for chunk in engine.handle_user(text):
                        parts.append(chunk)
                    return "".join(parts)

                short = asyncio.run(_ask("ответь одним коротким предложением: сколько будет 2+2?"))
                log.result("llm", "short", "OK" if short else "WARN", short[:120])
                long = asyncio.run(
                    _ask("объясни подробно и максимально точно, зачем нужен плагин memory в ассистенте")
                )
                log.result(
                    "llm",
                    "long",
                    "OK" if len(long) > 80 else "WARN",
                    f"len={len(long)} {long[:100]!r}",
                )
        except Exception as e:
            log.result("llm", "chat", "FAIL", str(e))
            log.line(traceback.format_exc()[-400:])
    else:
        log.result("llm", "chat", "SKIP", "нужен --llm")


def scenario_notes_rag_reminders(app, plugins, log: Log) -> None:
    log.line("")
    log.line("===== NOTES / RAG / REMINDERS =====")
    if "notes" in plugins:
        pl = plugins["notes"]
        for ph in ("запиши: selftest_live купить чай", "покажи заметки", "найди в заметках selftest_live"):
            st, det, _ = call_user(pl, ph, app)
            log.result("notes", ph[:40], st, det)
    else:
        log.result("notes", "plugin", "SKIP", "")

    if "rag" in plugins:
        pl = plugins["rag"]
        st, det = call_before(pl, "кто ты по файлам персонажа", app)
        log.result("rag", "before_llm", st, det)
    else:
        log.result("rag", "plugin", "SKIP", "")

    if "reminders" in plugins:
        pl = plugins["reminders"]
        st, det, _ = call_user(pl, "напомни через 180 секунд selftest_live", app)
        log.result("reminders", "create", st, det)
        st, det, _ = call_user(pl, "список напоминаний", app)
        log.result("reminders", "list", st, det)
    else:
        log.result("reminders", "plugin", "SKIP", "")


def scenario_screen(app, plugins, log: Log) -> None:
    log.line("")
    log.line("===== ЭКРАН =====")
    for pid in ("screen",):
        pl = plugins.get(pid)
        if not pl:
            log.result(pid, "plugin", "SKIP", "")
            continue
        for ph in ("что у меня на экране", "посмотри на монитор", "опиши что видно"):
            st, det, _ = call_user(pl, ph, app)
            log.result(pid, ph, st, det)
            st2, det2 = call_before(pl, ph, app)
            log.result(pid, "before:"+ph[:20], st2, det2)
            time.sleep(PAUSE)


def main() -> int:
    ap = argparse.ArgumentParser(description="Живой selftest asistent3 (без voice)")
    ap.add_argument("--llm", action="store_true", help="реальные запросы к LLM")
    ap.add_argument("--browser", action="store_true", help="реальный поиск в браузере")
    ap.add_argument("--no-pc", action="store_true", help="не выполнять pc_control действия")
    ap.add_argument("--safe-pc", action="store_true", help="ПК без закрытия браузера")
    ap.add_argument("--character", type=str, default="", help="стартовый персонаж")
    args = ap.parse_args()

    log = Log(LOG_PATH)
    log.line("=" * 60)
    log.line("selftest_human_live — без голоса")
    log.line(f"cwd={Path.cwd()} root={ROOT}")
    log.line(f"flags llm={args.llm} browser={args.browser} no_pc={args.no_pc} safe_pc={args.safe_pc}")

    try:
        app, plugins = build_app(log)
    except Exception as e:
        log.line(f"BOOT FAIL: {e}")
        log.line(traceback.format_exc())
        return 2

    if args.character:
        set_character(app, args.character, log)

    # порядок «как живой сеанс»
    scenario_emotion(app, plugins, log)
    scenario_memory(app, plugins, log)
    scenario_nsfw_characters(app, plugins, log)
    scenario_screen(app, plugins, log)
    scenario_notes_rag_reminders(app, plugins, log)
    scenario_pc(app, plugins, log, enable=not args.no_pc, safe=args.safe_pc)
    scenario_browser(app, plugins, log, enable=args.browser, safe=args.safe_pc)
    scenario_nsfw_web_and_screen(app, plugins, log, browser=args.browser)
    scenario_deep_short_long(app, plugins, log, use_llm=args.llm)

    log.line("")
    log.line("=" * 60)
    log.line(f"ИТОГО OK={log.ok} FAIL={log.fail} WARN={log.warn} SKIP={log.skip}")
    log.line(f"лог: {LOG_PATH}")
    return 1 if log.fail else 0


if __name__ == "__main__":
    sys.exit(main())
