# -*- coding: utf-8 -*-
"""Прямоугольная заставка 16:9, ~150×84 мм (16:9) на экране."""
from __future__ import annotations

import os
from PyQt5 import QtWidgets, QtCore, QtGui

import config

# Физический размер окна (16:9). 50 мм ширина → высота 50*9/16 ≈ 28 мм.
MM_W = 150.0
MM_H = MM_W * 9.0 / 16.0

_W = 320
_H = 180


def _apply_size(app):
    global _W, _H
    screen = app.primaryScreen() if app else None
    dpi = 96.0
    if screen:
        dpi = float(screen.logicalDotsPerInch() or screen.physicalDotsPerInch() or 96)
    _W = max(160, int(round(MM_W / 25.4 * dpi)))
    _H = max(90, int(round(MM_H / 25.4 * dpi)))


def _candidate_portraits():
    data = getattr(config, "DATA_DIR", os.getcwd())
    frames = getattr(config, "FRAMES_DIR", os.path.join(data, "frames"))
    names = (
        os.path.join(data, "splash.png"),
        os.path.join(data, "splash.jpg"),
        os.path.join(frames, "basic", "happy.png"),
        os.path.join(frames, "basic", "neutral.png"),
        os.path.join(frames, "basic", "idle.png"),
        os.path.join(frames, "extra", "happy.png"),
        os.path.join(frames, "idle", "idle_happy.png"),
    )
    return [p for p in names if os.path.isfile(p)]


def _load_portrait():
    for path in _candidate_portraits():
        pm = QtGui.QPixmap(path)
        if not pm.isNull():
            return pm
    return QtGui.QPixmap()


def _fit(src, w, h):
    scaled = src.scaled(w, h, QtCore.Qt.KeepAspectRatioByExpanding, QtCore.Qt.SmoothTransformation)
    x = max(0, (scaled.width() - w) // 2)
    y = max(0, (scaled.height() - h) // 2)
    return scaled.copy(x, y, w, h)


def _make_pixmap(status="загрузка…", pct=0):
    w, h = _W, _H
    pm = QtGui.QPixmap(w, h)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.fillRect(0, 0, w, h, QtGui.QColor("#120e0c"))
    portrait = _load_portrait()
    if not portrait.isNull():
        p.drawPixmap(0, 0, _fit(portrait, w, h))
    p.setPen(QtGui.QPen(QtGui.QColor("#c47a3a"), 2))
    p.setBrush(QtCore.Qt.NoBrush)
    p.drawRect(1, 1, w - 3, h - 3)

    p.setPen(QtGui.QColor("#f0c27a"))
    title = QtGui.QFont()
    title.setPixelSize(max(10, h // 9))
    title.setBold(True)
    p.setFont(title)
    p.drawText(QtCore.QRect(4, 2, w - 8, max(16, h // 6)), QtCore.Qt.AlignCenter, "Лисичка")

    pct = max(0, min(100, int(pct or 0)))
    bar_h = max(8, h // 14)
    bar_m = max(8, w // 30)
    bar_y = h - bar_h - max(6, h // 18)
    bar = QtCore.QRect(bar_m, bar_y, w - bar_m * 2, bar_h)
    p.setBrush(QtGui.QColor(0, 0, 0, 140))
    p.setPen(QtGui.QPen(QtGui.QColor("#c47a3a"), 1))
    p.drawRect(bar)
    fill_w = int((bar.width() - 2) * pct / 100.0)
    if fill_w > 0:
        p.fillRect(bar.x() + 1, bar.y() + 1, fill_w, bar.height() - 2, QtGui.QColor("#c47a3a"))

    sub = QtGui.QFont()
    sub.setPixelSize(max(9, h // 14))
    p.setFont(sub)
    p.setPen(QtGui.QColor("#f0d0a8"))
    label = f"{pct}%  {status or ''}".strip()
    p.drawText(QtCore.QRect(4, bar_y - max(16, h // 8), w - 8, max(16, h // 7)), QtCore.Qt.AlignCenter, label)
    p.end()
    return pm


current = None


class BootSplash:
    def __init__(self, app):
        global current
        self.app = app
        self.pct = 5
        self._total = 0
        self._done = 0
        self._span = (20, 95)
        _apply_size(app)
        pm = _make_pixmap("старт", self.pct)
        self.sp = QtWidgets.QSplashScreen(pm, QtCore.Qt.WindowStaysOnTopHint)
        self.sp.show()
        self.app.processEvents()
        current = self

    def say(self, text, pct=None):
        if pct is not None:
            self.pct = int(pct)
        if self.sp is None:
            return
        self.sp.setPixmap(_make_pixmap(text, self.pct))
        self.app.processEvents()
        print(f"⏳ {self.pct}% {text}")

    def begin_jobs(self, total, label="загрузка", span=(20, 95)):
        self._total = max(1, int(total or 1))
        self._done = 0
        self._span = span
        self.say(f"{label} 0/{self._total}", span[0])

    def tick(self, name=""):
        self._done += 1
        a, b = self._span
        pct = a + int((b - a) * self._done / self._total)
        left = max(0, self._total - self._done)
        label = name or "кадр"
        self.say(f"{label}  {self._done}/{self._total}", pct)

    def finish(self, window):
        self.say("готово", 100)
        if self.sp:
            self.sp.finish(window)
        self.sp = None
