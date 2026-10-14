# -*- coding: utf-8 -*-
"""Тесты Этапа 1 встроенного браузера: окно, вкладки, навигация.

Запуск (из корня asistent3):
    python/python.exe tests/test_browser_stage1.py
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
sys.path.insert(0, str(DATA_DIR))

# Оффскрин-режим для CI и тестов без дисплея
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# QtWebEngine должен быть импортирован ДО создания QApplication
from PyQt5 import QtWidgets  # noqa: E402
from PyQt5.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401

_QT_APP = None


def get_app() -> QtWidgets.QApplication:
    """Создать/вернуть глобальный QApplication."""
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    return _QT_APP


def _normalize_url(raw: str) -> str:
    """Логика нормализации URL (дубликат из window._on_url_entered для теста)."""
    raw = raw.strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://", "ftp://", "about:")):
        raw = "https://" + raw
    return raw


class TestUrlNormalize(unittest.TestCase):
    """Быстрые unit-тесты нормализации URL (без Qt)."""

    def test_domain_gets_https(self):
        self.assertEqual(_normalize_url("google.com"), "https://google.com")

    def test_www_domain(self):
        self.assertEqual(_normalize_url("www.google.com"), "https://www.google.com")

    def test_full_url_preserved(self):
        self.assertEqual(_normalize_url("http://example.com/x"),
                         "http://example.com/x")

    def test_https_preserved(self):
        self.assertEqual(_normalize_url("https://example.com"),
                         "https://example.com")

    def test_ftp_preserved(self):
        self.assertEqual(_normalize_url("ftp://ftp.example.com"),
                         "ftp://ftp.example.com")

    def test_blank_returned_empty(self):
        self.assertEqual(_normalize_url("   "), "")


class TestBrowserLive(unittest.TestCase):
    """Живые тесты с реальным QtWebEngine (требуют дисплея/сети).

    Пропускаются, если не задано LISICHKA_BROWSER_LIVE=1.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get("LISICHKA_BROWSER_LIVE", "0") != "1":
            raise unittest.SkipTest("LISICHKA_BROWSER_LIVE != 1")
        get_app()
        from browser.window import BrowserWindow
        cls.win = BrowserWindow()

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "win"):
            cls.win.close()

    def test_one_initial_tab(self):
        self.assertEqual(self.win.tab_count, 1)

    def test_new_tab_increases_count(self):
        before = self.win.tab_count
        self.win.new_tab("about:blank")
        self.assertEqual(self.win.tab_count, before + 1)

    def test_url_bar_normalizes_on_enter(self):
        # Ввод домена без протокола должен нормализоваться до https://
        self.win.url_bar.setText("example.com")
        self.win._on_url_entered()
        self.assertIn("https://example.com", self.win.url_bar.text())

    def test_close_tab_keeps_at_least_one(self):
        before = self.win.tab_count
        if before < 2:
            self.win.new_tab("about:blank")
        idx = self.win.tab_count - 1
        self.win.close_tab(idx)
        self.assertEqual(self.win.tab_count, before)

    def test_current_tab_is_tab_widget(self):
        tab = self.win.current_tab()
        self.assertIsNotNone(tab)
        if tab is not None:
            self.assertIsInstance(tab, QtWidgets.QWidget)

    def test_go_home(self):
        self.win._go_home()
        tab = self.win.current_tab()
        self.assertIsNotNone(tab)

    def test_zoom_functions_on_tab(self):
        tab = self.win.current_tab()
        self.assertIsNotNone(tab)
        if tab is not None:
            tab.zoom_in()
            tab.zoom_out()
            tab.zoom_reset()
            self.assertEqual(tab.view.zoomFactor(), 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)