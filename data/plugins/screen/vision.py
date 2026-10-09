# -*- coding: utf-8 -*-
"""Видение экрана: снимок выбранного монитора (с понятными именами как в Windows)."""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.plugin_api import AppContext, Plugin, SettingField


def list_monitors() -> List[Dict[str, Any]]:
    """
    Список мониторов:
      index 0 = виртуальный «все экраны»
      1..N   = физические (как в mss / близко к нумерации Windows)
    """
    out: List[Dict[str, Any]] = []
    try:
        import mss
        with mss.mss() as sct:
            # sct.monitors[0] = bounding box all
            all_m = sct.monitors[0]
            out.append({
                "index": 0,
                "label": f"0 — Все экраны сразу ({all_m['width']}×{all_m['height']})",
                "width": all_m["width"],
                "height": all_m["height"],
                "left": all_m["left"],
                "top": all_m["top"],
                "primary": False,
            })
            for i, m in enumerate(sct.monitors[1:], start=1):
                primary = (m.get("left", 0) == 0 and m.get("top", 0) == 0)
                # primary heuristic: often monitor 1 is primary; also left=0 top=0
                tag = "основной" if primary or i == 1 else f"доп. #{i}"
                out.append({
                    "index": i,
                    "label": (
                        f"{i} — Монитор {i} ({tag}): "
                        f"{m['width']}×{m['height']} @ ({m['left']},{m['top']})"
                    ),
                    "width": m["width"],
                    "height": m["height"],
                    "left": m["left"],
                    "top": m["top"],
                    "primary": primary or i == 1,
                })
            # refine primary: smallest left+top among physical is usually primary in Windows
            if len(out) > 1:
                phys = out[1:]
                best = min(phys, key=lambda x: (x["left"] ** 2 + x["top"] ** 2, x["index"]))
                for m in phys:
                    m["primary"] = m["index"] == best["index"]
                    tag = "основной" if m["primary"] else f"доп. #{m['index']}"
                    m["label"] = (
                        f"{m['index']} — Монитор {m['index']} ({tag}): "
                        f"{m['width']}×{m['height']} @ ({m['left']},{m['top']})"
                    )
    except Exception as e:
        print(f"screen_vision: list_monitors: {e}", flush=True)
        out = [
            {"index": 0, "label": "0 — Все экраны", "width": 0, "height": 0, "left": 0, "top": 0, "primary": False},
            {"index": 1, "label": "1 — Монитор 1 (основной)", "width": 0, "height": 0, "left": 0, "top": 0, "primary": True},
            {"index": 2, "label": "2 — Монитор 2", "width": 0, "height": 0, "left": 0, "top": 0, "primary": False},
            {"index": 3, "label": "3 — Монитор 3", "width": 0, "height": 0, "left": 0, "top": 0, "primary": False},
        ]
    return out


