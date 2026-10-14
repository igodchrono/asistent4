# -*- coding: utf-8 -*-
"""Вкладка браузера — обёртка над QWebEngineView."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage, QWebEngineSettings

log = logging.getLogger(__name__)


class Tab(QtWidgets.QWidget):
    """Одна вкладка встроенного браузера.

    Содержит:
    - QWebEngineView для рендеринга страниц,
    - прогресс-бар загрузки,
    - сигналы об изменении заголовка/URL/прогресса.

    Сигналы:
        title_changed(str)     — заголовок страницы изменился
        url_changed(str)       — URL изменился
        load_progress(int)     — 0..100 процент загрузки
        load_finished(bool)    — загрузка завершена (ok=True при успехе)
        link_hovered(str)      — ссылка под курсором
    """

    title_changed = QtCore.pyqtSignal(str)
    url_changed = QtCore.pyqtSignal(str)
    load_progress = QtCore.pyqtSignal(int)
    load_finished = QtCore.pyqtSignal(bool)
    link_hovered = QtCore.pyqtSignal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None,
                 profile: Optional["QWebEngineProfile"] = None) -> None:
        super().__init__(parent)
        self._bridge = None  # JSBridge — устанавливается через install_bridge()
        self._dom_api = None  # DomApi — создаётся лениво через свойство dom
        self._progress: int = 0
        self._loaded_ok: bool = False
        self._title: str = ""
        self._url: str = "about:blank"

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Прогресс-бар (полоска сверху, как в Chrome)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setStyleSheet(
            "QProgressBar { background-color: #2d2d2d; border: none; }"
            "QProgressBar::chunk { background-color: #4c8; }"
        )
        layout.addWidget(self.progress_bar)

        # WebEngine view
        self.view = QWebEngineView()
        if profile is not None:
            self.view.setPage(QWebEnginePage(profile, self.view))

        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.ImagesEnabled, True)
        settings.setAttribute(QWebEngineSettings.PluginsEnabled, True)

        # Подключаем сигналы QWebEngine
        page = self.view.page()
        page.titleChanged.connect(self._on_title_changed)
        page.urlChanged.connect(self._on_url_changed)
        page.loadProgress.connect(self._on_load_progress)
        page.loadFinished.connect(self._on_load_finished)
        page.linkHovered.connect(self._on_link_hovered)

        layout.addWidget(self.view, 1)

        # Начальная страница
        self.view.load(QtCore.QUrl("about:blank"))

    # ── свойства ────────────────────────────────────────────────────────

    @property
    def title(self) -> str:
        return self._title

    @property
    def url(self) -> str:
        return self._url

    @property
    def progress(self) -> int:
        return self._progress

    @property
    def loaded_ok(self) -> bool:
        return self._loaded_ok

    # ── внутренние обработчики сигналов QWebEngine ──────────────────────

    @QtCore.pyqtSlot(str)
    def _on_title_changed(self, title: str) -> None:
        self._title = title
        self.title_changed.emit(title)

    @QtCore.pyqtSlot(QtCore.QUrl)
    def _on_url_changed(self, qurl: QtCore.QUrl) -> None:
        url_str = qurl.toString()
        self._url = url_str
        self.url_changed.emit(url_str)

    @QtCore.pyqtSlot(int)
    def _on_load_progress(self, value: int) -> None:
        self._progress = value
        self.progress_bar.setValue(value)
        if value >= 100:
            self.progress_bar.setMaximum(0)
            self.progress_bar.setMaximum(100)
        self.load_progress.emit(value)

    @QtCore.pyqtSlot(bool)
    def _on_load_finished(self, ok: bool) -> None:
        self._loaded_ok = ok
        self.progress_bar.setValue(100 if ok else 0)
        self.load_finished.emit(ok)

    @QtCore.pyqtSlot(str)
    def _on_link_hovered(self, url: str) -> None:
        self.link_hovered.emit(url)

    # ── публичные методы ────────────────────────────────────────────────

    def load(self, url: str) -> None:
        """Загрузить URL."""
        qurl = QtCore.QUrl(url)
        if not qurl.isValid():
            log.warning("Некорректный URL: %s", url)
            return
        self.view.load(qurl)

    def reload(self) -> None:
        """Обновить страницу."""
        self.view.reload()

    def back(self) -> None:
        """Назад в истории навигации."""
        page = self.view.page()
        history = page.history()
        if history.canGoBack():
            history.back()

    def forward(self) -> None:
        """Вперёд в истории навигации."""
        page = self.view.page()
        history = page.history()
        if history.canGoForward():
            history.forward()

    def stop(self) -> None:
        """Остановить загрузку."""
        self.view.stop()

    def can_go_back(self) -> bool:
        """Можно ли вернуться назад."""
        return self.view.page().history().canGoBack()

    def can_go_forward(self) -> bool:
        """Можно ли перейти вперёд."""
        return self.view.page().history().canGoForward()

    def find_text(self, text: str) -> None:
        """Поиск текста на странице (Ctrl+F)."""
        self.view.findText(text)

    def set_zoom(self, factor: float) -> None:
        """Установить масштаб страницы (1.0 = 100%)."""
        self.view.setZoomFactor(factor)

    def zoom_in(self) -> None:
        """Приблизить (шаг +10%)."""
        current = self.view.zoomFactor()
        self.view.setZoomFactor(min(3.0, current + 0.1))

    def zoom_out(self) -> None:
        """Отдалить (шаг -10%)."""
        current = self.view.zoomFactor()
        self.view.setZoomFactor(max(0.2, current - 0.1))

    def zoom_reset(self) -> None:
        """Сбросить масштаб (100%)."""
        self.view.setZoomFactor(1.0)

    def screenshot(self) -> QtGui.QImage:
        """Скриншот текущего состояния страницы."""
        if hasattr(self.view.page(), "grab"):
            return self.view.page().grab()
        return QtGui.QImage()

    def run_js(self, script: str,
               callback: Optional[Callable[[str], None]] = None) -> None:
        """Выполнить JavaScript на странице.

        Args:
            script: JS-код для выполнения.
            callback: вызывается с результатом (str) или None.
        """
        if callback is None:
            self.view.page().runJavaScript(script)
        else:
            self.view.page().runJavaScript(script, callback)

    def wait_load(self, timeout: float = 30.0) -> bool:
        """Блокирующее ожидание загрузки страницы (для тестов).

        Использует QApplication.processEvents().

        Returns:
            True если страница загружена, False по таймауту.
        """
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.processEvents()
            if self._loaded_ok:
                return True
            time.sleep(0.05)
        return False

    # ── Мост и DOM API ────────────────────────────────────────────────────

    def install_bridge(self, bridge) -> None:
        """Установить JS-мост на страницу (QWebChannel + скрипты).

        Args:
            bridge: экземпляр browser.js_bridge.JSBridge.

        Вызывается window при создании вкладки (до загрузки контента).
        """
        from .js_bridge import install_on_page
        self._bridge = bridge
        install_on_page(self.view.page(), bridge)

    @property
    def dom(self) -> "DomApi":
        """DomApi — python-интерфейс к DOM текущей страницы.

        Создаётся лениво при первом обращении. Работает через
        внедрённый js/dom_api.js (окно.lisichka).
        """
        if self._dom_api is None:
            from .dom_api import DomApi
            self._dom_api = DomApi(self)
        return self._dom_api