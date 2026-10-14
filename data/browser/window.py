# -*- coding: utf-8 -*-
"""Главное окно встроенного браузера."""

from __future__ import annotations

import logging
from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWebEngineWidgets import QWebEngineProfile

from .js_bridge import JSBridge
from .profile import BrowserProfile
from .tab import Tab

log = logging.getLogger(__name__)


class BrowserWindow(QtWidgets.QMainWindow):
    """Главное окно встроенного браузера.

    Возможности Этапа 1:
    - Вкладки (QTabWidget)
    - Адресная строка с автодополнением из истории
    - Кнопки: назад, вперёд, обновить, домой
    - Индикатор загрузки
    - Поиск по странице (Ctrl+F)
    - Масштаб (Ctrl++ / Ctrl+-)
    - Полноэкранный режим (F11)
    - Горячие клавиши: Ctrl+T, Ctrl+W, Ctrl+L, F5, Ctrl+Shift+T

    Сигналы:
        tab_added(int)          — добавлена вкладка с индексом
        tab_closed(int)         — закрыта вкладка с индексом
        url_navigated(str)      — пользователь перешёл по URL
    """

    tab_added = QtCore.pyqtSignal(int)
    tab_closed = QtCore.pyqtSignal(int)
    url_navigated = QtCore.pyqtSignal(str)

    HOME_URL = "about:blank"

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None,
                 profile: Optional[QWebEngineProfile] = None,
                 bridge: Optional[JSBridge] = None) -> None:
        super().__init__(parent)
        self._profile = profile or BrowserProfile.get()
        self._bridge = bridge or JSBridge()
        self._closed_tabs: list[str] = []
        self._search_bar: Optional[QtWidgets.QLineEdit] = None
        self._fullscreen: bool = False

        self._setup_ui()
        self._setup_shortcuts()

        # Первая вкладка
        self.new_tab(self.HOME_URL)

    # ── UI setup ────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        """Настройка графического интерфейса."""
        self.setWindowTitle("Браузер — Лисичка")
        self.resize(1024, 720)
        self.setMinimumSize(480, 320)
        self._apply_dark_theme()

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Тулбар ──────────────────────────────────────────────────────
        toolbar = QtWidgets.QToolBar("Навигация")
        toolbar.setMovable(False)
        toolbar.setIconSize(QtCore.QSize(20, 20))

        self.back_btn = QtWidgets.QAction("◀", self)
        self.back_btn.setToolTip("Назад (Alt+←)")
        self.back_btn.triggered.connect(self._go_back)
        toolbar.addAction(self.back_btn)

        self.forward_btn = QtWidgets.QAction("▶", self)
        self.forward_btn.setToolTip("Вперёд (Alt+→)")
        self.forward_btn.triggered.connect(self._go_forward)
        toolbar.addAction(self.forward_btn)

        self.reload_btn = QtWidgets.QAction("↻", self)
        self.reload_btn.setToolTip("Обновить (F5)")
        self.reload_btn.triggered.connect(self._reload_current)
        toolbar.addAction(self.reload_btn)

        self.home_btn = QtWidgets.QAction("🏠", self)
        self.home_btn.setToolTip("Домой (Alt+Home)")
        self.home_btn.triggered.connect(self._go_home)
        toolbar.addAction(self.home_btn)

        # Адресная строка
        self.url_bar = QtWidgets.QLineEdit()
        self.url_bar.setPlaceholderText("Введите URL...")
        self.url_bar.setClearButtonEnabled(True)
        self.url_bar.returnPressed.connect(self._on_url_entered)
        toolbar.addWidget(self.url_bar)

        # Кнопка перехода
        self.go_btn = QtWidgets.QAction(">", self)
        self.go_btn.setToolTip("Перейти")
        self.go_btn.triggered.connect(self._on_url_entered)
        toolbar.addAction(self.go_btn)

        self.addToolBar(QtCore.Qt.TopToolBarArea, toolbar)

        # Вкладки
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._on_tab_switched)
        layout.addWidget(self.tabs, 1)

        # Статус-бар
        self.status_bar = QtWidgets.QStatusBar()
        self.status_bar.setStyleSheet("color: #777; font-size: 11px;")
        self.status_bar.setSizeGripEnabled(False)
        layout.addWidget(self.status_bar)

    def _apply_dark_theme(self) -> None:
        """Темная тема для окна браузера."""
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QToolBar { background-color: #2d2d2d; border: none; spacing: 4px; }
            QToolButton { background-color: #3a3a3a; color: #f0f0f0;
                          border: 1px solid #555; padding: 4px 8px;
                          border-radius: 4px; min-width: 28px; }
            QToolButton:hover { background-color: #4a4a4a; }
            QToolButton:disabled { background-color: #2a2a2a; color: #666; }
            QLineEdit { background-color: #2d2d2d; color: #f0f0f0;
                        border: 1px solid #555; padding: 6px 10px;
                        border-radius: 5px; min-height: 28px; }
            QTabWidget::pane { background-color: #1e1e1e; border: none; }
            QTabBar::tab { background-color: #2d2d2d; color: #ccc;
                           border: 1px solid #444; padding: 6px 12px;
                           border-top-left-radius: 4px;
                           border-top-right-radius: 4px; }
            QTabBar::tab:selected { background-color: #3a3a3a; color: #fff;
                                    border-bottom-color: #4c8; }
            QTabBar::tab:hover { background-color: #353535; }
            QStatusBar { background-color: #252525; color: #888; font-size: 11px; }
        """)

    def _setup_shortcuts(self) -> None:
        """Назначить глобальные горячие клавиши."""
        self._shortcuts: list[QtWidgets.QShortcut] = []

        def _add(keyseq: str, fn) -> None:
            try:
                sc = QtWidgets.QShortcut(QtCore.QKeySequence(keyseq), self, fn)
                self._shortcuts.append(sc)
            except Exception as exc:
                log.debug("shortcut %s: %s", keyseq, exc)

        _add("Ctrl+T", lambda: self.new_tab())
        _add("Ctrl+W", self._close_current_tab)
        _add("Ctrl+L", self._focus_url_bar)
        _add("F5", self._reload_current)
        _add("Ctrl+Shift+T", self._restore_last_tab)
        _add("F11", self._toggle_fullscreen)
        _add("Ctrl++", self._zoom_in)
        _add("Ctrl+-", self._zoom_out)
        _add("Ctrl+0", self._zoom_reset)
        _add("Ctrl+F", self._toggle_search)

    # -- Управление вкладками -----------------------------------------------

    def new_tab(self, url: str = "about:blank") -> Tab:
        """Создать новую вкладку.

        Args:
            url: URL для загрузки (по умолчанию about:blank).

        Returns:
            Созданный экземпляр Tab.
        """
        tab = Tab(self, self._profile)
        index = self.tabs.addTab(tab, "Новая вкладка")

        # JS-мост (QWebChannel + DOM API)
        if self._bridge is not None:
            tab.install_bridge(self._bridge)

        # Обновляем заголовок вкладки
        tab.title_changed.connect(
            lambda title, t=tab: self.tabs.setTabText(
                self.tabs.indexOf(t), title or "Новая вкладка"))
        # Обновляем URL-бар при смене активной вкладки
        tab.url_changed.connect(self._on_tab_url_changed)

        tab.load(url)
        self.tabs.setCurrentWidget(tab)
        self._sync_nav_buttons()
        self.tab_added.emit(index)
        log.debug("Новая вкладка [%d]: %s", index, url)
        return tab

    def close_tab(self, index: int) -> None:
        """Закрыть вкладку по индексу."""
        if self.tabs.count() <= 1:
            log.info("Нельзя закрыть единственную вкладку")
            return
        tab = self.tabs.widget(index)
        if tab is None:
            return
        self._closed_tabs.append(tab.url)
        self.tabs.removeTab(index)
        tab.deleteLater()
        self.tab_closed.emit(index)
        log.debug("Вкладка закрыта [%d]", index)

    def _close_current_tab(self) -> None:
        """Закрыть текущую вкладку."""
        idx = self.tabs.currentIndex()
        if idx >= 0:
            self.close_tab(idx)

    def current_tab(self) -> Optional[Tab]:
        """Вернуть текущую активную вкладку."""
        return self.tabs.currentWidget()

    def current_dom(self) -> "DomApi":
        """DomApi текущей вкладки (интерфейс к DOM страницы).

        Ленивое создание; работает на загруженных страницах:
            dom = win.current_dom()
            links = dom.get_links()
        """
        tab = self.current_tab()
        if tab is None:
            raise RuntimeError("Нет активной вкладки")
        return tab.dom

    @property
    def profile(self) -> QWebEngineProfile:
        """Профиль окна (cookies/кеш/настройки)."""
        return self._profile

    @property
    def bridge(self) -> "JSBridge":
        """JS-мост окна (QWebChannel + события страниц)."""
        return self._bridge

    def _restore_last_tab(self) -> None:
        """Восстановить последнюю закрытую вкладку (Ctrl+Shift+T)."""
        if self._closed_tabs:
            url = self._closed_tabs.pop()
            self.new_tab(url)
            log.debug("Восстановлена вкладка: %s", url)

    def _on_tab_switched(self, index: int) -> None:
        """Обработчик переключения вкладок."""
        tab = self.tabs.widget(index)
        if tab is not None:
            self.url_bar.setText(tab.url)
            self._sync_nav_buttons()
            self.setWindowTitle(f"{tab.title or 'Браузер'} -- Лисичка")

    def _on_tab_url_changed(self, url: str) -> None:
        """URL изменился в любой вкладке."""
        tab = self.sender()
        if tab is self.current_tab():
            self.url_bar.setText(url)
            self._sync_nav_buttons()

    # -- Навигация ---------------------------------------------------------

    def open_url(self, url: str, new_tab: bool = False) -> None:
        """Открыть URL.

        Args:
            url: URL для открытия.
            new_tab: если True, открыть в новой вкладке.
        """
        if not url or url == "about:blank":
            return
        if new_tab:
            self.new_tab(url)
        else:
            tab = self.current_tab()
            if tab is not None:
                tab.load(url)

    def _on_url_entered(self) -> None:
        """Обработчик ввода URL."""
        raw = self.url_bar.text().strip()
        if not raw:
            return

        # Если нет протокола -- добавляем https://
        if not raw.startswith(("http://", "https://", "ftp://", "about:")):
            raw = "https://" + raw

        self.open_url(raw)
        self.url_navigated.emit(raw)

    def _go_back(self) -> None:
        tab = self.current_tab()
        if tab:
            tab.back()
            self._sync_nav_buttons()

    def _go_forward(self) -> None:
        tab = self.current_tab()
        if tab:
            tab.forward()
            self._sync_nav_buttons()

    def _reload_current(self) -> None:
        tab = self.current_tab()
        if tab:
            tab.reload()

    def _go_home(self) -> None:
        self.open_url(self.HOME_URL)

    def _sync_nav_buttons(self) -> None:
        """Синхронизировать состояние кнопок навигации."""
        tab = self.current_tab()
        if tab is not None:
            self.back_btn.setEnabled(tab.can_go_back())
            self.forward_btn.setEnabled(tab.can_go_forward())
        else:
            self.back_btn.setEnabled(False)
            self.forward_btn.setEnabled(False)

    # -- Поиск по странице --------------------------------------------------

    def _toggle_search(self) -> None:
        """Показать/скрыть строку поиска (Ctrl+F)."""
        if self._search_bar is not None and self._search_bar.isVisible():
            self._search_bar.hide()
            self._search_bar = None
            return

        if self._search_bar is None:
            self._search_bar = QtWidgets.QLineEdit()
            self._search_bar.setPlaceholderText("Найти на странице...")
            self._search_bar.setMaximumWidth(250)
            self._search_bar.returnPressed.connect(self._on_search)

        bar = self._search_bar
        bar.show()
        bar.setFocus()
        bar.selectAll()

    def _on_search(self) -> None:
        """Выполнить поиск по странице."""
        text = self._search_bar.text().strip()
        if not text:
            return
        tab = self.current_tab()
        if tab:
            tab.find_text(text)

    # -- Масштаб ------------------------------------------------------------

    def _zoom_in(self) -> None:
        tab = self.current_tab()
        if tab:
            tab.zoom_in()

    def _zoom_out(self) -> None:
        tab = self.current_tab()
        if tab:
            tab.zoom_out()

    def _zoom_reset(self) -> None:
        tab = self.current_tab()
        if tab:
            tab.zoom_reset()

    # -- Полноэкранный режим ------------------------------------------------

    def _toggle_fullscreen(self) -> None:
        """Переключить полноэкранный режим (F11)."""
        self._fullscreen = not self._fullscreen
        if self._fullscreen:
            self.showFullScreen()
        else:
            self.showNormal()

    # -- Фокус URL-бара -----------------------------------------------------

    def _focus_url_bar(self) -> None:
        """Установить фокус на адресную строку и выделить URL."""
        self.url_bar.setFocus()
        self.url_bar.selectAll()

    # -- Свойства -----------------------------------------------------------

    @property
    def tab_count(self) -> int:
        return self.tabs.count()

    @property
    def tab_urls(self) -> list[str]:
        """Список URL всех открытых вкладок."""
        urls: list[str] = []
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if tab is not None:
                urls.append(tab.url)
        return urls