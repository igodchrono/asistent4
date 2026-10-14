# -*- coding: utf-8 -*-
"""Постоянный профиль браузера «Лисичка»: cookies, кеш, настройки.

Обёртка над QWebEngineProfile с именованным хранилищем данных.
Данные сохраняются в каталог `data/browser/storage/`
(автоматически создаётся при первом использовании).

Использование:
    from browser.profile import BrowserProfile
    profile = BrowserProfile.get()
    tab = Tab(profile=profile)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from PyQt5 import QtCore
from PyQt5.QtNetwork import QNetworkCookie
from PyQt5.QtWebEngineWidgets import QWebEngineProfile, QWebEngineSettings

log = logging.getLogger(__name__)

STORAGE_NAME = "lisichka"
STORAGE_ROOT = Path(__file__).resolve().parent / "storage"

# Жёсткий лимит кеша (МБ) — 100 МБ по умолчанию
DEFAULT_CACHE_MB = 100


class BrowserProfile(QWebEngineProfile):
    """Постоянный профиль QtWebEngine.

    Значения по умолчанию, применяемые к каждому профилю:
    - JavaScript включён
    - Картинки и плагины включены
    - LocalStorage включён
    - Cookies сохраняются принудительно (ForcePersistentCookies)
    - Каталог кеша/истории — data/browser/storage/<имя>
    """

    _default: Optional["BrowserProfile"] = None

    def __init__(self, parent: Optional[QtCore.QObject] = None,
                 storage_name: str = STORAGE_NAME,
                 storage_dir: Optional[Path] = None,
                 off_the_record: bool = False) -> None:
        """Создать профиль.

        Args:
            parent: родительский QObject (владелец).
            storage_name: имя хранилища (пишется в путь данных).
            storage_dir: корневой каталог для данных; по умолчанию
                data/browser/storage.
            off_the_record: True — данные не сохраняются.
        """
        # Хранение по имени: Qt сам сохраняет cookies/кеш/HTML5-данные
        super().__init__(storage_name, parent)

        self._storage_root = Path(storage_dir) if storage_dir else STORAGE_ROOT
        self._storage_root.mkdir(parents=True, exist_ok=True)

        if not off_the_record:
            self.setPersistentStoragePath(str(self._storage_root / "webdata"))
            self.setHttpCacheMaximumSize(DEFAULT_CACHE_MB * 1024 * 1024)
            # Принудительно сохраняем cookies между запусками
            self.setPersistentCookiesPolicy(
                QWebEngineProfile.ForcePersistentCookies)
            self._init_storage_dirs()

        self._apply_default_settings()

        if self.isOffTheRecord():
            log.debug("Профиль %s: off-the-record (данные не пишутся)",
                      storage_name)
        else:
            log.debug("Профиль %s: хранилище %s",
                      storage_name, self._storage_root)

    # ── Фабричный метод ────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> "BrowserProfile":
        """Вернуть глобальный профиль (синглтон процесса)."""
        if cls._default is None:
            cls._default = cls()
        return cls._default

    # ── Настройка хранилища ───────────────────────────────────────────────

    def _init_storage_dirs(self) -> None:
        """Создать подкаталоги данных (если их нет)."""
        webdata = self._storage_root / "webdata"
        webdata.mkdir(parents=True, exist_ok=True)

    def _apply_default_settings(self) -> None:
        """Применить стандартные настройки к страницам профиля."""
        s = self.settings()
        s.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, True)
        s.setAttribute(QWebEngineSettings.JavascriptCanAccessClipboard, False)
        s.setAttribute(QWebEngineSettings.ImagesEnabled, True)
        s.setAttribute(QWebEngineSettings.AutoLoadImages, True)
        s.setAttribute(QWebEngineSettings.PluginsEnabled, True)
        s.setAttribute(QWebEngineSettings.LocalStorageEnabled, True)
        s.setAttribute(QWebEngineSettings.FullScreenSupportEnabled, True)
        s.setAttribute(QWebEngineSettings.ErrorPageEnabled, True)
        s.setAttribute(QWebEngineSettings.HyperlinkAuditingEnabled, False)
        s.setAttribute(QWebEngineSettings.ShowScrollBars, True)

    # ── Свойства ──────────────────────────────────────────────────────────

    @property
    def storage_root(self) -> Path:
        """Корневой каталог данных профиля."""
        return self._storage_root

    @property
    def is_off_the_record(self) -> bool:
        """Профиль не сохраняет данные на диск."""
        return self.isOffTheRecord()

    @property
    def user_agent(self) -> str:
        """Текущий User-Agent."""
        return self.httpUserAgent()

    @user_agent.setter
    def user_agent(self, ua: str) -> None:
        """Переопределить User-Agent."""
        if ua:
            self.setHttpUserAgent(ua)
            log.debug("User-Agent → %s", ua)

    # ── Управление данными ────────────────────────────────────────────────

    def clear_cache(self) -> None:
        """Очистить HTTP-кеш профиля."""
        self.clearHttpCache()
        log.debug("HTTP-кеш очищен")

    def clear_cookies(self) -> None:
        """Удалить все cookies профиля."""
        self.cookieStore().deleteAllCookies()
        log.debug("Все cookies удалены")

    def delete_cookie(self, name: Optional[str] = None,
                      domain: Optional[str] = None) -> None:
        """Удалить cookies по имени и/или домену.

        Args:
            name: имя cookie (или None для любого).
            domain: домен cookie (или None).
        """
        if name is None and domain is None:
            self.clear_cookies()
            return

        def _on_cookie(cookie: QNetworkCookie) -> None:
            try:
                matches = True
                if name is not None:
                    matches = matches and cookie.name().data().decode(
                        "utf-8", "replace") == name
                if domain is not None:
                    cdomain = cookie.domain().data().decode("utf-8", "replace")
                    matches = matches and (domain in cdomain
                                           or cdomain in domain)
                if matches:
                    self.cookieStore().deleteCookie(cookie)
            except Exception as exc:  # noqa: BLE001
                log.debug("delete_cookie: %s", exc)

        store = self.cookieStore()
        store.loadAllCookies()
        try:
            store.cookieAdded.connect(_on_cookie)
        except Exception as exc:  # noqa: BLE001
            log.debug("cookieAdded connect error: %s", exc)

    def list_cookies(self, callback: Callable[[list[dict]], None]) -> None:
        """Асинхронно получить список cookies.

        Args:
            callback: вызвается со списком словарей
                [{name, domain, path, value, secure, httponly}].
        """
        store = self.cookieStore()
        cookies: list[dict] = []
        seen: set[bytes] = set()

        def _on_cookie(cookie: QNetworkCookie) -> None:
            raw = cookie.name().data() + b"\x00" + cookie.domain().data()
            if raw in seen:
                return
            seen.add(raw)
            try:
                cookies.append({
                    "name": cookie.name().data().decode("utf-8", "replace"),
                    "domain": cookie.domain().data().decode("utf-8", "replace"),
                    "path": cookie.path().data().decode("utf-8", "replace"),
                    "value": cookie.value().data().decode("utf-8", "replace"),
                    "secure": cookie.isSecure(),
                    "httponly": cookie.isHttpOnly(),
                })
            except Exception as exc:  # noqa: BLE001
                log.debug("list_cookies item: %s", exc)

        # Куки приходят пачкой — ждём паузы, затем отдаём результат
        timer = QtCore.QTimer()
        timer.setSingleShot(True)

        def _finish() -> None:
            try:
                store.cookieAdded.disconnect(_on_cookie)
            except Exception:  # noqa: BLE001
                pass
            callback(cookies)

        store.cookieAdded.connect(_on_cookie)
        timer.timeout.connect(_finish)
        timer.start(600)
        store.loadAllCookies()

    def clear_all(self) -> None:
        """Полная очистка кеша и cookies."""
        self.clear_cache()
        self.clear_cookies()
        log.debug("Все данные профиля очищены")