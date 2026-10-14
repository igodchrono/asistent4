# -*- coding: utf-8 -*-
"""Пакет встроенного браузера на QtWebEngine (Chromium внутри PyQt5).

Использование:
    from browser import BrowserWindow, BrowserProfile
    bw = BrowserWindow()
    bw.new_tab("https://example.com")
    bw.show()

Доступ к DOM страницы:
    dom = bw.current_dom()
    links = dom.get_links()
    dom.set_value("input[name=q]", "погода")
    dom.submit("form")
"""

from __future__ import annotations

from .dom_api import DomApi
from .js_bridge import JSBridge
from .profile import BrowserProfile
from .tab import Tab
from .window import BrowserWindow

__all__ = [
    "BrowserWindow",
    "BrowserProfile",
    "Tab",
    "JSBridge",
    "DomApi",
]