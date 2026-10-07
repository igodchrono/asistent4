# -*- coding: utf-8 -*-
"""Окно аватара персонажа — статичные кадры из папки изображений."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5 import QtWidgets, QtCore, QtGui
import config
try:
    from settings_manager import load_settings, save_settings
except Exception:
    load_settings = None
    save_settings = None

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def find_image_dirs(character_dir: Path) -> List[Path]:
    """Возможные папки с картинками внутри персонажа."""
    if not character_dir or not character_dir.is_dir():
        return []
    candidates = [
        character_dir / "avatar",
        character_dir / "images",
        character_dir / "frames",
        character_dir / "frames" / "basic",
        character_dir / "sprites",
    ]
    found = []
    for c in candidates:
        if c.is_dir() and any(
            p.suffix.lower() in IMAGE_EXTS for p in c.rglob("*") if p.is_file()
        ):
            found.append(c)
    # если картинки лежат прямо в корне персонажа
    if any(p.suffix.lower() in IMAGE_EXTS for p in character_dir.iterdir() if p.is_file()):
        found.append(character_dir)
    return found


def has_avatar_images(character_dir: Path) -> bool:
    return bool(find_image_dirs(character_dir))


def load_frames_map(character_dir: Path) -> Dict[str, List[Path]]:
    """name -> [paths] (несколько файлов = простая анимация по имени)."""
    dirs = find_image_dirs(character_dir)
    frames: Dict[str, List[Path]] = {}
    for d in dirs:
        for p in sorted(d.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
                continue
            name = p.stem.lower()
            # happy_01 -> happy
            base = name
            for sep in ("_", "-"):
                parts = name.rsplit(sep, 1)
                if len(parts) == 2 and parts[1].isdigit():
                    base = parts[0]
                    break
            frames.setdefault(base, []).append(p)
            if base != name:
                frames.setdefault(name, []).append(p)
    return frames


class AvatarWindow(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Аватар")
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self._label = QtWidgets.QLabel(self)
        self._label.setAlignment(QtCore.Qt.AlignCenter)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._label)
        self._size = 280
        self._frames: Dict[str, List[Path]] = {}
        self._pixmaps: Dict[str, List[QtGui.QPixmap]] = {}
        self._current = "neutral"
        self._frame_i = 0
        self._drag = None
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._anim_ms = 80
        self._always_on_top = True

    def set_always_on_top(self, on: bool) -> None:
        self._always_on_top = bool(on)
        flags = QtCore.Qt.FramelessWindowHint | QtCore.Qt.Tool
        if self._always_on_top:
            flags |= QtCore.Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        if self.isVisible():
            self.show()

    def set_display_size(self, width: int) -> None:
        self._size = max(64, min(800, int(width)))
        self._pixmaps.clear()
        self.show_static(self._current)

    def set_anim_speed(self, ms: int) -> None:
        self._anim_ms = max(30, min(500, int(ms)))
        if self._timer.isActive():
            self._timer.start(self._anim_ms)

    def load_from_character_dir(self, character_dir: Path) -> int:
        self._frames = load_frames_map(character_dir)
        self._pixmaps.clear()
        return sum(len(v) for v in self._frames.values())

    def animation_names(self) -> List[str]:
        return sorted(self._frames.keys())

    def has(self, name: str) -> bool:
        return (name or "").lower() in self._frames

    def _scaled(self, path: Path) -> QtGui.QPixmap:
        pm = QtGui.QPixmap(str(path))
        if pm.isNull():
            return pm
        return pm.scaled(
            self._size,
            self._size,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )

    def _ensure_pixmaps(self, name: str) -> List[QtGui.QPixmap]:
        name = (name or "neutral").lower()
        if name in self._pixmaps:
            return self._pixmaps[name]
        paths = self._frames.get(name) or []
        pms = [self._scaled(p) for p in paths]
        pms = [p for p in pms if not p.isNull()]
        self._pixmaps[name] = pms
        return pms

    def show_static(self, name: str = "neutral") -> None:
        name = (name or "neutral").lower()
        if not self.has(name):
            # fallback
            for alt in ("neutral", "idle", "happy", "default"):
                if self.has(alt):
                    name = alt
                    break
            else:
                names = self.animation_names()
                if not names:
                    return
                name = names[0]
        self._current = name
        self._timer.stop()
        pms = self._ensure_pixmaps(name)
        if not pms:
            return
        pm = pms[0]
        self._label.setPixmap(pm)
        self.resize(pm.width(), pm.height())
        self._label.resize(pm.width(), pm.height())

    def play(self, name: str, loop: bool = True) -> None:
        name = (name or "neutral").lower()
        if not self.has(name):
            self.show_static(name)
            return
        self._current = name
        self._frame_i = 0
        pms = self._ensure_pixmaps(name)
        if len(pms) <= 1:
            self.show_static(name)
            return
        self._loop = loop
        self._timer.start(self._anim_ms)
        self._tick()

    def _tick(self) -> None:
        pms = self._ensure_pixmaps(self._current)
        if not pms:
            self._timer.stop()
            return
        pm = pms[self._frame_i % len(pms)]
        self._label.setPixmap(pm)
        self.resize(pm.width(), pm.height())
        self._frame_i += 1
        if not getattr(self, "_loop", True) and self._frame_i >= len(pms):
            self._timer.stop()
            self.show_static(self._current)

    def move_to_corner(self, corner: str = "bottom_right") -> None:
        screen = QtWidgets.QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        self.adjustSize()
        w, h = self.width(), self.height()
        margin = 16
        if corner == "bottom_left":
            x, y = geo.left() + margin, geo.bottom() - h - margin
        elif corner == "top_right":
            x, y = geo.right() - w - margin, geo.top() + margin
        elif corner == "top_left":
            x, y = geo.left() + margin, geo.top() + margin
        else:
            x, y = geo.right() - w - margin, geo.bottom() - h - margin
        self.move(int(x), int(y))

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & QtCore.Qt.LeftButton:
            self.move(event.globalPos() - self._drag)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag = None
        # Сохранить текущую позицию окна в настройках плагина, чтобы при следующем запуске
        # окно можно было восстановить туда же (немедленное применение — записываем в config).
        try:
            pos = self.pos()
            store = getattr(config, "PLUGIN_SETTINGS", None) or {}
            block = dict(store.get("avatar") or {})
            block["position"] = [int(pos.x()), int(pos.y())]
            store["avatar"] = block
            try:
                config.PLUGIN_SETTINGS = store
            except Exception:
                pass
            # Попытаться сохранить на диск
            if load_settings is not None and save_settings is not None:
                try:
                    data = load_settings() or {}
                    data["PLUGIN_SETTINGS"] = store
                    save_settings(data)
                except Exception:
                    pass
        except Exception:
            pass