class PluginImpl(Plugin):
    id = "screen_vision"
    name = "Видение экрана"
    version = "2.2.0"
    description = "Снимок выбранного монитора для описания"
    settings_tab = "own"
    settings_tab_title = "Видение экрана"
    # schema без monitor — монитор рисуем в custom UI
    settings_schema = [
        SettingField("enabled", "Включить", "bool", True),
        SettingField("max_side", "Макс. сторона снимка (px)", "int", 1600, min_value=640, max_value=3840),
        SettingField("monitor", "Индекс монитора", "int", 1, min_value=0, max_value=8,
                     help="Служебное; выбирай в списке ниже"),
    ]

    def __init__(self) -> None:
        self._ui: Dict[str, Any] = {}
        self.app = None

    def _sid(self) -> str:
        return "screen"

    def register_tools(self, app: AppContext) -> None:
        app.tools["describe_screen"] = self.tool_describe_screen

    def tool_describe_screen(self, app: AppContext, **kwargs) -> str:
        if not app.get_plugin_setting(self._sid(), "enabled", True):
            return "Видение экрана выключено."
        mon = kwargs.get("monitor")
        if mon is not None:
            try:
                app.state["screen_monitor_override"] = int(mon)
            except Exception:
                pass
        path = self.capture(app)
        if not path:
            app.state.pop("screen_monitor_override", None)
            return "Не удалось сделать снимок (нужен Pillow / mss)."
        app.state["screen_vision_attach"] = True
        app.state["screen_vision_just_captured"] = True
        app.state["screen_vision_last_path"] = str(path)
        mon_i = self._monitor_index(app)
        app.state.pop("screen_monitor_override", None)
        info = next((m for m in list_monitors() if m["index"] == mon_i), None)
        label = info["label"] if info else str(mon_i)
        app.state["screen_vision_last_desc"] = f"screenshot:{path.name} | {label}"
        print(f"screen_vision: shot {path} {label}", flush=True)
        return f"Снимок готов: {label}"

    def on_before_llm(self, messages: List[Dict[str, Any]], app: AppContext) -> List[Dict[str, Any]]:
        if not app.state.pop("screen_vision_attach", None) and not app.state.get("screen_vision_just_captured"):
            return messages
        app.state["screen_vision_just_captured"] = False
        path = Path(str(app.state.get("screen_vision_last_path") or ""))
        if not path.is_file():
            return messages
        try:
            import base64
            raw = path.read_bytes()
            if len(raw) < 80:
                return messages
            b64 = base64.b64encode(raw).decode("ascii")
            url = "data:image/jpeg;base64," + b64
            hint = (
                "На снимке — то, что сейчас на выбранном мониторе. "
                "Если сетка картинок: выбери одну (позиция + что на ней). Без нового поиска."
            )
            for m in reversed(messages):
                if m.get("role") == "user":
                    prev = m.get("content")
                    if isinstance(prev, list):
                        text = " ".join(
                            str(p.get("text") or "") for p in prev if isinstance(p, dict)
                        )
                    else:
                        text = str(prev or "")
                    m["content"] = [
                        {"type": "text", "text": (text + "\n\n" + hint).strip()},
                        {"type": "image_url", "image_url": {"url": url}},
                    ]
                    print(f"screen_vision: attached {path.name} bytes={len(raw)}", flush=True)
                    break
        except Exception as e:
            print(f"screen_vision: attach fail {e}", flush=True)
        return messages

    def on_after_llm(self, reply: str, app: AppContext) -> str:
        text = reply or ""
        m = re.search(r"PICK:\s*(.+)", text, re.I)
        if m:
            pick = m.group(1).strip().split("\n")[0][:120]
            app.state["last_image_pick"] = pick
            print(f"screen_vision: PICK={pick!r}", flush=True)
            text = re.sub(r"\s*PICK:\s*.+", "", text, count=1, flags=re.I).strip()
        return text

    def _monitor_index(self, app: AppContext) -> int:
        ov = app.state.get("screen_monitor_override")
        if ov is not None:
            try:
                return int(ov)
            except Exception:
                pass
        if app.state.pop("screen_capture_search", None):
            found = self._monitor_with_browser(app)
            if found is not None:
                print(f"screen_vision: browser monitor → {found}", flush=True)
                return found
            print("screen_vision: browser window not found → all screens", flush=True)
            return 0
        for pid in ("screen", self.id):
            raw = app.get_plugin_setting(pid, "monitor", None)
            if raw is not None:
                try:
                    return int(str(raw).split()[0])
                except Exception:
                    pass
        return 1

    @staticmethod
    def _monitor_with_browser(app: AppContext) -> Optional[int]:
        """Монитор, где открыт поиск (Chrome/Edge заголовок с запросом)."""
        q = str(app.state.get("last_search_query") or "").strip().lower()
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            found = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            def cb(hwnd, _lp):
                if not user32.IsWindowVisible(hwnd):
                    return True
                n = user32.GetWindowTextLengthW(hwnd)
                if n < 4:
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value or ""
                tl = title.lower()
                if not any(b in tl for b in ("chrome", "firefox", "edge", "opera", "brave", "yandex")):
                    return True
                score = 0
                if q and q[:24] in tl:
                    score += 5
                if any(w in tl for w in ("google", "поиск", "search", "images", "картин", "яндекс")):
                    score += 2
                if score:
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    found.append((score, rect.left, rect.top, rect.right, rect.bottom, title))
                return True

            user32.EnumWindows(cb, 0)
            if not found:
                return None
            found.sort(key=lambda x: -x[0])
            _s, left, top, right, bottom, title = found[0]
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            print(f"screen_vision: search window {title[:70]!r} center=({cx},{cy})", flush=True)
            import mss
            with mss.mss() as sct:
                for i, mon in enumerate(sct.monitors[1:], start=1):
                    if (mon["left"] <= cx < mon["left"] + mon["width"]
                            and mon["top"] <= cy < mon["top"] + mon["height"]):
                        return i
            return 0
        except Exception as e:
            print(f"screen_vision: find browser: {e}", flush=True)
            return None

    def capture(self, app: AppContext) -> Optional[Path]:
        try:
            from PIL import Image, ImageGrab
        except ImportError:
            return None
        mon_idx = self._monitor_index(app)
        try:
            image = None
            try:
                import mss
                with mss.mss() as sct:
                    if mon_idx <= 0:
                        mon = sct.monitors[0]
                    elif mon_idx < len(sct.monitors):
                        mon = sct.monitors[mon_idx]
                    else:
                        mon = sct.monitors[-1]
                    print(f"screen_vision: capture idx={mon_idx} geo={mon}", flush=True)
                    app.state["screen_capture_geo"] = {
                        "left": int(mon.get("left", 0)),
                        "top": int(mon.get("top", 0)),
                        "width": int(mon.get("width", 0)),
                        "height": int(mon.get("height", 0)),
                    }
                    shot = sct.grab(mon)
                    image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            except Exception as e:
                print(f"screen_vision: mss fallback: {e}", flush=True)
                image = ImageGrab.grab(all_screens=(mon_idx <= 0))
            if image is None:
                return None
            max_side = int(
                app.get_plugin_setting("screen", "max_side", None)
                or app.get_plugin_setting(self._sid(), "max_side", 1600)
                or 1600
            )
            image.thumbnail((max_side, max_side))
            buf = io.BytesIO()
            image.convert("RGB").save(buf, format="JPEG", quality=86)
            base = Path(getattr(app.config, "DATA_DIR", Path("data"))) / "cache"
            base.mkdir(parents=True, exist_ok=True)
            path = base / "screen_last.jpg"
            path.write_bytes(buf.getvalue())
            return path
        except Exception as e:
            print(f"screen_vision: {e}", flush=True)
            return None

    # ---- Settings UI ----
    def setup_settings_tab(self, tab, app: AppContext) -> bool:
        try:
            from PyQt5 import QtWidgets, QtCore
        except ImportError:
            return False
        self.app = app
        layout = tab.layout() or QtWidgets.QVBoxLayout(tab)
        while layout.count():
            it = layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        layout.addWidget(QtWidgets.QLabel(
            "<b>Видение экрана</b><br>"
            "Выбери монитор так же, как в параметрах Windows: "
            "по номеру, размеру и позиции (левый/верхний угол)."
        ))

        # enabled + max_side
        values = {}
        try:
            from plugin_catalog import plugin_settings_block
            values = plugin_settings_block(self._sid()) or {}
        except Exception:
            pass

        self._ui = {}
        en = QtWidgets.QCheckBox("Включить")
        en.setChecked(bool(values.get("enabled", True)))
        self._ui["enabled"] = en
        layout.addWidget(en)

        react = QtWidgets.QCheckBox("Реакция на активное окно")
        react.setChecked(bool(values.get("react", True)))
        self._ui["react"] = react
        layout.addWidget(react)

        auto = QtWidgets.QCheckBox("Сама выбирать картинку с этого монитора, без просьбы")
        auto.setChecked(bool(values.get("auto_pick", True)))
        self._ui["auto_pick"] = auto
        layout.addWidget(auto)

        form = QtWidgets.QFormLayout()
        spin = QtWidgets.QSpinBox()
        spin.setRange(640, 3840)
        spin.setValue(int(values.get("max_side", 1600) or 1600))
        self._ui["max_side"] = spin
        form.addRow("Макс. сторона снимка (px)", spin)
        iv = QtWidgets.QSpinBox()
        iv.setRange(2, 30)
        iv.setValue(int(values.get("interval_sec", 4) or 4))
        self._ui["interval_sec"] = iv
        form.addRow("Интервал опроса (сек)", iv)
        gap = QtWidgets.QSpinBox()
        gap.setRange(2, 180)
        gap.setValue(int(values.get("auto_pick_minutes", 5) or 5))
        self._ui["auto_pick_minutes"] = gap
        form.addRow("Сама выбирает не чаще, минут", gap)
        layout.addLayout(form)

        layout.addWidget(QtWidgets.QLabel("<b>Монитор для снимков и «что на экране»</b>"))
        combo = QtWidgets.QComboBox()
        monitors = list_monitors()
        cur = int(values.get("monitor", 1) or 1)
        sel = 0
        for i, m in enumerate(monitors):
            combo.addItem(m["label"], m["index"])
            if m["index"] == cur:
                sel = i
        combo.setCurrentIndex(sel)
        self._ui["monitor_combo"] = combo
        layout.addWidget(combo)

        info = QtWidgets.QLabel("")
        info.setWordWrap(True)
        layout.addWidget(info)

        def update_info():
            idx = combo.currentData()
            m = next((x for x in monitors if x["index"] == idx), None)
            if not m:
                info.setText("")
                return
            if m["index"] == 0:
                info.setText(
                    "Будет склеен <b>весь рабочий стол</b> (все мониторы). "
                    "Модель чаще путается — лучше выбрать один."
                )
            else:
                prim = "да" if m.get("primary") else "нет"
                info.setText(
                    f"Снимок только этого экрана.<br>"
                    f"Разрешение: <b>{m['width']}×{m['height']}</b><br>"
                    f"Позиция (лево, верх): <b>({m['left']}, {m['top']})</b><br>"
                    f"Основной в Windows (эвристика): <b>{prim}</b><br>"
                    f"<i>Подсказка: в Windows «Параметры → Система → Дисплей» номера "
                    f"часто совпадают с 1, 2, 3… Слева направо смотри left.</i>"
                )

        combo.currentIndexChanged.connect(lambda *_: update_info())
        update_info()

        # Identify: flash border by capturing and showing path + optional brief
        btn_row = QtWidgets.QHBoxLayout()
        btn_ref = QtWidgets.QPushButton("Обновить список мониторов")
        btn_test = QtWidgets.QPushButton("Тест: снимок выбранного")
        btn_row.addWidget(btn_ref)
        btn_row.addWidget(btn_test)
        layout.addLayout(btn_row)
        preview = QtWidgets.QLabel("(превью после теста)")
        preview.setAlignment(QtCore.Qt.AlignCenter)
        preview.setMinimumHeight(120)
        layout.addWidget(preview)
        self._ui["preview"] = preview

        def reload_list():
            combo.blockSignals(True)
            combo.clear()
            mons = list_monitors()
            cur_i = combo.currentData() if combo.count() else 1
            for m in mons:
                combo.addItem(m["label"], m["index"])
            # restore
            for i in range(combo.count()):
                if combo.itemData(i) == cur:
                    combo.setCurrentIndex(i)
                    break
            combo.blockSignals(False)
            update_info()

        def test_shot():
            # временно применить monitor из combo
            idx = combo.currentData()
            try:
                from plugin_catalog import set_plugin_setting
                set_plugin_setting(self._sid(), "monitor", int(idx))
            except Exception:
                # fallback app state
                if not hasattr(app.config, "PLUGIN_SETTINGS"):
                    app.config.PLUGIN_SETTINGS = {}
                app.config.PLUGIN_SETTINGS.setdefault(self._sid(), {})["monitor"] = int(idx)
            path = self.capture(app)
            if not path:
                preview.setText("Не удалось снять экран")
                return
            from PyQt5.QtGui import QPixmap
            pm = QPixmap(str(path))
            if not pm.isNull():
                preview.setPixmap(pm.scaledToWidth(320, QtCore.Qt.SmoothTransformation))
            else:
                preview.setText(str(path))
            QtWidgets.QMessageBox.information(
                tab, "Снимок",
                f"Сохранено: {path}\nМонитор: {combo.currentText()}",
            )

        btn_ref.clicked.connect(reload_list)
        btn_test.clicked.connect(test_shot)
        layout.addStretch(1)
        return True

    def collect_settings_tab(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if "enabled" in self._ui:
            out["enabled"] = self._ui["enabled"].isChecked()
        if "react" in self._ui:
            out["react"] = self._ui["react"].isChecked()
        if "auto_pick" in self._ui:
            out["auto_pick"] = self._ui["auto_pick"].isChecked()
        if "auto_pick_minutes" in self._ui:
            out["auto_pick_minutes"] = self._ui["auto_pick_minutes"].value()
        if "max_side" in self._ui:
            out["max_side"] = self._ui["max_side"].value()
        if "interval_sec" in self._ui:
            out["interval_sec"] = self._ui["interval_sec"].value()
        combo = self._ui.get("monitor_combo")
        if combo is not None:
            try:
                out["monitor"] = int(combo.currentData())
            except Exception:
                out["monitor"] = 1
        return out

    @staticmethod
    def _keywords(text: str) -> str:
        stop = {
            "на", "в", "и", "с", "что", "как", "это", "экран", "монитор", "вижу",
            "изображение", "картинка", "хозяин", "можно", "хочешь", "похожие",
            "день", "вечер", "утро", "лови", "ищу", "жми",
        }
        words = []
        for w in re.findall(r"[A-Za-zА-Яа-яЁё0-9]{3,}", text or ""):
            lw = w.lower()
            if lw in stop:
                continue
            if lw not in words:
                words.append(lw)
            if len(words) >= 8:
                break
        return " ".join(words)


def register():
    return PluginImpl()
