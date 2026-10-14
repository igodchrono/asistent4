# -*- coding: utf-8 -*-
"""Мост Python <-> JavaScript на базе QWebChannel.

Что делает этот модуль:
1. Объявляет JSBridge — QObject, доступный страницам как `window.lisichka_bridge`.
2. Внедряет в страницы два скрипта:
   - qwebchannel.js (клиент QWebChannel, из ресурсов Qt),
   - js/dom_api.js (DOM API, объект `window.lisichka`).

Использование:
    from browser.js_bridge import JSBridge, install_on_page
    bridge = JSBridge()
    install_on_page(page, bridge)      # один раз на страницу (до нагрузки)
    bridge.page_ready.connect(handler) # страница загружена и канал поднят
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PyQt5 import QtCore
from PyQt5.QtCore import QFile, QIODevice
from PyQt5.QtWebChannel import QWebChannel

log = logging.getLogger(__name__)


def _js_abs_path() -> str:
    """Абсолютный путь к каталогу js/ пакета."""
    import os
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "js")


def load_qwebchannel_source() -> str:
    """Содержимое клиента QWebChannel (встроен в Qt)."""
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.open(QIODevice.ReadOnly):
        log.warning("Не удалось открыть :/qtwebchannel/qwebchannel.js")
        return ""
    try:
        return bytes(f.readAll()).decode("utf-8", errors="replace")
    finally:
        f.close()


def load_dom_api_source() -> str:
    """Содержимое js/dom_api.js из пакета browser."""
    import os
    path = os.path.join(_js_abs_path(), "dom_api.js")
    if not os.path.isfile(path):
        log.warning("dom_api.js не найден: %s", path)
        return ""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _make_script(name: str, source: str,
                 injection_point: "QWebEngineScript.InjectionPoint",
                 world_id: int) -> "QWebEngineScript":
    """Создать QWebEngineScript из исходного кода."""
    from PyQt5.QtWebEngineWidgets import QWebEngineScript
    script = QWebEngineScript()
    script.setName(name)
    script.setSourceCode(source)
    script.setInjectionPoint(injection_point)
    script.setWorldId(world_id)
    script.setRunsOnSubFrames(True)
    return script


def install_scripts(page: "QWebEnginePage") -> None:
    """Добавить в страницу скрипты qwebchannel.js и dom_api.js.

    Безопасно вызывать повторно (скрипты не дублируются).
    """
    from PyQt5.QtWebEngineWidgets import QWebEngineScript
    scripts = page.scripts()
    names = {s.name() for s in scripts.toList()}

    if "lisichka_qwebchannel" not in names:
        src = load_qwebchannel_source()
        if src:
            scripts.insert(_make_script(
                "lisichka_qwebchannel", src,
                QWebEngineScript.DocumentCreation,
                QWebEngineScript.MainWorld))

    if "lisichka_dom_api" not in names:
        src = load_dom_api_source()
        if src:
            scripts.insert(_make_script(
                "lisichka_dom_api", src,
                QWebEngineScript.DocumentReady,
                QWebEngineScript.MainWorld))


class JSBridge(QtCore.QObject):
    """Мост Python <-> JavaScript.

    На странице доступен как `window.lisichka_bridge` (после
    инициализации QWebChannel в dom_api.js).

    Сигналы:
        page_ready(str)   — страница загрузилась и канал работает (передан URL)
        log_message(str, str) — JS-лог (уровень, сообщение)
        data_message(str, str) — произвольное событие из JS (тип, payload)
    """

    page_ready = QtCore.pyqtSignal(str)
    log_message = QtCore.pyqtSignal(str, str)
    data_message = QtCore.pyqtSignal(str, str)

    def __init__(self, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self._handlers: dict[str, Callable[[str], None]] = {}

    # ── Слоты, вызываемые из JavaScript ──────────────────────────────────

    @QtCore.pyqtSlot(str, str)
    def log(self, level: str, message: str) -> None:
        """Лог из JavaScript (level: debug/info/warning/error)."""
        level = (level or "info").lower()
        if level == "error":
            log.error("[JS] %s", message)
        elif level == "warning":
            log.warning("[JS] %s", message)
        elif level == "debug":
            log.debug("[JS] %s", message)
        else:
            log.info("[JS] %s", message)
        self.log_message.emit(level, message)

    @QtCore.pyqtSlot(str)
    def notify_loaded(self, url: str) -> None:
        """Страница загружена; канал поднят."""
        log.debug("JS-мост страницы готов: %s", url)
        self.page_ready.emit(url)

    @QtCore.pyqtSlot(str, str)
    def emit_data(self, handler: str, payload: str) -> None:
        """Событие из JS в Python.

        Args:
            handler: имя зарегистрированного обработчика.
            payload: JSON-строка с данными.
        """
        fn = self._handlers.get(handler)
        if fn is not None:
            try:
                fn(payload)
            except Exception as exc:  # noqa: BLE001
                log.exception("Обработчик JS-события %s упал: %s",
                              handler, exc)
        else:
            log.debug("JS-событие без обработчика: %s", handler)
        self.data_message.emit(handler, payload)

    # ── Реестр обработчиков JS-событий ──────────────────────────────────

    def register_handler(self, name: str,
                         fn: Callable[[str], None]) -> None:
        """Зарегистрировать Python-обработчик для JS-события."""
        self._handlers[name] = fn

    def unregister_handler(self, name: str) -> None:
        """Убрать обработчик JS-события."""
        self._handlers.pop(name, None)

    def has_handler(self, name: str) -> bool:
        return name in self._handlers


def install_on_page(page: "QWebEnginePage",
                    bridge: Optional[JSBridge] = None) -> Optional[QWebChannel]:
    """Подключить мост к странице.

    Args:
        page: страница QWebEnginePage.
        bridge: JSBridge (создаётся новый, если не передан).

    Returns:
        QWebChannel или None при неудаче.
    """
    install_scripts(page)

    bridge = bridge or JSBridge()
    try:
        channel = QWebChannel(page)
        channel.registerObject("lisichka", bridge)
        page.setWebChannel(channel)
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось включить QWebChannel: %s", exc)
        return None
    log.debug("Мост подключён к странице")
    return channel