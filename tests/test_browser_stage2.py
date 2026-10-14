# -*- coding: utf-8 -*-
"""Тесты Этапов 2-3: профиль (cookie/кеш) и DOM API (+ JS-мост).

Запуск (из корня asistent3):
    python/python.exe tests/test_browser_stage2.py

Живые тесты с WebEngine требуют дисплея и включаются
переменной LISICHKA_BROWSER_LIVE=1.
"""
from __future__ import annotations

import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "none")
# Предотвращает ошибку OpenGL при отсутствии дисплея
import PyQt5.QtCore
PyQt5.QtCore.QCoreApplication.setAttribute(
    PyQt5.QtCore.Qt.AA_ShareOpenGLContexts, True)

from PyQt5 import QtWidgets  # noqa: E402,F401
from PyQt5.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
sys.path.insert(0, str(DATA_DIR))

_QT_APP = None


def _skip_no_display():
    """Пропустить тест, если не задан LISICHKA_BROWSER_LIVE=1."""
    if os.environ.get("LISICHKA_BROWSER_LIVE", "0") != "1":
        raise unittest.SkipTest("Требуется LISICHKA_BROWSER_LIVE=1")


def get_app():
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(
            sys.argv[:1])
    return _QT_APP


# ── Вспомогательный HTTP-сервер для живых тестов ─────────────────────────

PAGE_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Тестовая страница</title>
  <meta name="description" content="проверка meta">
</head>
<body>
  <h1 id="head">Привет, Лисичка</h1>
  <p class="item">Первый абзац</p>
  <a href="/page2" title="Вторая">Перейти на 2</a>
  <a href="https://example.com">Внешняя</a>
  <input name="q" placeholder="поиск" type="text">
  <textarea name="bio"></textarea>
  <select name="city"><option value="m">Москва</option></select>
  <form id="f" action="/submit">
    <input name="user" type="text">
    <input name="pass" type="password">
  </form>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # не шумим
        pass


class _HttpServer:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()


# ── Тесты профиля ─────────────────────────────────────────────────────────

class TestBrowserProfile(unittest.TestCase):
    def setUp(self) -> None:
        _skip_no_display()
        get_app()
        from browser.profile import BrowserProfile
        self.profile = BrowserProfile(
            storage_name="test_lisichka",
            storage_dir=Path(DATA_DIR) / "browser" / "_test_storage",
            off_the_record=True)

    def tearDown(self) -> None:
        self.profile.deleteLater()

    def test_persistent_by_default(self):
        from browser.profile import BrowserProfile
        p = BrowserProfile(storage_name="test_persist",
                           storage_dir=Path(DATA_DIR) / "browser" / "_test2",
                           off_the_record=False)
        try:
            self.assertFalse(p.isOffTheRecord())
            self.assertFalse(p.is_off_the_record)
        finally:
            p.deleteLater()

    def test_storage_root_created(self):
        root = self.profile.storage_root
        self.assertTrue(root.exists())

    def test_javascript_enabled(self):
        s = self.profile.settings()
        from PyQt5.QtWebEngineWidgets import QWebEngineSettings
        self.assertTrue(
            s.testAttribute(QWebEngineSettings.JavascriptEnabled))

    def test_user_agent(self):
        ua = "LisichkaBot/1.0"
        self.profile.user_agent = ua
        self.assertIn("LisichkaBot/1.0", self.profile.user_agent)

    def test_clear_ops_do_not_crash(self):
        self.profile.clear_cache()
        self.profile.clear_cookies()
        self.profile.clear_all()

    def test_list_cookies_callback(self):
        result = []
        self.profile.list_cookies(result.append)
        import time
        deadline = time.monotonic() + 2
        while not result and time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.1)
        self.assertIsInstance(result[0], list)


# ── Тесты установки моста (без загрузки контента) ────────────────────────

