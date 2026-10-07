# -*- coding: utf-8 -*-
"""persona = эмоции + аватар. Все кадры из images/, не узкий список."""
from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.plugin_api import AppContext, HookResult, Plugin, SettingField

try:
    from plugins.persona.window import AvatarWindow, has_avatar_images
except Exception as _e:
    print(f"persona: window import FAIL: {_e}", flush=True)
    AvatarWindow = None

    def has_avatar_images(_p):
        return False

_ANIM_RE = re.compile(r"\[ANIM:([a-zA-Z0-9_]+)\]", re.I)

_FALLBACK = {
    "lust": "seductive",
    "horny": "seductive",
    "smile": "happy",
    "laugh": "giggling",
    "cry": "cry",
    "annoyed": "angry",
    "mad": "angry",
    "curious": "thinking",
    "wink": "teasing",
    "neutral": "idle",
    "hate": "angry",
    "playful": "playful",
    "sleepy": "sleepy",
}

# настроение → семьи кадров (не один idle)
_MOOD_FAMILIES = {
    "happy": ("happy", "playful", "giggling", "dance", "proud", "idle"),
    "flirty": ("flirty", "teasing", "sly", "love", "seductive", "lingerie", "undress"),
    "sad": ("cry", "shy", "pouting", "love", "idle"),
    "angry": ("angry", "pouting", "jealous", "pointing"),
    "shy": ("shy", "blush", "embarrassed", "innocent", "idle"),
    "curious": ("thinking", "searching", "surprised", "pointing"),
    "calm": ("idle", "neutral", "thinking", "sleepy"),
    "annoyed": ("angry", "pouting", "tired", "pointing"),
    "playful": ("playful", "mischievous", "sly", "teasing", "dance", "giggling"),
    "sleepy": ("sleepy", "tired", "idle", "bed"),
    "proud": ("proud", "confident", "happy", "smirking"),
    "mischievous": ("mischievous", "sly", "teasing", "playful"),
}

_NSFW_FAMILIES = {
    "undress", "lingerie", "bath", "bed", "naked", "seductive",
    "dominant", "submissive",
}

_INTENT_ANIM = {
    "web_search": "searching",
    "search_similar": "searching",
    "imggen": "pointing",
    "imggen_edit": "thinking",
    "describe_screen": "searching",
    "deep_think": "thinking",
    "pc_open": "pointing",
    "pc_search_files": "searching",
    "pc_search_folders": "searching",
    "memory_add": "happy",
    "memory_list": "thinking",
    "reminder_add": "surprised",
    "note_add": "happy",
    "save_file": "pointing",
}

# короткие ключи — через границу слова, иначе «рук» ловит «инструкцию»
_POSE_KEYS: List[Tuple[Tuple[str, ...], str]] = [
    (("танцуй", "потанцуй", "станцуй", "танец", "попляши"), "dance"),
    (("хвостик", "хвост", "ушки", "ушка", "ушами"), "sly"),
    (("что держишь", "в руках", "покажи руки", "ладони", "ладошку"), "pointing"),
    (("покажи пальц", "пальцы"), "pointing"),
    (("укажи", "покажи сюда", "вот так", "посмотри сюда"), "pointing"),
    (("грудь", "сиськ", "тело ближе", "поближе", "крупнее", "крупный план", "часть тела"), "lingerie"),
    (("живот", "талия", "бедра", "ножки", "ноги ближе"), "lingerie"),
    (("попа", "попку", "ягодиц", "со спины", "спину"), "teasing"),
    (("лицо ближе", "улыбку", "глаза ближе", "мордочк"), "blush"),
    (("полный рост", "целиком", "как ты выглядишь", "покажи себя"), "idle"),
    (("бель", "лифчик", "нижнее"), "lingerie"),
    (("раздень", "голая", "голую", "без одежд", "ню "), "undress"),
    (("ванн", "моешь", "купа"), "bath"),
    (("кроват", "ляж", "ложись", "в постель"), "bed"),
    (("подмигн", "дразн"), "teasing"),
    (("смущ", "стесня"), "shy"),
    (("любл", "поцел", "обним"), "love"),
    (("ревн",), "jealous"),
    (("бесит", "злюсь", "злая"), "angry"),
    (("груст", "поплач", "поплачь"), "cry"),
    (("устал", "поспи", "спать пора"), "sleepy"),
    (("болею", "заболел", "плохо себя"), "sick"),
    (("удивись", "шок"), "shocked"),
    (("испуг", "страшно"), "scared"),
    (("горж", "молодец"), "proud"),
    (("балуй", "дуроч"), "mischievous"),
    (("подумай", "подумай-ка"), "thinking"),
    (("хихи", "хаха", "смейся"), "giggling"),
]

