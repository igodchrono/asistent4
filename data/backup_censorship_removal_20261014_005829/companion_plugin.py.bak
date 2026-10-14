# -*- coding: utf-8 -*-
"""
Companion — этапы 1–3 «живого помощника»:
  1) время + mood + профиль в каждом LLM-запросе
  2) сцена экрана (code/movie/nsfw/writing/browsing/idle) + реакция
  3) паттерны пользователя + редкие предложения (cooldown)
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.plugin_api import AppContext, Plugin, SettingField

_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

_SCENE_HINTS = {
    "coding": (
        "code", "visual studio", "vscode", "pycharm", "idea", "cursor", "sublime",
        "terminal", "cmd.exe", "powershell", "windows terminal", "git", "docker",
        "stackoverflow", "github", ".py", "debug",
    ),
    "writing": (
        "word", "docs", "notepad", "блокнот", "obsidian", "notion", "typora",
        "writer", "google docs", "libreoffice",
    ),
    "movie": (
        "youtube", "netflix", "kino", "кино", "vlc", "mpv", "potplayer", "plex",
        "prime video", "ivI", "фильм", "сериал", "twitch",
    ),
    "nsfw": (
        "porno", "porn", "xvideos", "xnxx", "hentai", "18+", "nhentai", "rule34",
        "xxx", "sex", "nsfw",
    ),
    "browsing": (
        "chrome", "firefox", "edge", "opera", "brave", "yandex", "mozilla",
    ),
    "chat": (
        "telegram", "discord", "whatsapp", "slack", "teams", "zoom",
    ),
}


class PluginImpl(Plugin):
    id = "companion"
    name = "Живой компаньон"
    version = "1.2.0"
    description = "Время, одно настроение, сцена экрана и реплики в чат"
    settings_tab = "own"
    settings_tab_title = "Компаньон"
    settings_schema = [
        SettingField("enabled", "Включить", "bool", True),
        SettingField("scene_interval_sec", "Проверка экрана (сек)", "int", 45, min_value=15, max_value=300),
        SettingField("suggest_cooldown_min", "Пауза между предложениями (мин)", "int", 6, min_value=1, max_value=120),
        SettingField("suggest_chance", "Шанс предложения %", "int", 70, min_value=0, max_value=100),
        SettingField("mood_drift", "Смена настроения со временем", "bool", True),
        SettingField("proactive", "Проактивные реплики по сцене", "bool", True),
        SettingField("comment_browsing", "Комментировать браузер тоже", "bool", True),
        SettingField("use_llm_comments", "Текст реплики через LLM", "bool", True),
        SettingField("debug_log", "Лог пропусков в консоль", "bool", True),
    ]

    def __init__(self) -> None:
        self._timer = None
        self._last_suggest_at = 0.0
        self._last_scene = "idle"
        self._last_scene_at = 0.0
        self._last_comment_key = ""
        self._patterns: Dict[str, int] = {}
        self.app: Optional[AppContext] = None

    def on_load(self, app: AppContext) -> None:
        self.app = app
        self._load_patterns(app)
        self._tick_time(app)
        self._ensure_mood(app)
        try:
            from PyQt5 import QtCore
            self._timer = QtCore.QTimer()
            sec = int(app.get_plugin_setting(self.id, "scene_interval_sec", 45) or 45)
            self._timer.setInterval(max(15, sec) * 1000)
            self._timer.timeout.connect(lambda: self._on_tick(app))
            self._timer.start()
        except Exception as e:
            print(f"companion: timer {e}", flush=True)
        print("companion 1.3: comment on real scene change", flush=True)

    def on_shutdown(self, app: AppContext) -> None:
        if self._timer is not None:
            try:
                self._timer.stop()
            except Exception:
                pass
        self._save_patterns(app)

    def on_character_changed(self, character_id: str, previous_id: str, app: AppContext) -> None:
        # настроение сбрасываем мягко при смене персонажа
        app.state["companion_mood"] = "curious"
        app.state["companion_mood_energy"] = 0.6
        self._load_patterns(app)

    def on_user_message(self, text: str, app: AppContext):
        if not app.get_plugin_setting(self.id, "enabled", True):
            return None
        self._tick_time(app)
        self._note_pattern(app, text or "")
        # лёгкий сдвиг настроения от тона
        low = (text or "").lower()
        if any(w in low for w in ("спасибо", "молодец", "любим", "рада", "класс", "умница", "милая")):
            self._set_mood(app, "happy", 0.8, sprite=True, source="chat")
        elif any(w in low for w in ("дур", "туп", "бесит", "заткни", "достал")):
            self._set_mood(app, "annoyed", 0.7, sprite=True, source="chat")
        elif any(w in low for w in ("скуч", "груст", "плохо", "устал")):
            self._set_mood(app, "sad", 0.55, sprite=True, source="chat")
        elif any(w in low for w in ("хаха", "ахах", "смешн", "прикол", "танцуй", "потанцуй")):
            self._set_mood(app, "playful", 0.8, sprite=True, source="chat")
        elif any(w in low for w in ("спокойн", "просто поговор", "обними")):
            self._set_mood(app, "calm", 0.55, sprite=True, source="chat")
        elif any(w in low for w in ("секс", "голая", "18+", "пошл", "хочу тебя", "раздень")):
            try:
                from core.mode import is_work
                work = is_work(app)
            except Exception:
                work = False
            if app.state.get("character_nsfw") and not work:
                self._set_mood(app, "flirty", 0.85, sprite=True, source="chat")
        return None

    def on_before_llm(self, messages: List[Dict[str, Any]], app: AppContext) -> List[Dict[str, Any]]:
        if not app.get_plugin_setting(self.id, "enabled", True):
            return messages
        if not messages:
            return messages
        self._tick_time(app)
        block = self._system_block(app)
        if messages[0].get("role") == "system":
            messages[0]["content"] = str(messages[0].get("content") or "") + "\n\n" + block
        else:
            messages.insert(0, {"role": "system", "content": block})
        return messages

    def on_after_llm(self, reply: str, app: AppContext) -> str:
        return reply

    # ---------- time / mood / profile block ----------
    def _tick_time(self, app: AppContext) -> None:
        now = datetime.now()
        app.state["local_time"] = now.strftime("%H:%M")
        app.state["local_date"] = now.strftime("%Y-%m-%d")
        app.state["local_weekday"] = _WEEKDAYS[now.weekday()]
        app.state["local_hour"] = now.hour
        if now.hour < 6:
            app.state["day_part"] = "ночь"
        elif now.hour < 12:
            app.state["day_part"] = "утро"
        elif now.hour < 18:
            app.state["day_part"] = "день"
        else:
            app.state["day_part"] = "вечер"

    def _ensure_mood(self, app: AppContext) -> None:
        if app.state.get("companion_mood"):
            return
        hour = int(app.state.get("local_hour") or datetime.now().hour)
        if hour < 7:
            mood, energy = "sleepy", 0.35
        elif hour < 11:
            mood, energy = "happy", 0.65
        elif hour < 18:
            mood, energy = "curious", 0.6
        elif hour < 22:
            mood, energy = "playful" if app.state.get("character_nsfw") else "calm", 0.7
        else:
            mood, energy = "calm", 0.45
        app.state["companion_mood"] = mood
        app.state["companion_mood_energy"] = energy
        app.state["companion_mood_at"] = time.time()
        app.state["emotion"] = mood

    def _set_mood(self, app: AppContext, mood: str, energy: Optional[float], sprite: bool = False, source: str = "screen") -> None:
        app.state["companion_mood"] = mood
        app.state["emotion"] = mood
        if energy is not None:
            app.state["companion_mood_energy"] = float(energy)
        app.state["companion_mood_at"] = time.time()
        if not sprite:
            return
        persona = app.plugins.get("persona") or app.state.get("emotion_plugin")
        if persona is not None and hasattr(persona, "set_context"):
            try:
                persona.set_context(app, mood, source)
            except Exception as e:
                print(f"companion: mood sprite {e}", flush=True)

    def _profile_lines(self, app: AppContext) -> List[str]:
        lines: List[str] = []
        # из memory store — факты с меткой профиль / важно
        mem = app.plugins.get("memory")
        store = getattr(mem, "store", None) if mem else None
        if store is not None:
            try:
                items = []
                if hasattr(store, "list_recent"):
                    items = store.list_recent(30) or []
                elif hasattr(store, "all"):
                    items = store.all() or []
                elif hasattr(store, "list"):
                    items = store.list(limit=30) or []
                for it in items:
                    text = it if isinstance(it, str) else (it.get("text") or it.get("content") or str(it))
                    t = str(text).strip()
                    low = t.lower()
                    if any(k in low for k in ("профиль:", "важно:", "пользовател", "меня зовут", "я люблю", "я не люблю", "нельзя", "можно")):
                        lines.append(t[:160])
                    if len(lines) >= 8:
                        break
            except Exception as e:
                print(f"companion: profile memory: {e}", flush=True)
        # явный state
        extra = app.state.get("user_profile_lines")
        if isinstance(extra, list):
            for t in extra[:5]:
                if t and str(t) not in lines:
                    lines.append(str(t)[:160])
        return lines

    def _system_block(self, app: AppContext) -> str:
        self._ensure_mood(app)
        mood = app.state.get("companion_mood", "calm")
        energy = app.state.get("companion_mood_energy", 0.5)
        scene = app.state.get("screen_scene", "idle")
        title = app.state.get("screen_react_title") or app.state.get("fg_title") or ""
        parts = [
            "[COMPANION]",
            f"Сейчас: {app.state.get('local_date')} {app.state.get('local_time')} ({app.state.get('local_weekday')}), {app.state.get('day_part')}.",
            f"Настроение персонажа: {mood} (energy={float(energy):.2f}). "
            "Держи это настроение в тоне и мимике, без упоминания служебных меток. "
            "Можно менять настроение по ходу разговора, если тема сдвинулась.",
            f"Сцена на экране: {scene}" + (f" | окно: {title[:80]}" if title else "") + ".",
        ]
        # подсказка по сцене
        hints = {
            "coding": "Пользователь, похоже, пишет код. Можно коротко помочь или похвалить удачное — но не мешать каждым сообщением.",
            "writing": "Пользователь пишет текст. Уместны правки стиля/структуры, если просят или очень явно нужно.",
            "movie": "Похоже, смотрит видео/фильм. Не спойлерить. Можно спросить впечатления.",
            "nsfw": "На экране 18+. Реагируй в характере карточки.",
            "browsing": "Браузер. Не открывай поиск повторно без запроса.",
            "chat": "Мессенджер. Будь краткой, если не зовут в диалог.",
            "idle": "Нейтральный рабочий стол.",
        }
        if scene in hints:
            parts.append(hints[scene])
        prof = self._profile_lines(app)
        if prof:
            parts.append("Профиль/важное о пользователе:")
            parts.extend(f"- {p}" for p in prof)
        pats = self._top_patterns(6)
        if pats:
            parts.append("Паттерны запросов (часто): " + ", ".join(pats))
        return "\n".join(parts)

    # ---------- screen scene ----------
    def _on_tick(self, app: AppContext) -> None:
        if not app.get_plugin_setting(self.id, "enabled", True):
            return
        self._tick_time(app)
        if app.get_plugin_setting(self.id, "mood_drift", True):
            self._mood_drift(app)
        scene, title, conf = self._classify_scene(app)
        prev = app.state.get("screen_scene")
        app.state["screen_scene"] = scene
        app.state["screen_react_title"] = title
        app.state["fg_title"] = title
        if scene != prev:
            print(f"companion: scene {prev} → {scene} conf={conf:.2f} «{title[:60]}»", flush=True)
            self._react_scene_mood(app, scene)
            self._last_scene = scene
            self._last_scene_at = time.time()
            app.state["companion_scene_changed_at"] = time.time()
        if app.get_plugin_setting(self.id, "proactive", True):
            self._maybe_suggest(app, scene, title, conf)

    @staticmethod
    def _own_title(title: str) -> bool:
        low = (title or "").lower()
        return any(k in low for k in ("лисич", "lisichka", "asistent"))

    def _classify_scene(self, app: AppContext) -> tuple:
        title = self._fg_title()
        if self._own_title(title):
            prev = str(app.state.get("screen_scene") or "idle")
            return prev, "", 0.0
        low = title.lower()
        for scene, keys in _SCENE_HINTS.items():
            if any(k in low for k in keys):
                conf = 0.82 if scene == "nsfw" else 0.7
                return scene, title, conf
        ctx = str(app.state.get("screen_react_context") or "")
        if ctx and not self._own_title(ctx):
            for scene, keys in _SCENE_HINTS.items():
                if any(k in ctx.lower() for k in keys):
                    return scene, title or ctx[:80], 0.6
        return "idle", title, 0.4

    def _react_scene_mood(self, app: AppContext, scene: str) -> None:
        if str(app.state.get("imggen_stage") or "idle") != "idle":
            return
        last = float(app.state.get("last_user_activity") or 0)
        if last and time.time() - last < 90:
            return
        persona = app.plugins.get("persona")
        if persona is not None and time.time() < float(getattr(persona, "_pose_lock_until", 0) or 0):
            return
        nsfw_ok = bool(app.state.get("character_nsfw"))
        try:
            from core.mode import is_work
            if is_work(app):
                nsfw_ok = False
        except Exception:
            pass
        if scene == "nsfw":
            self._set_mood(app, "flirty" if nsfw_ok else "curious", 0.8 if nsfw_ok else 0.6, sprite=True, source="screen")
        elif scene == "coding":
            self._set_mood(app, "thinking", 0.65, sprite=True, source="screen")
        elif scene == "movie":
            self._set_mood(app, "calm", 0.55, sprite=True, source="screen")
        elif scene == "writing":
            self._set_mood(app, "thinking", 0.6, sprite=True, source="screen")
        elif scene == "chat":
            self._set_mood(app, "happy", 0.6, sprite=True, source="screen")
        elif scene == "browsing":
            self._set_mood(app, "searching", 0.55, sprite=True, source="screen")

    def _dbg(self, app: AppContext, msg: str) -> None:
        if app.get_plugin_setting(self.id, "debug_log", True):
            print(f"companion: {msg}", flush=True)

    @staticmethod
    def _title_interesting(title: str) -> bool:
        t = (title or "").strip()
        if len(t) < 8:
            return False
        low = t.lower()
        bare = {
            "google chrome", "chrome", "mozilla firefox", "firefox",
            "microsoft edge", "edge", "opera", "brave", "проводник",
        }
        core = low
        for tail in (" - google chrome", " — google chrome", " - mozilla firefox",
                     " - microsoft edge", " - opera", " - brave"):
            if core.endswith(tail):
                core = core[: -len(tail)].strip()
        return core not in bare and low not in bare

    def _maybe_suggest(self, app: AppContext, scene: str, title: str, conf: float) -> None:
        if str(app.state.get("imggen_stage") or "idle") != "idle":
            self._dbg(app, "skip: generation")
            return
        window = getattr(app, "window", None) or app.state.get("gui")
        if window is None:
            self._dbg(app, "skip: no window")
            return
        if getattr(window, "_busy", False):
            self._dbg(app, "skip: chat busy")
            return
        last_user = float(app.state.get("last_user_activity") or 0)
        if last_user and time.time() - last_user < 20:
            self._dbg(app, "skip: user typing")
            return
        title_use = title
        scene_use = scene
        if not title or self._own_title(title):
            title_use = str(app.state.get("companion_last_foreign_title") or "")
            scene_use = str(app.state.get("companion_last_foreign_scene") or scene_use)
            if not title_use or scene_use in ("", "idle"):
                self._dbg(app, "skip: своё окно или пустой заголовок")
                return
        else:
            if self._title_interesting(title) or scene not in ("idle", "browsing"):
                app.state["companion_last_foreign_title"] = title
                app.state["companion_last_foreign_scene"] = scene
        if scene_use == "idle" and conf < 0.55:
            self._dbg(app, f"skip: low conf={conf:.2f}")
            return
        if not self._title_interesting(title_use) and scene_use in ("idle", "browsing", "chat"):
            self._dbg(app, f"skip: пустой заголовок «{title_use[:50]}» scene={scene_use}")
            return
        try:
            from core.mode import is_work
            work = is_work(app)
        except Exception:
            work = False
        if work and scene_use in ("nsfw", "movie"):
            self._dbg(app, "skip: work mode")
            return
        if scene_use == "browsing" and not app.get_plugin_setting(self.id, "comment_browsing", True):
            self._dbg(app, "skip: browsing off")
            return
        key = f"{scene_use}|{(title_use or '')[:80]}"
        if key == self._last_comment_key or key == getattr(self, "_comment_inflight", ""):
            return
        cd = int(app.get_plugin_setting(self.id, "suggest_cooldown_min", 6) or 6) * 60
        wait = time.time() - self._last_suggest_at
        if self._last_suggest_at and wait < cd:
            self._dbg(app, f"skip: cooldown {int(wait)}s < {cd}s")
            return
        text = self._suggest_text(app, scene_use, title_use)
        if text:
            self._mark_comment(app, key, text)

    def _suggest_text(self, app: AppContext, scene: str, title: str) -> str:
        static = self._static_line(scene, title)
        if not app.get_plugin_setting(self.id, "use_llm_comments", True):
            return static
        window = getattr(app, "window", None) or app.state.get("gui")
        engine = None
        if window is not None:
            engine = getattr(window, "engine", None)
        engine = engine or app.state.get("engine") or getattr(app, "engine", None)
        if engine is None or not hasattr(engine, "generate_proactive"):
            return static
        instruction = (
            "Одно короткое сообщение в чат про то, что сейчас на экране. "
            f"Сцена: {scene}. Заголовок окна: «{(title or '')[:120]}». "
            "Только то, что есть в заголовке. Одно предложение, без «как ИИ»."
        )
        try:
            import asyncio

            async def _run():
                try:
                    out = await engine.generate_proactive(instruction)
                except Exception as e:
                    print(f"companion: llm comment {e}", flush=True)
                    out = ""
                text = (out or "").strip() or self._static_line(scene, title)
                if text:
                    self._mark_comment(app, f"{scene}|{(title or '')[:80]}", text)

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                return static
            if loop.is_running():
                self._comment_inflight = f"{scene}|{(title or '')[:80]}"
                asyncio.ensure_future(_run())
                return ""
            return static
        except Exception as e:
            print(f"companion: llm suggest {e}", flush=True)
            return static

    def _static_line(self, scene: str, title: str) -> str:
        hint = (title or "").split(" - ")[0].split(" — ")[0].strip()[:60]
        if scene == "coding":
            opts = [
                f"Ты в «{hint}». Если ошибка заела — кинь кусок." if hint else "Вижу код. Застрянешь — позови.",
                "Пахнет отладкой. Нужен свежий взгляд — я тут.",
            ]
        elif scene == "writing":
            opts = [
                "Пишешь текст? Могу подправить кусок, если покажешь.",
                "Документ открыт. Скажи, если нужно короче.",
            ]
        elif scene == "movie":
            opts = [
                f"«{hint}» на экране. Как оно?" if hint else "Что-то смотришь. Как оно?",
                "Я рядом, без спойлеров.",
            ]
        elif scene == "nsfw":
            opts = ["На экране что-то яркое. Если хочешь поговорить — я рядом."]
        elif scene == "browsing":
            opts = [
                f"Открыто: {hint}." if hint else "Сидишь в браузере.",
                f"Листаешь «{hint}». Найти или сохранить?" if hint else "Нужно сузить поиск — скажи.",
            ]
        elif scene == "chat":
            opts = ["Ты в переписке. Я рядом, без спама."]
        else:
            if not hint:
                return ""
            opts = [f"Сейчас у тебя «{hint}». Как оно?"]
        return random.choice(opts)

    def _mark_comment(self, app: AppContext, key: str, text: str) -> None:
        if key and key == self._last_comment_key:
            return
        self._comment_inflight = ""
        ok = self._publish(app, text)
        if not ok:
            self._dbg(app, f"publish-fail «{(text or '')[:80]}»")
            return
        self._last_comment_key = key
        self._last_suggest_at = time.time()
        self._dbg(app, f"sent «{(text or '')[:80]}»")

    def _publish(self, app: AppContext, text: str) -> bool:
        text = (text or "").strip()
        if not text:
            return False
        window = getattr(app, "window", None) or app.state.get("gui")
        if window is None:
            print("companion: publish skip — нет окна чата", flush=True)
            return False
        try:
            if hasattr(window, "publish_assistant_message"):
                window.publish_assistant_message(text)
                return True
            if hasattr(window, "post") and hasattr(window, "_append"):
                window.post(lambda t=text: window._append("Ассистент", t))
                return True
            if hasattr(window, "_append"):
                window._append("Ассистент", text)
                return True
        except Exception as e:
            print(f"companion: publish {e}", flush=True)
        return False

    def _mood_drift(self, app: AppContext) -> None:
        if str(app.state.get("imggen_stage") or "idle") != "idle":
            return
        last_user = float(app.state.get("last_user_activity") or 0)
        if last_user and time.time() - last_user < 600:
            return
        last = float(app.state.get("companion_mood_at") or 0)
        if last and time.time() - last < 480:
            return
        hour = int(app.state.get("local_hour") or datetime.now().hour)
        mood = str(app.state.get("companion_mood") or "calm")
        # сильные состояния со временем отпускают
        if mood in ("annoyed", "sad") and random.random() < 0.45:
            self._set_mood(app, "calm", 0.5)
            return
        if mood in ("flirty", "lust") and random.random() < 0.25:
            self._set_mood(app, "playful", 0.6)
            return
        # лёгкий дрейф по времени суток
        if random.random() > 0.35:
            return
        if hour < 7:
            self._set_mood(app, "sleepy", 0.35)
        elif hour < 11:
            self._set_mood(app, random.choice(("happy", "curious", "calm")), 0.6)
        elif hour < 18:
            self._set_mood(app, random.choice(("curious", "playful", "calm")), 0.6)
        elif hour < 22:
            nxt = "flirty" if app.state.get("character_nsfw") else "calm"
            self._set_mood(app, random.choice(("playful", nxt, "happy")), 0.65)
        else:
            self._set_mood(app, random.choice(("sleepy", "calm", "shy")), 0.4)

    @staticmethod
    def _fg_title() -> str:
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            return (buf.value or "").strip()
        except Exception:
            return ""

    # ---------- patterns ----------
    def _patterns_path(self, app: AppContext) -> Path:
        root = Path(getattr(app.config, "DATA_DIR", Path("data")))
        cid = getattr(app.config, "ACTIVE_CHARACTER", "default") or "default"
        d = root / "personas" / "characters" / str(cid)
        d.mkdir(parents=True, exist_ok=True)
        return d / "user_patterns.json"

    def _load_patterns(self, app: AppContext) -> None:
        p = self._patterns_path(app)
        try:
            if p.is_file():
                self._patterns = json.loads(p.read_text(encoding="utf-8"))
            else:
                self._patterns = {}
        except Exception:
            self._patterns = {}

    def _save_patterns(self, app: AppContext) -> None:
        try:
            p = self._patterns_path(app)
            p.write_text(json.dumps(self._patterns, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"companion: save patterns {e}", flush=True)

    def _note_pattern(self, app: AppContext, text: str) -> None:
        low = text.lower().strip()
        keys = []
        if any(w in low for w in ("найди", "погугли", "поиск")):
            keys.append("search")
        if any(w in low for w in ("картин", "фото", "image")):
            keys.append("images")
        if any(w in low for w in ("код", "функц", "баг", "error", "python", "asyncio")):
            keys.append("code_help")
        if any(w in low for w in ("запомни", "память")):
            keys.append("memory")
        if any(w in low for w in ("экран", "монитор", "что видишь")):
            keys.append("screen")
        if any(w in low for w in ("18+", "секс", "пошл", "nsfw")):
            keys.append("nsfw_talk")
        if any(w in low for w in ("открой", "закрой", "папк", "файл")):
            keys.append("pc")
        for k in keys:
            self._patterns[k] = int(self._patterns.get(k, 0)) + 1
        # иногда сохраняем
        if sum(self._patterns.values()) % 5 == 0:
            self._save_patterns(app)

    def _top_patterns(self, n: int = 5) -> List[str]:
        items = sorted(self._patterns.items(), key=lambda x: -x[1])
        labels = {
            "search": "поиск в сети",
            "images": "картинки",
            "code_help": "помощь по коду",
            "memory": "память",
            "screen": "экран",
            "nsfw_talk": "взрослые темы",
            "pc": "управление ПК",
        }
        return [f"{labels.get(k, k)}×{v}" for k, v in items[:n] if v > 0]


def register():
    return PluginImpl()
