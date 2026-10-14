# -*- coding: utf-8 -*-
"""Python-интерфейс к DOM страницы через внедрённый js/dom_api.js.

DomApi вызывает функции `window.lisichka` (см. data/browser/js/dom_api.js)
через QWebEnginePage.runJavaScript и возвращает готовые Python-структуры.

Синхронные методы блокируют поток до ответа (внутри крутится локальный
QEventLoop). Для длинных операций используйте `wait_for_selector`.

Пример:
    tab = ...
    dom = DomApi(tab)
    title = dom.get_title()
    links = dom.get_links()
    dom.set_value("input[name=q]", "например: погода")
    dom.submit("form")
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from PyQt5 import QtCore, QtWidgets

log = logging.getLogger(__name__)

_ERR_PREFIX = "LISICHKA_ERR:"


class JsError(RuntimeError):
    """Ошибка исполнения JavaScript (или таймаут ответа)."""


class DomApi:
    """Работа с DOM открытой страницы."""

    def __init__(self, tab: Any, timeout: float = 10.0) -> None:
        """
        Args:
            tab: экземпляр browser.tab.Tab (или любой объект с .view.page()).
            timeout: максимальное время ожидания ответа JS (сек).
        """
        self._tab = tab
        self._timeout = timeout

    # ── Свойства ──────────────────────────────────────────────────────────

    @property
    def tab(self) -> Any:
        """Вкладка, к которой привязан DomApi."""
        return self._tab

    @property
    def timeout(self) -> float:
        return self._timeout

    @timeout.setter
    def timeout(self, value: float) -> None:
        self._timeout = max(0.1, float(value))

    def _page(self):
        return self._tab.view.page()

    # ── Низкоуровневые вызовы JS ──────────────────────────────────────────

    def _wrap(self, expression: str) -> str:
        """Обернуть JS-выражение в try/catch, возвращающий JSON-строку."""
        return ("(function(){ "
                "try { return JSON.stringify(" + expression + "); } "
                "catch(e) { return '" + _ERR_PREFIX + "' + e; }"
                "})()")

    def call_async(self, expression: str,
                   callback: Any, timeout: Optional[float] = None) -> None:
        """Асинхронно выполнить JS (результат попадёт в callback).

        Args:
            expression: JS, возвращающий сериализуемое значение.
            callback: callable(value) — Python-значение или JsError.
            timeout: таймаут (сек), None — использовать self.timeout.
        """
        timeout = timeout if timeout is not None else self._timeout
        timer = QtCore.QTimer()
        timer.setSingleShot(True)

        def _on_timeout() -> None:
            try:
                timer.timeout.disconnect(_on_timeout)
            except Exception:  # noqa: BLE001
                pass
            callback(JsError(f"Таймаут JS ({timeout} c)"))

        def _on_result(raw: Any) -> None:
            timer.stop()
            try:
                timer.timeout.disconnect(_on_timeout)
            except Exception:  # noqa: BLE001
                pass
            if isinstance(raw, str) and raw.startswith(_ERR_PREFIX):
                callback(JsError(raw[len(_ERR_PREFIX):]))
                return
            try:
                callback(self._to_py(raw))
            except Exception as exc:  # noqa: BLE001
                callback(JsError(str(exc)))

        timer.timeout.connect(_on_timeout)
        timer.start(int(timeout * 1000))
        self._page().runJavaScript(self._wrap(expression), _on_result)

    def call(self, expression: str) -> Any:
        """Синхронно выполнить JS и вернуть результат.

        Args:
            expression: JS-выражение (без обёртки JSON.stringify).

        Returns:
            Python-значение результата (str, dict, list, bool, None...).
        """
        result: dict[str, Any] = {}
        done = QtCore.QEventLoop()
        timer = QtCore.QTimer()
        tm = self._timeout

        def _on_result(raw: Any) -> None:
            result["raw"] = raw
            done.quit()

        def _on_timeout() -> None:
            result["raw"] = JsError(f"Таймаут JS ({tm} c)")
            done.quit()

        timer.setSingleShot(True)
        timer.timeout.connect(_on_timeout)
        timer.start(int(tm * 1000))
        self._page().runJavaScript(self._wrap(expression), _on_result)
        done.exec_()
        timer.stop()
        raw = result.get("raw")

        if isinstance(raw, JsError):
            raise raw
        if isinstance(raw, str) and raw.startswith(_ERR_PREFIX):
            raise JsError(raw[len(_ERR_PREFIX):])
        return self._to_py(raw)

    def _to_py(self, raw: Any) -> Any:
        """Преобразовать JSON-строку JS в Python-значение."""
        if raw is None:
            return None
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (ValueError, TypeError):
                return raw
        return raw

    # ── Сведения о странице ───────────────────────────────────────────────

    def get_title(self) -> str:
        """Заголовок страницы (document.title)."""
        return self.call("window.lisichka ? window.lisichka.getTitle() : ''")

    def get_url(self) -> str:
        """Текущий URL (location.href)."""
        return self.call("window.lisichka ? window.lisichka.getUrl() : ''")

    def get_ready_state(self) -> str:
        """document.readyState (loading/interactive/complete)."""
        return self.call(
            "window.lisichka ? window.lisichka.getReadyState() : ''")

    def get_meta(self, name: str) -> str:
        """Содержимое meta-тега по name/property."""
        return self.call(
            "window.lisichka ? window.lisichka.getMeta("
            + json.dumps(name) + ") : ''")

    # ── Текст / HTML ──────────────────────────────────────────────────────

    def get_text(self, selector: Optional[str] = None) -> str:
        """Текст страницы (или элемента по CSS-селектору)."""
        if selector:
            return self.call("window.lisichka.getText("
                             + json.dumps(selector) + ")")
        return self.call("window.lisichka.getText(null)")

    def get_html(self, selector: Optional[str] = None) -> str:
        """HTML-код страницы (или элемента по CSS-селектору)."""
        if selector:
            return self.call("window.lisichka.getHtml("
                             + json.dumps(selector) + ")")
        return self.call("window.lisichka.getHtml(null)")

    def get_attribute(self, selector: str, name: str) -> str:
        """Значение атрибута элемента (например href, title)."""
        return self.call("window.lisichka.getAttribute("
                         + json.dumps(selector) + ", "
                         + json.dumps(name) + ")")

    # ── Ссылки и картинки ─────────────────────────────────────────────────

    def get_links(self, max_links: int = 1000) -> list[dict[str, str]]:
        """Список ссылок: [{href, text, title}]."""
        return self.call("window.lisichka.getLinks(" + str(max_links) + ")")

    def get_images(self, max_images: int = 500) -> list[dict[str, str]]:
        """Список картинок: [{src, alt}]."""
        return self.call("window.lisichka.getImages(" + str(max_images) + ")")

    # ── Формы и поля ──────────────────────────────────────────────────────

    def get_inputs(self) -> list[dict[str, str]]:
        """Список полей ввода: [{tag, type, name, id, placeholder, value}]."""
        return self.call("window.lisichka.getInputs()")

    def get_forms(self) -> list[dict]:
        """Список форм: [{action, method, fields:[...]}]."""
        return self.call("window.lisichka.getForms()")

    # ── Действия ──────────────────────────────────────────────────────────

    def click(self, selector: str) -> bool:
        """Кликнуть по элементу (el.click()). True если элемент найден."""
        return bool(self.call("window.lisichka.click("
                              + json.dumps(selector) + ")"))

    def set_value(self, selector: str, value: str) -> bool:
        """Заполнить поле ввода (с событиями input/change)."""
        return bool(self.call("window.lisichka.setValue("
                              + json.dumps(selector) + ", "
                              + json.dumps(value) + ")"))

    def submit(self, selector: Optional[str] = None) -> bool:
        """Отправить форму (или ближайшую к селектору)."""
        if selector:
            return bool(self.call("window.lisichka.submit("
                                  + json.dumps(selector) + ")"))
        return bool(self.call("window.lisichka.submit(null)"))

    def fill_form(self, selector: str,
                  data: dict[str, str]) -> dict[str, bool]:
        """Заполнить поля формы значениями по их name.

        Args:
            selector: CSS-селектор формы.
            data: {name_поля: значение}.

        Returns:
            {name_поля: успешно ли заполнено}.
        """
        result: dict[str, bool] = {}
        for name, value in data.items():
            field = f"{selector} [name={json.dumps(name)}]"
            result[name] = self.set_value(field, value)
        return result

    # ── Прокрутка ─────────────────────────────────────────────────────────

    def scroll_to(self, y: int = 0) -> int:
        """Прокрутить страницу на y пикселей (0 — вверх)."""
        return int(self.call("window.lisichka.scrollTo(" + str(y) + ")"))

    def scroll_to_bottom(self) -> int:
        """Прокрутить страницу вниз. Возвращает высоту документа."""
        return int(self.call("window.lisichka.scrollToBottom()"))

    def get_scroll_info(self) -> dict:
        """Информация о прокрутке: scrollY, scrollHeight, innerHeight."""
        return self.call("window.lisichka.getScrollInfo()")

    # ── Проверки и ожидание ───────────────────────────────────────────────

    def exists(self, selector: str) -> bool:
        """Есть ли элемент на странице."""
        return bool(self.call("window.lisichka.exists("
                              + json.dumps(selector) + ")"))

    def wait_for_selector(self, selector: str,
                          timeout: float = 10.0) -> bool:
        """Ждать появления элемента (для SPA и динамики).

        Args:
            selector: CSS-селектор.
            timeout: максимум секунд ожидания.

        Returns:
            True если элемент найден в течение таймаута.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.exists(selector):
                    return True
            except JsError:
                pass
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.processEvents()
            time.sleep(0.15)
        return False