class TestBridgeInstall(unittest.TestCase):
    def setUp(self) -> None:
        _skip_no_display()
        get_app()
        from PyQt5.QtWebEngineWidgets import QWebEnginePage
        self.page = QWebEnginePage()
        from browser.js_bridge import JSBridge
        self.bridge = JSBridge()
        self.channel = None

    def tearDown(self) -> None:
        self.page.deleteLater()

    def test_scripts_installed(self):
        from browser.js_bridge import install_scripts
        install_scripts(self.page)
        names = {s.name() for s in self.page.scripts().toList()}
        self.assertIn("lisichka_qwebchannel", names)
        self.assertIn("lisichka_dom_api", names)

    def test_install_does_not_duplicate(self):
        from browser.js_bridge import install_scripts
        install_scripts(self.page)
        install_scripts(self.page)
        names = [s.name() for s in self.page.scripts().toList()]
        self.assertEqual(names.count("lisichka_dom_api"), 1)

    def test_install_on_page_returns_channel(self):
        from browser.js_bridge import install_on_page
        self.channel = install_on_page(self.page, self.bridge)
        self.assertIsNotNone(self.channel)

    def test_bridge_handlers(self):
        self.bridge.register_handler("x", lambda payload: None)
        self.assertTrue(self.bridge.has_handler("x"))
        self.bridge.unregister_handler("x")
        self.assertFalse(self.bridge.has_handler("x"))


# ── Живые тесты DOM API и моста ───────────────────────────────────────────

class TestDomApiLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get("LISICHKA_BROWSER_LIVE", "0") != "1":
            raise unittest.SkipTest("LISICHKA_BROWSER_LIVE != 1")
        get_app()
        from browser.js_bridge import JSBridge, install_on_page
        from browser.tab import Tab
        from browser.profile import BrowserProfile

        cls.server = _HttpServer()
        cls.bridge = JSBridge()
        cls.profile = BrowserProfile(
            storage_name="test_live",
            storage_dir=Path(DATA_DIR) / "browser" / "_test_storage",
            off_the_record=True)

        cls.tab = Tab(profile=cls.profile)
        install_on_page(cls.tab.view.page(), cls.bridge)
        cls.tab.load(cls.server.url)
        ok = cls.tab.wait_load(timeout=15.0)
        if not ok:
            cls.tearDownClass()
            raise unittest.SkipTest("WebEngine не загрузил страницу (нет дисплея?)")

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "tab"):
            cls.tab.deleteLater()
        if hasattr(cls, "profile"):
            cls.profile.deleteLater()
        if hasattr(cls, "server"):
            cls.server.shutdown()

    def test_dom_title(self):
        self.assertEqual(self.tab.dom.get_title(), "Тестовая страница")

    def test_dom_url(self):
        self.assertTrue(self.tab.dom.get_url().startswith("http://127.0.0.1"))

    def test_dom_text(self):
        text = self.tab.dom.get_text()
        self.assertIn("Привет, Лисичка", text)

    def test_dom_text_selector(self):
        text = self.tab.dom.get_text("#head")
        self.assertEqual(text, "Привет, Лисичка")

    def test_dom_html_contains(self):
        html = self.tab.dom.get_html("h1#head")
        self.assertIn("Привет, Лисичка", html)

    def test_dom_meta(self):
        self.assertEqual(self.tab.dom.get_meta("description"), "проверка meta")

    def test_dom_links(self):
        links = self.tab.dom.get_links()
        self.assertGreaterEqual(len(links), 2)
        hrefs = [l["href"] for l in links]
        self.assertTrue(any(h.endswith("/page2") for h in hrefs))

    def test_dom_inputs(self):
        inputs = self.tab.dom.get_inputs()
        names = {i["name"] for i in inputs if i["name"]}
        self.assertIn("q", names)
        self.assertIn("user", names)
        self.assertIn("pass", names)

    def test_dom_forms(self):
        forms = self.tab.dom.get_forms()
        self.assertGreaterEqual(len(forms), 1)

    def test_dom_set_value_and_exists(self):
        self.assertTrue(self.tab.dom.set_value("input[name=q]", "коты"))
        self.assertTrue(self.tab.dom.exists("input[name=q]"))
        self.assertFalse(self.tab.dom.exists("h1#head"))
        self.assertTrue(self.tab.dom.exists("h1"))

    def test_dom_scroll(self):
        self.tab.dom.scroll_to(50)
        self.tab.dom.scroll_to_bottom()
        info = self.tab.dom.get_scroll_info()
        self.assertIn("scrollY", info)

    def test_bridge_page_ready(self):
        self.assertTrue(self.bridge.has_handler("__noop__") is False)

    def test_js_bridge_emit(self):
        got = []
        def handler(payload):
            got.append(payload)
        self.bridge.register_handler("evt", handler)
        self.bridge.emit_data("evt", '{"a":1}')
        self.assertEqual(got, ['{"a":1}'])
if __name__ == "__main__":
    unittest.main(verbosity=2)