_CYCLE_KEYS = (
    "все позы", "все кадры", "все анимац", "покажи позы", "покажи кадры",
    "следующ кадр", "следующая поза", "следующий кадр", "перелистни поз",
    "листай поз", "листай кадр", "ещё позу", "еще позу", "другую позу",
)


def key_hit(low: str, key: str) -> bool:
    """Подстрока для фраз, граница слова для коротких стемов."""
    key = (key or "").lower().strip()
    if not key or not low:
        return False
    if " " in key or len(key) >= 5:
        return key in low
    return re.search(r"(?:^|[^\wа-яё])" + re.escape(key), low, re.I) is not None


def family_of(name: str) -> str:
    return (name or "idle").lower().strip().split("_")[0] or "idle"


def group_families(names: List[str]) -> Dict[str, List[str]]:
    fam: Dict[str, List[str]] = {}
    for n in names:
        fam.setdefault(family_of(n), []).append(n)
    for k in fam:
        fam[k] = sorted(set(fam[k]), key=lambda x: (x != k, x))
    return fam


class PluginImpl(Plugin):
    id = "persona"
    name = "Персона (эмоции + аватар)"
    version = "1.2.0"
    description = "Все кадры, позы, живое настроение, poses.json"
    settings_tab = "own"
    settings_tab_title = "Персона"
    settings_schema = [
        SettingField("enabled", "Включить", "bool", True),
        SettingField("show_avatar", "Показывать окно аватара", "bool", True),
        SettingField("inject_mood", "Писать настроение и кадры в system", "bool", True),
        SettingField("react_to_reply", "Менять кадр по ответу / [ANIM:]", "bool", True),
        SettingField("live_idle", "Живой простой: сама меняет кадр", "bool", True),
        SettingField("live_idle_sec", "Секунд между сменами кадра", "int", 22, min_value=8, max_value=120),
        SettingField("anim_ms", "Скорость анимации (мс)", "int", 80, min_value=30, max_value=500),
    ]

    def __init__(self):
        self.win = None
        self.app = None
        self._keywords: List[dict] = []
        self._forbidden: set = set()
        self._forbid_fallback = "pouting"
        self._nsfw = True
        self._recent: List[str] = []
        self._pose_lock_until = 0.0
        self._cycle_i = 0
        self._idle_timer = None

    def _nsfw_ok(self) -> bool:
        if not self._nsfw:
            return False
        try:
            from core.mode import is_work
            if self.app is not None and is_work(self.app):
                return False
        except Exception:
            pass
        return True

    def on_load(self, app: AppContext) -> None:
        self.app = app
        app.state.setdefault("emotion", "idle")
        app.state.setdefault("emotion_animation", "idle")
        app.state["avatar_plugin"] = self
        app.state["emotion_plugin"] = self
        print("🎭 persona 1.2: all sprites + live mood + poses.json", flush=True)
        if app.get_plugin_setting(self.id, "show_avatar", True):
            self._ensure_window()
            self._load_active()
        self._start_idle_timer(app)

    def on_shutdown(self, app: AppContext) -> None:
        if self._idle_timer is not None:
            try:
                self._idle_timer.stop()
            except Exception:
                pass
            self._idle_timer = None
        if self.win is not None:
            try:
                self.win.close()
            except Exception:
                pass
            self.win = None

    def on_character_changed(self, character_id: str, previous_id: str, app: AppContext) -> None:
        self.app = app
        self._recent = []
        self._cycle_i = 0
        self._pose_lock_until = 0.0
        if not app.get_plugin_setting(self.id, "show_avatar", True):
            if self.win:
                self.win.hide()
            return
        self._ensure_window()
        self._load_active()

    def set_context(self, app: AppContext, emotion: str, source: str = "") -> None:
        raw = str(emotion or "idle").lower().strip()
        fam = family_of(raw)
        shown = str(app.state.get("emotion_animation") or "")
        if source == "screen":
            last_chat = float(app.state.get("last_user_activity") or 0)
            if last_chat and time.time() - last_chat < 45:
                return
            if shown and family_of(shown) == fam:
                return
        locked = time.time() < self._pose_lock_until
        if locked and source in ("screen", "companion_mood", "auto_message", "web_search", "chat"):
            return
        app.state["emotion"] = fam
        app.state["emotion_source"] = source
        if source in ("companion_mood", "user", "chat"):
            app.state["companion_mood"] = fam
            app.state["companion_mood_at"] = time.time()
        elif source in ("screen", "auto_message", "web_search"):
            mapped = {
                "searching": "curious",
                "thinking": "curious",
                "neutral": "calm",
                "idle": "calm",
            }.get(fam, fam)
            app.state["companion_mood"] = mapped
            app.state["companion_mood_at"] = time.time()
        if app.get_plugin_setting(self.id, "show_avatar", True):
            self.apply_emotion(raw, exact=False)

    def set_animation(self, name: str, app=None) -> None:
        self.apply_emotion(name)

    def on_user_message(self, text, app):
        if not app.get_plugin_setting(self.id, "enabled", True):
            return None
        low = (text or "").lower()
        if any(k in low for k in _CYCLE_KEYS):
            shown = self._cycle_next(app)
            if shown:
                return HookResult(True, f"кадр {self._cycle_i}/{max(1, len(self._live_names()))}: {shown}")
        hit = self._match_pose(low)
        if hit:
            self._pose_lock_until = time.time() + 90
            if app.get_plugin_setting(self.id, "show_avatar", True):
                self.apply_emotion(hit, exact=True)
            shown = str(app.state.get("emotion_animation") or hit)
            app.state["persona_pose_request"] = shown
            app.state["emotion"] = family_of(shown)
            app.state["emotion_source"] = "user_pose"
            return None
        fam = self._text_family(low)
        if fam and time.time() >= self._pose_lock_until:
            app.state["chat_emotion"] = fam
            app.state["emotion_source"] = "user_text"
            self.apply_emotion(fam)
        return None

    def on_before_llm(self, messages: List[Dict[str, Any]], app: AppContext) -> List[Dict[str, Any]]:
        if not app.get_plugin_setting(self.id, "enabled", True):
            return messages
        if not app.get_plugin_setting(self.id, "inject_mood", True):
            return messages
        mood = str(app.state.get("companion_mood") or app.state.get("emotion") or "idle")
        pose = str(app.state.get("persona_pose_request") or "")
        pose_line = f" Пользователь просит позу «{family_of(pose)}»." if pose else ""
        block = (
            f"\n\n[НАСТРОЕНИЕ] сейчас {mood}.{pose_line}\n"
            "Лицо уже выставлено по тексту. Не перечисляй эмоции и не ставь [ANIM], если тон тот же.\n"
        )
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = str(messages[0].get("content") or "") + block
        return messages

    def on_after_llm(self, reply: str, app: AppContext) -> str:
        if not app.get_plugin_setting(self.id, "enabled", True):
            return reply
        if time.time() < self._pose_lock_until:
            return _ANIM_RE.sub("", reply or "").strip("\n")
        pose = str(app.state.get("persona_pose_request") or "")
        app.state["persona_pose_request"] = ""
        if pose:
            self.apply_emotion(pose, exact=True)
            return _ANIM_RE.sub("", reply or "").strip("\n")
        user = str(app.state.get("last_user_text") or "")
        want = self._text_family(user) or self._text_family(reply or "")
        tag = ""
        tags = _ANIM_RE.findall(reply or "")
        if tags:
            tag = family_of(tags[-1])
        if not want and tag:
            want = tag
        if not want:
            last = float(app.state.get("last_user_activity") or 0)
            if not last or time.time() - last > 45:
                scr = str(app.state.get("screen_react_emotion") or "")
                if scr and scr not in ("neutral", "idle"):
                    want = scr
        if want and app.get_plugin_setting(self.id, "react_to_reply", True):
            app.state["chat_emotion"] = want
            app.state["emotion_source"] = "chat"
            app.state["companion_mood"] = family_of(want)
            app.state["companion_mood_at"] = time.time()
            print(f"persona chat → {want}", flush=True)
            self.apply_emotion(want)
        return _ANIM_RE.sub("", reply or "").strip("\n")

    def apply_emotion(self, emotion: str, exact: bool = False) -> None:
        if self.app is None:
            return
        if not self.app.get_plugin_setting(self.id, "show_avatar", True):
            return
        self._ensure_window()
        if self.win is None:
            return
        if not getattr(self.win, "_frames", None):
            self._load_active()
        try:
            if not self.win.isVisible():
                self.win.show()
        except Exception:
            pass
        raw = str(emotion or "idle").lower().strip()
        shown = str(self.app.state.get("emotion_animation") or "")
        if not exact and shown and family_of(shown) == family_of(raw):
            return
        names = set(self._live_names())
        if exact and raw in names and raw not in self._forbidden:
            name = raw
        else:
            name = self._resolve(raw)
        if not name:
            print(f"persona: нет кадра {emotion}", flush=True)
            return
        self._remember_shown(name)
        self.app.state["emotion_animation"] = name
        self.app.state["emotion"] = family_of(name)
        frames = self.win._frames.get(name) or []
        print(f"persona: frame {name} n={len(frames)}", flush=True)
        if len(frames) > 1:
            self.win.play(name, loop=True)
        else:
            self.win.show_static(name)

    def _live_names(self) -> List[str]:
        if self.win and hasattr(self.win, "animation_names"):
            return [n for n in self.win.animation_names() if n not in self._forbidden]
        return []

    def _remember_shown(self, name: str) -> None:
        if not name:
            return
        self._recent.append(name)
        if len(self._recent) > 40:
            self._recent = self._recent[-40:]

    def _pick_unused(self, pool: List[str]) -> str:
        if not pool:
            return ""
        fresh = [p for p in pool if p not in self._recent]
        if not fresh:
            # все из пула уже были — забыть только их, чтобы снова крутить семью
            shown = set(pool)
            self._recent = [x for x in self._recent if x not in shown]
            fresh = list(pool)
        last = self._recent[-1] if self._recent else ""
        if last in fresh and len(fresh) > 1:
            fresh = [p for p in fresh if p != last]
        return random.choice(fresh)

    def _family_pool(self, family: str, names: Set[str], mood: str = "") -> List[str]:
        family = family_of(family)
        if family in _FALLBACK and family not in names:
            family = _FALLBACK[family]
        if not self._nsfw_ok() and family in _NSFW_FAMILIES:
            family = self._forbid_fallback
        pool = []
        for n in names:
            if n == family or n.startswith(family + "_"):
                pool.append(n)
        if mood:
            want = f"{family}_{mood}"
            if want in names and want not in pool:
                pool.append(want)
            want2 = f"{mood}_{family}"
            if want2 in names and want2 not in pool:
                pool.append(want2)
        if not pool and family in names:
            pool = [family]
        return [p for p in pool if p not in self._forbidden]

    def _resolve(self, raw: str) -> str:
        raw = (raw or "idle").lower().strip()
        names = set(self._live_names())
        if not names and self.win:
            names = set(self.win.animation_names())
        if raw in self._forbidden:
            raw = self._forbid_fallback
        if raw in names and raw not in self._forbidden:
            return raw
        pool = self._family_pool(raw, names, "")
        if not pool:
            pool = self._family_pool(_FALLBACK.get(raw, raw), names, "")
        if not pool:
            for alt in list(names):
                if alt.startswith(family_of(raw) + "_") or alt.endswith("_" + family_of(raw)):
                    if alt not in self._forbidden:
                        pool.append(alt)
        if pool:
            return sorted(pool)[0]
        for c in (self._forbid_fallback, "idle", "neutral", "happy"):
            if c in names:
                return c
        return next(iter(sorted(names)), "")

    def _match_pose(self, low: str) -> str:
        for item in self._keywords:
            keys = item.get("keys") or []
            anim = str(item.get("anim") or "")
            if anim and any(key_hit(low, k) for k in keys):
                if not self._nsfw_ok() and family_of(anim) in _NSFW_FAMILIES:
                    return self._forbid_fallback
                return anim
        for keys, anim in _POSE_KEYS:
            if any(key_hit(low, k) for k in keys):
                if not self._nsfw_ok() and anim in _NSFW_FAMILIES:
                    return self._forbid_fallback
                return anim
        return ""

    def _text_family(self, text: str) -> str:
        """Пустая строка, если в тексте нет явного тона."""
        low = (text or "").lower()
        if not low.strip():
            return ""
        rules = (
            (("хаха", "хихи", "ахах", "смеш", "лол"), "giggling"),
            (("груст", "жалко", "обид", "плач", "плохо мне"), "cry"),
            (("злюсь", "бесит", "раздраж", "ненавиж"), "angry"),
            (("люблю", "скучаю", "обним", "целу", "родн"), "love"),
            (("нарисуй", "сгенери", "промпт"), "pointing"),
            (("найди", "поищи", "что на экране", "глянь"), "searching"),
            (("сонн", "устал", "спать"), "sleepy"),
            (("смущ", "стесня"), "shy"),
            (("привет", "доброе утро", "добрый вечер", "как ты"), "happy"),
        )
        for keys, fam in rules:
            if any(k in low for k in keys):
                return fam
        if "?" in low and len(low) < 400:
            return "thinking"
        return ""

    def _situation_family(self, app: AppContext) -> str:
        last = float(app.state.get("last_user_activity") or 0)
        if last and time.time() - last < 50:
            return str(app.state.get("companion_mood") or app.state.get("emotion") or "idle")
        scr = str(app.state.get("screen_react_emotion") or "")
        if scr and scr not in ("neutral", "idle"):
            return scr
        return str(app.state.get("companion_mood") or "calm")

    def _auto_frame(self, app: AppContext) -> str:
        intent = str(app.state.get("last_intent") or "")
        if intent in _INTENT_ANIM and float(app.state.get("last_user_activity") or 0) and time.time() - float(app.state.get("last_user_activity") or 0) < 50:
            return self._resolve(_INTENT_ANIM[intent])
        return self._resolve(self._situation_family(app))

    def _cycle_next(self, app: AppContext) -> str:
        names = self._live_names()
        if not names:
            return ""
        self._cycle_i = (self._cycle_i % len(names)) + 1
        name = names[self._cycle_i - 1]
        self._pose_lock_until = time.time() + 20
        app.state["persona_pose_request"] = name
        app.state["emotion_animation"] = name
        app.state["emotion"] = family_of(name)
        self.apply_emotion(name, exact=True)
        return name

    def _start_idle_timer(self, app: AppContext) -> None:
        try:
            from PyQt5 import QtCore
        except Exception:
            return
        if self._idle_timer is not None:
            try:
                self._idle_timer.stop()
            except Exception:
                pass
        sec = int(app.get_plugin_setting(self.id, "live_idle_sec", 22) or 22)
        self._idle_timer = QtCore.QTimer()
        self._idle_timer.setInterval(max(8, sec) * 1000)
        self._idle_timer.timeout.connect(lambda: self._on_idle_tick(app))
        self._idle_timer.start()

    def _on_idle_tick(self, app: AppContext) -> None:
        if not app.get_plugin_setting(self.id, "enabled", True):
            return
        if not app.get_plugin_setting(self.id, "live_idle", True):
            return
        if time.time() < self._pose_lock_until:
            return
        if not app.get_plugin_setting(self.id, "show_avatar", True):
            return
        # не дёргать, если пользователь только что писал (кадр сменит on_after_llm)
        last = float(app.state.get("last_user_activity") or 0)
        if last and time.time() - last < 20:
            return
        want = family_of(self._situation_family(app))
        shown = family_of(str(app.state.get("emotion_animation") or ""))
        if shown == want:
            return
        print(f"persona live {shown} → {want}", flush=True)
        self.apply_emotion(want)

    def _ensure_window(self) -> None:
        if AvatarWindow is None:
            print("persona: AvatarWindow is None — положи plugins/persona/window.py", flush=True)
            return
        if self.win is None:
            self.win = AvatarWindow()
            try:
                self.win.app = self.app
            except Exception:
                pass
        if self.app is None:
            return
        self.win.set_anim_speed(int(self.app.get_plugin_setting(self.id, "anim_ms", 80) or 80))

    def _load_active(self) -> None:
        if self.app is None or self.win is None:
            return
        self._load_maps()
        cdir = self.app.get_character_dir()
        if not has_avatar_images(cdir):
            print(f"persona: нет картинок в {cdir}", flush=True)
            try:
                self.win.hide()
            except Exception:
                pass
            return
        n = self.win.load_from_character_dir(cdir)
        names = self.win.animation_names()
        print(f"persona: {cdir.name} files≈{n} unique={len(names)} forbidden={len(self._forbidden)}", flush=True)
        start = "idle" if self.win.has("idle") else ("neutral" if self.win.has("neutral") else (names[0] if names else "idle"))
        self.win.show_static(start)
        self.win.show()

    def _load_maps(self) -> None:
        self._keywords = []
        self._forbidden = set()
        self._forbid_fallback = "pouting"
        self._nsfw = True
        if self.app is None:
            return
        cdir = self.app.get_character_dir()
        for fname in ("poses.json", "emotions_map.json", "reactions.json"):
            p = Path(cdir) / fname
            if not p.is_file():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"persona: {fname} {e}", flush=True)
                continue
            rows = []
            if isinstance(data.get("poses"), list):
                rows.extend(data["poses"])
            if isinstance(data.get("keywords"), list):
                rows.extend(data["keywords"])
            for row in data.get("reactions") or []:
                if not isinstance(row, dict):
                    continue
                phrases = row.get("phrases") or []
                sprite = row.get("sprite") or row.get("anim") or ""
                if phrases and sprite:
                    rows.append({"keys": phrases, "anim": sprite})
            for row in rows:
                if isinstance(row, dict) and (row.get("keys") or row.get("phrases")) and (row.get("anim") or row.get("sprite")):
                    self._keywords.append({
                        "keys": list(row.get("keys") or row.get("phrases") or []),
                        "anim": str(row.get("anim") or row.get("sprite") or ""),
                    })
        try:
            from character_catalog import read_character_card
            card = read_character_card(str(self.app.get_active_character() or "")) or ""
        except Exception:
            card = ""
        self._nsfw = True
        for raw in (card or "").splitlines()[:20]:
            line = raw.strip().lower()
            if line.startswith("nsfw:"):
                self._nsfw = line.split(":", 1)[-1].strip() in ("true", "yes", "1", "on", "да")
                break
        grab = False
        for line in (card or "").splitlines():
            s = line.strip()
            low = s.lower()
            if low.startswith("## анимации запрещен"):
                grab = True
                continue
            if low.startswith("## "):
                grab = False
            if grab and s and not s.startswith("#"):
                for part in s.replace(";", ",").split(","):
                    n = part.strip().lower()
                    if n:
                        self._forbidden.add(n)
        nxt = False
        for line in (card or "").splitlines():
            s = line.strip()
            if s.lower().startswith("## анимация вместо"):
                nxt = True
                continue
            if nxt and s and not s.startswith("#"):
                self._forbid_fallback = s.split()[0].strip(",.").lower()
                break


def register():
    return PluginImpl()


if __name__ == "__main__":
    fail = 0

    def check(ok, msg):
        global fail
        if not ok:
            fail += 1
            print("FAIL", msg)
        else:
            print("OK  ", msg)

    check(key_hit("покажи хвостик", "хвостик"), "хвостик")
    check(not key_hit("напиши инструкцию", "рук"), "рук ≠ инструкция")
    check(not key_hit("это дорого", "ого"), "ого ≠ дорого")
    check(key_hit("что в руках", "в руках"), "в руках")
    check(key_hit("разденься", "раздень"), "раздень")
    check(family_of("undress_teasing") == "undress", "family undress")
    fams = group_families(["idle", "idle_happy", "sly", "sly_happy", "dance"])
    check(fams["idle"] == ["idle", "idle_happy"], f"group idle {fams.get('idle')}")
    check("все позы" in "покажи все позы пожалуйста", "cycle phrase")
    print(f"fail={fail}")
    raise SystemExit(fail)
