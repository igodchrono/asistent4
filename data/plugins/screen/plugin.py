# -*- coding: utf-8 -*-
"""screen = screen_vision + screen_react + выбор картинки с монитора."""
from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

from core.plugin_api import AppContext, HookResult, Plugin, SettingField

# reuse helpers from original vision if present
try:
    from plugins.screen.vision import list_monitors
except Exception:
    def list_monitors():
        return []

_PICK = (
    "выбери картин", "выбери фото", "выбери изображ", "выбери одну",
    "выбери кадр", "какая нравится", "какая тебе нрав", "какая больше нрав",
    "какая по душе", "выбери с экрана", "выбери из этих",
)
_SKIP = (
    "нарисуй", "сгенерир", "сделай картин", "найди", "погугли", "поищи",
    "скачай перв", "открой", "модель", "граф", "воркфлоу",
)
_EMOTIONS = {
    "happy", "flirty", "sad", "angry", "shy", "curious", "calm",
    "annoyed", "playful", "sleepy", "proud", "mischievous",
}

class PluginImpl(Plugin):
    id = "screen"
    name = "Экран (зрение + реакция)"
    version = "1.2.0"
    description = "Снимок монитора, реакция на окно и выбор одной картинки"
    settings_tab = "own"
    settings_tab_title = "Экран"
    settings_schema = [
        SettingField("enabled", "Включить", "bool", True),
        SettingField("react", "Реакция на активное окно", "bool", True),
        SettingField("auto_pick", "Сама выбирать картинку с монитора", "bool", True),
        SettingField("auto_pick_minutes", "Не чаще, чем раз в N минут", "int", 5, min_value=2, max_value=180),
        SettingField("interval_sec", "Интервал опроса (сек)", "int", 4, min_value=2, max_value=30),
        SettingField("max_side", "Макс. сторона снимка (px)", "int", 1600, min_value=640, max_value=3840),
        SettingField("monitor", "Индекс монитора", "int", 1, min_value=0, max_value=8),
    ]

    def __init__(self):
        self._timer = None
        self.app = None
        self._vision = None

    def on_load(self, app: AppContext) -> None:
        self.app = app
        app.state["screen_plugin"] = self
        print("👁 screen 1.3: save original, crop only if download fails", flush=True)
        # wrap original vision tools if folder still exists
        try:
            from plugins.screen.vision import PluginImpl as Vision
            self._vision = Vision()
            if hasattr(self._vision, "on_load"):
                try:
                    self._vision.on_load(app)
                except Exception:
                    pass
        except Exception as e:
            print(f"screen: vision helper {e}", flush=True)
        try:
            from PyQt5 import QtCore
            self._timer = QtCore.QTimer()
            self._timer.timeout.connect(lambda: self._tick(app))
            sec = int(app.get_plugin_setting(self.id, "interval_sec", 4) or 4)
            self._timer.start(max(2, sec) * 1000)
        except Exception as e:
            print(f"screen: no timer {e}", flush=True)

    def on_user_message(self, text, app):
        if not app.get_plugin_setting(self.id, "enabled", True):
            return None
        low = (text or "").strip().lower()
        if not low:
            return None
        if app.state.get("screen_pick_busy"):
            if any(w in low for w in ("стоп", "отмена", "не надо")):
                app.state["screen_pick_busy"] = False
                app.state["screen_pick_job"] = 0
                return HookResult(True, "ок, не выбираю.")
            if self._wants_pick(low):
                return HookResult(True, "уже смотрю экран.")
            return None
        if str(app.state.get("imggen_stage") or "idle") != "idle":
            return None
        save = self._wants_save_screen(low, app)
        if not save and not self._wants_pick(low):
            return None
        job = time.time()
        app.state["screen_pick_busy"] = True
        app.state["screen_pick_job"] = job
        threading.Thread(
            target=self._pick_thread, args=(app, text, job, False), name="screen-pick", daemon=True
        ).start()
        if save:
            return HookResult(True, "беру оригинал с экрана, не скриншот.")
        return HookResult(True, "смотрю выбранный экран и выбираю.")

    @staticmethod
    def _wants_save_screen(low: str, app) -> bool:
        keys = (
            "сохрани картин", "сохрани фото", "сохрани изображ",
            "сохрани этот кадр", "сохрани кадр", "сохрани с экран",
            "скачай с экран", "скачай эту картин", "скачай это фото",
            "скачай это изображ",
        )
        if not any(k in (low or "") for k in keys):
            return False
        if re.search(r"\b\d{1,2}\b", low or "") and str(app.state.get("last_search_mode") or "") == "images":
            return False
        return True

    @staticmethod
    def _wants_pick(low: str) -> bool:
        if any(w in low for w in _SKIP):
            return False
        return any(w in low for w in _PICK)

    def _pick_thread(self, app, text: str, job: float, quiet: bool = False) -> None:
        try:
            asyncio.run(self._pick(app, text, job, quiet))
        except Exception as e:
            print(f"screen pick: {e}", flush=True)
            if quiet:
                self._finish(app, job, "", None, "")
            else:
                self._finish(app, job, "не смогла выбрать картинку. проверь модель со зрением в LM Studio.", None, "")

    async def _pick(self, app, text: str, job: float, quiet: bool = False) -> None:
        def fail(msg: str) -> None:
            self._finish(app, job, "" if quiet else msg, None, "")

        if not self._vision or not hasattr(self._vision, "capture"):
            fail("зрение экрана не загружено.")
            return
        path = self._vision.capture(app)
        if not path or not Path(path).is_file():
            fail("не сняла выбранный экран. проверь монитор в настройках.")
            return
        raw = Path(path).read_bytes()
        if len(raw) < 80:
            fail("снимок пустой.")
            return
        name, card = self._card(app)
        system = (
            "Ты персонаж из карточки. На снимке выбранный монитор пользователя. "
            "Если там нет нескольких отдельных картинок, верни только {\"skip\": true}. "
            "Если картинки есть, выбери одну по вкусу из карточки, не «самую красивую вообще». "
            "Игнорируй окна ассистента, панели и пустые поля. "
            "Верни только JSON без markdown:\n"
            '{"bbox":[x,y,w,h],"emotion":"happy","line":"...","index":0}\n'
            "bbox — доли от 0 до 1, прямоугольник только выбранной миниатюры. "
            "emotion одно из: happy, flirty, sad, angry, shy, curious, calm, annoyed, "
            "playful, sleepy, proud, mischievous. "
            "line — 1–2 предложения по-русски, как будто ты сама это заметила, без JSON и без слов bbox. "
            "index — номер слева направо сверху вниз, если это сетка поиска, иначе 0."
        )
        user = (
            f"Персонаж: {name}\nКарточка:\n{card}\n"
            f"{text}\nВыбери одну картинку на снимке или откажись, если их нет."
        )
        b64 = base64.b64encode(raw).decode("ascii")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": user},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
            ]},
        ]
        data = await self._ask_json(app, messages)
        if not data:
            messages[0]["content"] += "\nПрошлый ответ был не JSON. Верни только один JSON-объект."
            data = await self._ask_json(app, messages)
        if app.state.get("screen_pick_job") != job:
            return
        skip = data.get("skip") if data else None
        if not data or skip is True or str(skip).strip().lower() in ("true", "1", "yes"):
            fail("не разобрала, какую картинку выбрать. оставь сетку на выбранном мониторе.")
            return
        bbox = self._bbox(data.get("bbox"))
        if not bbox:
            fail("на экране не вижу отдельную картинку. оставь сетку на выбранном мониторе.")
            return
        saved = None
        if not quiet:
            saved = self._grab_original(app, bbox)
        if saved is not None:
            app.state["screen_save_kind"] = "original"
        else:
            saved = self._crop(app, Path(path), bbox)
            app.state["screen_save_kind"] = "crop"
        if saved is None or not saved.exists():
            fail("вижу кадр, но не смогла сохранить оригинал и вырезать картинку.")
            return
        app.state["phone_media_last"] = str(saved)
        app.state["screen_last_at"] = time.time()  # для _after_scene: свежий скриншот = референс
        # НЕ ставим last_attachments — это поле только для ручного прикрепления файлов пользователем
        # screen pick не должен просачиваться в генерацию как референс
        emo = str(data.get("emotion") or "").lower().strip()
        if emo not in _EMOTIONS:
            emo = "curious"
        line = self._line(data.get("line"))
        self._finish(app, job, line, saved, emo)

    async def _ask_json(self, app, messages) -> dict:
        llm = getattr(app, "llm", None)
        if llm is None or not hasattr(llm, "chat_once"):
            return {}
        try:
            raw = await llm.chat_once(messages, temperature=0.4, max_tokens=700)
        except Exception as e:
            print(f"screen pick llm: {e}", flush=True)
            return {}
        return self._parse_json(raw or "")

    @staticmethod
    def _parse_json(raw: str) -> dict:
        s = (raw or "").strip()
        if "```" in s:
            parts = s.split("```")
            s = parts[1] if len(parts) > 1 else s
            if s.lower().startswith("json"):
                s = s[4:]
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            return {}
        try:
            data = json.loads(s[i:j + 1])
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _bbox(raw) -> Optional[Tuple[float, float, float, float]]:
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            return None
        try:
            x, y, w, h = [float(v) for v in raw]
        except Exception:
            return None
        if min(x, y, w, h) < 0 or max(x, y) > 1 or w > 1 or h > 1:
            return None
        if w < 0.04 or h < 0.04 or x + w > 1.08 or y + h > 1.08:
            return None
        w = min(w, 1 - x)
        h = min(h, 1 - y)
        if w < 0.04 or h < 0.04:
            return None
        return x, y, w, h

    @staticmethod
    def _line(raw) -> str:
        text = re.sub(r"\s+", " ", str(raw or "")).strip()
        text = re.sub(r"(?i)\b(bbox|json|pick)\b", "", text).strip(" .")
        if not text or len(text) > 400 or text.startswith("{"):
            return "вот эта. она мне ближе остальных."
        return text

    def _card(self, app) -> Tuple[str, str]:
        name = "персонаж"
        try:
            if hasattr(app, "get_active_character"):
                name = str(app.get_active_character() or "") or name
            if not name or name == "персонаж":
                name = str(getattr(app.config, "ACTIVE_CHARACTER", "") or "") or name
        except Exception:
            pass
        look = ""
        try:
            from character_catalog import read_character_card
            look = (read_character_card(name) or "")[:1500]
        except Exception:
            look = ""
        return name, look

    def _pick_dir(self, app) -> Path:
        name, _look = self._card(app)
        safe = re.sub(r"[^\w\-\u0400-\u04FF]+", "_", name).strip("_") or "default"
        root = Path(__file__).resolve().parents[2] / "generated" / safe / "picks"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _grab_original(self, app, bbox: Tuple[float, float, float, float]) -> Optional[Path]:
        """Клик по картинке: адрес и скачивание, иначе «Сохранить как»."""
        import sys
        if not sys.platform.startswith("win"):
            return None
        geo = app.state.get("screen_capture_geo") or {}
        try:
            left, top = int(geo.get("left", 0)), int(geo.get("top", 0))
            width, height = int(geo.get("width", 0)), int(geo.get("height", 0))
        except Exception:
            return None
        if width < 80 or height < 80:
            return None
        x, y, w, h = bbox
        px = int(left + (x + w / 2.0) * width)
        py = int(top + (y + h / 2.0) * height)
        try:
            import ctypes
            user32 = ctypes.windll.user32
        except Exception as e:
            print(f"screen save: no user32 {e}", flush=True)
            return None
        point = self._cursor(user32)
        print(f"screen save: mouse {px},{py}", flush=True)
        try:
            self._cursor_set(user32, px, py)
            time.sleep(0.05)
            for phrase in ("копировать адрес", "copy image address"):
                self._right_click(user32)
                time.sleep(0.22)
                self._clear_clipboard(user32)
                self._type_text(user32, phrase)
                time.sleep(0.08)
                self._tap(user32, 0x0D)
                time.sleep(0.35)
                url = self._clipboard_text(user32).strip()
                if url.startswith("http://") or url.startswith("https://"):
                    saved = self._download_url(app, url)
                    if saved:
                        print(f"screen save: downloaded {saved}", flush=True)
                        return saved
                self._tap(user32, 0x1B)
                time.sleep(0.12)
                self._cursor_set(user32, px, py)
            since = time.time()
            for phrase in ("сохранить изображение", "save image"):
                self._right_click(user32)
                time.sleep(0.22)
                self._type_text(user32, phrase)
                time.sleep(0.08)
                self._tap(user32, 0x0D)
                if not self._wait_save_dialog(user32, 1.6):
                    self._tap(user32, 0x1B)
                    time.sleep(0.1)
                    self._cursor_set(user32, px, py)
                    continue
                dest = self._pick_dir(app) / f"save_{int(time.time())}"
                self._tap_ctrl(user32, 0x41)
                time.sleep(0.05)
                self._type_text(user32, str(dest))
                time.sleep(0.05)
                self._tap(user32, 0x0D)
                found = self._wait_new_image(since, dest)
                if found:
                    print(f"screen save: dialog {found}", flush=True)
                    return found
                self._tap(user32, 0x1B)
        except Exception as e:
            print(f"screen save: {e}", flush=True)
            return None
        finally:
            if point is not None:
                self._cursor_set(user32, point[0], point[1])
        return None

    def _download_url(self, app, url: str) -> Optional[Path]:
        browser = app.plugins.get("browser_search")
        if browser is None or not hasattr(browser, "_http_get"):
            return None
        try:
            data, ctype, final = browser._http_get(url)
        except Exception as e:
            print(f"screen save: download {e}", flush=True)
            return None
        if len(data) < 80:
            return None
        ext = ".jpg"
        if hasattr(browser, "_ext_from"):
            ext = browser._ext_from(data, ctype, final or url) or ".jpg"
        if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
            return None
        dest = self._pick_dir(app) / f"save_{int(time.time())}{ext}"
        dest.write_bytes(data)
        return dest

    def _wait_new_image(self, since: float, dest: Path) -> Optional[Path]:
        folders = [dest.parent, Path.home() / "Downloads"]
        deadline = time.time() + 4
        while time.time() < deadline:
            for folder in folders:
                if not folder.is_dir():
                    continue
                for item in folder.iterdir():
                    if item.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
                        continue
                    try:
                        if item.stat().st_mtime < since - 1:
                            continue
                    except Exception:
                        continue
                    if item.parent == dest.parent:
                        return item
                    target = dest.parent / f"save_{int(time.time())}{item.suffix.lower()}"
                    try:
                        target.write_bytes(item.read_bytes())
                    except Exception:
                        return item
                    return target
            time.sleep(0.2)
        return None

    @staticmethod
    def _cursor(user32):
        import ctypes
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return None
        return int(pt.x), int(pt.y)

    @staticmethod
    def _cursor_set(user32, x: int, y: int) -> None:
        import ctypes
        user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.SetCursorPos(int(x), int(y))

    @staticmethod
    def _right_click(user32) -> None:
        user32.mouse_event(0x0008, 0, 0, 0, 0)
        user32.mouse_event(0x0010, 0, 0, 0, 0)

    @staticmethod
    def _tap(user32, vk: int) -> None:
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)

    @staticmethod
    def _tap_ctrl(user32, vk: int) -> None:
        user32.keybd_event(0x11, 0, 0, 0)
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)
        user32.keybd_event(0x11, 0, 2, 0)

    @staticmethod
    def _type_text(user32, text: str) -> None:
        import ctypes
        ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ulong_ptr),
            ]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("ki", KEYBDINPUT)]

        if ctypes.sizeof(ctypes.c_void_p) == 8 and ctypes.sizeof(INPUT) < 40:
            class INPUT(ctypes.Structure):
                _fields_ = [
                    ("type", ctypes.c_ulong),
                    ("ki", KEYBDINPUT),
                    ("pad", ctypes.c_ulonglong),
                ]

        for ch in text:
            down = INPUT()
            down.type = 1
            down.ki.wScan = ord(ch)
            down.ki.dwFlags = 0x0004
            up = INPUT()
            up.type = 1
            up.ki.wScan = ord(ch)
            up.ki.dwFlags = 0x0004 | 0x0002
            user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(down))
            user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(up))
            time.sleep(0.02)

    @staticmethod
    def _clear_clipboard(user32) -> None:
        if user32.OpenClipboard(None):
            user32.EmptyClipboard()
            user32.CloseClipboard()

    @staticmethod
    def _clipboard_text(user32) -> str:
        import ctypes
        if not user32.OpenClipboard(None):
            return ""
        try:
            user32.GetClipboardData.restype = ctypes.c_void_p
            handle = user32.GetClipboardData(13)
            if not handle:
                return ""
            kernel32 = ctypes.windll.kernel32
            kernel32.GlobalLock.restype = ctypes.c_wchar_p
            kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
            kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            text = kernel32.GlobalLock(handle) or ""
            kernel32.GlobalUnlock(handle)
            return str(text)
        finally:
            user32.CloseClipboard()

    @staticmethod
    def _wait_save_dialog(user32, timeout: float) -> bool:
        import ctypes
        buf = ctypes.create_unicode_buffer(64)
        end = time.time() + timeout
        while time.time() < end:
            hwnd = user32.GetForegroundWindow()
            user32.GetClassNameW(hwnd, buf, 64)
            if buf.value == "#32770":
                return True
            time.sleep(0.1)
        return False

    def _crop(self, app, src: Path, bbox: Tuple[float, float, float, float]) -> Optional[Path]:
        try:
            from PIL import Image
        except ImportError:
            return None
        x, y, w, h = bbox
        x += w * 0.02
        y += h * 0.02
        w *= 0.96
        h *= 0.96
        im = Image.open(src).convert("RGB")
        W, H = im.size
        left = max(0, int(x * W))
        top = max(0, int(y * H))
        right = min(W, int((x + w) * W))
        bottom = min(H, int((y + h) * H))
        if right - left < 64 or bottom - top < 64:
            return None
        dest = self._pick_dir(app) / f"pick_{int(time.time())}.png"
        im.crop((left, top, right, bottom)).save(dest, format="PNG")
        print(f"screen pick: crop {dest}", flush=True)
        return dest

    def _save_screenshot(self, app, src: Path) -> Optional[Path]:
        """Сохранить полный скриншот вместо обрезка — качество выше."""
        try:
            dest = self._pick_dir(app) / f"pick_{int(time.time())}.png"
            from PIL import Image
            Image.open(src).convert("RGB").save(dest, format="PNG")
            print(f"screen pick: full screenshot {dest}", flush=True)
            return dest
        except Exception as e:
            print(f"screen pick: full screenshot error {e}", flush=True)
        return None

    def _download_original(self, app, data: dict) -> Optional[Path]:
        if str(app.state.get("last_search_mode") or "") != "images":
            return None
        results = list(app.state.get("last_search_results") or [])
        try:
            idx = int(data.get("index") or 0)
        except Exception:
            idx = 0
        if idx < 1 or idx > len(results):
            return None
        browser = app.plugins.get("browser_search")
        if browser is None or not hasattr(browser, "tool_download_image"):
            return None
        try:
            msg = browser.tool_download_image(app, index=idx)
        except Exception as e:
            print(f"screen pick download: {e}", flush=True)
            return None
        m = re.search(r"\[фото:\s*([^\]]+)\]", msg or "", flags=re.I)
        if not m:
            return None
        src = Path(m.group(1).strip().strip('"'))
        if not src.is_file():
            return None
        dest = self._pick_dir(app) / f"pick_{int(time.time())}{src.suffix.lower() or '.jpg'}"
        dest.write_bytes(src.read_bytes())
        print(f"screen pick: original {dest}", flush=True)
        return dest

    def _finish(self, app, job: float, text: str, path: Optional[Path], emotion: str) -> None:
        if app.state.get("screen_pick_job") != job:
            return
        app.state["screen_pick_job"] = 0
        app.state["screen_pick_busy"] = False

        def ui():
            if emotion:
                persona = (
                    app.plugins.get("persona")
                    or app.state.get("emotion_plugin")
                    or app.plugins.get("emotion")
                )
                if persona and hasattr(persona, "set_context"):
                    try:
                        persona.set_context(app, emotion, "chat")
                        persona._pose_lock_until = time.time() + 90
                    except Exception as e:
                        print(f"screen pick emotion: {e}", flush=True)
            msg = text or ""
            if path and path.exists() and str(app.state.get("screen_save_kind") or "") == "crop":
                msg = (msg + "\nоригинал не скачался, это обрезка экрана.").strip()
            if path and path.exists():
                msg = msg + f"\n[фото: {path}]"
            if not msg.strip():
                return
            gui = getattr(app, "window", None) or app.state.get("gui")
            if gui and hasattr(gui, "publish_assistant_message"):
                gui.publish_assistant_message(msg)
                return
            if gui and hasattr(gui, "_append"):
                gui._append("Ассистент", msg)

        win = getattr(app, "window", None) or app.state.get("gui")
        if win is not None and hasattr(win, "post"):
            win.post(ui)
            return
        ui()

    def on_shutdown(self, app: AppContext) -> None:
        if self._timer:
            self._timer.stop()

    def register_tools(self, app: AppContext) -> None:
        app.tools["describe_screen"] = self.tool_describe_screen
        if self._vision and hasattr(self._vision, "register_tools"):
            try:
                self._vision.register_tools(app)
            except Exception:
                pass

    def tool_describe_screen(self, app: AppContext, **kwargs) -> str:
        if self._vision and hasattr(self._vision, "tool_describe_screen"):
            return self._vision.tool_describe_screen(app, **kwargs)
        return "screen: vision-модуль не загружен"

    def capture(self, app: AppContext):
        if self._vision and hasattr(self._vision, "capture"):
            return self._vision.capture(app)
        return None

    def _tick(self, app: AppContext) -> None:
        if not app.get_plugin_setting(self.id, "enabled", True):
            return
        self._maybe_auto_pick(app)
        if not app.get_plugin_setting(self.id, "react", True):
            return
        title = self._fg_title()
        if not title or self._own_title(title):
            return
        prev = str(app.state.get("screen_react_title") or "")
        if title == prev and app.state.get("screen_react_emotion"):
            return
        app.state["screen_react_title"] = title
        app.state["screen_react_context"] = title
        emo, anim, conf = self._infer(app, title)
        if conf < 0.55:
            return
        app.state["screen_react_emotion"] = emo
        print(f"screen: {emo}/{anim} conf={conf:.2f} ← {title[:70]!r}", flush=True)
        if str(app.state.get("imggen_stage") or "idle") != "idle":
            return
        last_chat = float(app.state.get("last_user_activity") or 0)
        if last_chat and time.time() - last_chat < 90:
            return
        persona = (
            app.plugins.get("persona")
            or app.state.get("emotion_plugin")
            or app.plugins.get("emotion")
        )
        if persona and hasattr(persona, "set_context"):
            try:
                persona.set_context(app, anim or emo, "screen")
            except Exception:
                pass

    def _maybe_auto_pick(self, app: AppContext) -> None:
        if not app.get_plugin_setting(self.id, "auto_pick", True):
            return
        if app.state.get("screen_pick_busy"):
            return
        if str(app.state.get("imggen_stage") or "idle") != "idle":
            return
        if not self._vision or not hasattr(self._vision, "capture"):
            return
        gap = int(app.get_plugin_setting(self.id, "auto_pick_minutes", 5) or 5) * 60
        last = float(app.state.get("screen_auto_pick_at") or 0)
        if last and time.time() - last < max(120, gap):
            return
        job = time.time()
        app.state["screen_auto_pick_at"] = job
        app.state["screen_pick_busy"] = True
        app.state["screen_pick_job"] = job
        threading.Thread(
            target=self._pick_thread,
            args=(
                app,
                "Никто не просил. Ты сама смотришь выбранный монитор и решаешь, хочешь ли забрать одну картинку.",
                job,
                True,
            ),
            name="screen-auto-pick",
            daemon=True,
        ).start()

    def _infer(self, app, ctx):
        text = str(ctx or "")
        nsfw_allowed = False
        try:
            from core._guard import character_is_nsfw
            nsfw_allowed = bool(character_is_nsfw(app))
        except Exception:
            nsfw_allowed = bool(app.state.get("character_nsfw"))
        low = text.lower()
        rules = (
            (("porno", "porn", "hentai", "xxx", "xvideos", "xnxx", "nhentai", "rule34", "nsfw"), "flirty", 0.9),
            (("youtube", "netflix", "twitch", "vlc", "potplayer", "фильм", "сериал", "kinopoisk"), "calm", 0.75),
            (("spotify", "музыка", "youtube music", "яндекс музыка"), "happy", 0.7),
            (("telegram", "discord", "whatsapp", "slack", "vk.com", "вконтакте"), "happy", 0.72),
            (("visual studio", "vscode", "pycharm", "cursor", "powershell", "terminal", "stackoverflow", "github"), "thinking", 0.8),
            (("word", "excel", "notepad", "блокнот", "obsidian", "notion", "google docs"), "thinking", 0.7),
            (("steam", "game", "игра", "minecraft"), "playful", 0.7),
            (("error", "exception", "traceback", "ошибк"), "surprised", 0.8),
            (("chrome", "firefox", "edge", "opera", "brave", "google", "поиск", "search"), "searching", 0.66),
        )
        for keys, emo, conf in rules:
            if any(w in low for w in keys):
                if emo == "flirty" and not nsfw_allowed:
                    return "shy", "shy", 0.75
                return emo, emo, conf
        if low.strip():
            return "curious", "thinking", 0.58
        return "neutral", "idle", 0.3

    def on_before_llm(self, messages: List[Dict[str, Any]], app: AppContext) -> List[Dict[str, Any]]:
        title = str(app.state.get("screen_react_title") or "")
        desc = str(app.state.get("screen_vision_last_desc") or "")
        bits = []
        if title:
            bits.append(f"активное окно: {title}")
        if desc:
            bits.append(f"снимок: {desc[:200]}")
        if bits and messages and messages[0].get("role") == "system":
            messages[0]["content"] = str(messages[0].get("content") or "") + "\n\n[ЭКРАН] " + " | ".join(bits) + "\n"
        if self._vision and hasattr(self._vision, "on_before_llm"):
            try:
                messages = self._vision.on_before_llm(messages, app)
            except Exception as e:
                print(f"screen: on_before_llm {e}", flush=True)
        return messages

    def on_after_llm(self, reply: str, app: AppContext) -> str:
        if self._vision and hasattr(self._vision, "on_after_llm"):
            try:
                return self._vision.on_after_llm(reply, app)
            except Exception:
                pass
        return reply

    def setup_settings_tab(self, tab, app: AppContext) -> bool:
        if self._vision and hasattr(self._vision, "setup_settings_tab"):
            return self._vision.setup_settings_tab(tab, app)
        return False

    def collect_settings_tab(self):
        if self._vision and hasattr(self._vision, "collect_settings_tab"):
            return self._vision.collect_settings_tab()
        return {}

    @staticmethod
    def _own_title(title: str) -> bool:
        low = (title or "").lower()
        return any(k in low for k in ("лисич", "lisichka", "asistent"))

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


def register():
    return PluginImpl()
