/*
 * dom_api.js — DOM API для браузера «Лисичка».
 *
 * Создаёт глобальный объект window.lisichka с набором синхронных функций
 * для извлечения данных и управления DOM со стороны Python.
 *
 * При наличии QWebChannel (qt.webChannelTransport) дополнительно
 * инициализирует канал и оповещает Python (lisichka_bridge.notify_loaded).
 *
 * Инжектится через QWebEngineScript на DocumentReady (MainWorld).
 */
(function () {
    "use strict";

    if (window.lisichka) {
        return; // уже установлен
    }

    // ── Вспомогательные функции ──────────────────────────────────────────

    function qs(selector) {
        if (!selector) {
            return null;
        }
        try {
            return document.querySelector(selector);
        } catch (e) {
            return null;
        }
    }

    function nodeText(el) {
        if (!el) {
            return "";
        }
        return (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
    }

    function makeJson(expr) {
        // Обёртка: возвращает JSON-строку или маркер ошибки
        try {
            return JSON.stringify(expr());
        } catch (e) {
            return "LISICHKA_ERR:" + (e && e.message ? e.message : String(e));
        }
    }

    // ── Основной API ──────────────────────────────────────────────────────

    var lisichka = {
        // Сведения о странице
        getTitle: function () {
            return makeJson(function () { return document.title || ""; });
        },

        getUrl: function () {
            return makeJson(function () { return location.href || ""; });
        },

        getReadyState: function () {
            return makeJson(function () { return document.readyState || ""; });
        },

        getMeta: function (name) {
            return makeJson(function () {
                var el = document.querySelector('meta[name="' + name + '"]') ||
                         document.querySelector('meta[property="' + name + '"]');
                return el ? (el.getAttribute("content") || "") : "";
            });
        },

        // Текст / HTML
        getText: function (selector) {
            return makeJson(function () {
                return selector ? nodeText(qs(selector)) : nodeText(document.body);
            });
        },

        getHtml: function (selector) {
            return makeJson(function () {
                var el = selector ? qs(selector) : document.documentElement;
                return el ? el.outerHTML || "" : "";
            });
        },

        getAttribute: function (selector, name) {
            return makeJson(function () {
                var el = qs(selector);
                return el ? (el.getAttribute(name) || "") : "";
            });
        },

        // Ссылки
        getLinks: function (maxLinks) {
            return makeJson(function () {
                var out = [];
                var limit = (typeof maxLinks === "number" && maxLinks > 0) ? maxLinks : 1000;
                var links = document.links || [];
                for (var i = 0; i < links.length && i < limit; i++) {
                    var a = links[i];
                    out.push({
                        href: a.href || "",
                        text: nodeText(a),
                        title: a.title || ""
                    });
                }
                return out;
            });
        },

        // Картинки
        getImages: function (maxImages) {
            return makeJson(function () {
                var out = [];
                var limit = (typeof maxImages === "number" && maxImages > 0) ? maxImages : 500;
                var imgs = document.getElementsByTagName("img");
                for (var i = 0; i < imgs.length && i < limit; i++) {
                    out.push({
                        src: imgs[i].currentSrc || imgs[i].src || "",
                        alt: imgs[i].alt || ""
                    });
                }
                return out;
            });
        },

        // Формы и поля
        getInputs: function () {
            return makeJson(function () {
                var out = [];
                var els = document.querySelectorAll(
                    "input, textarea, select, button");
                for (var i = 0; i < els.length; i++) {
                    var el = els[i];
                    var tag = el.tagName.toLowerCase();
                    out.push({
                        tag: tag,
                        type: (tag === "input" ? (el.type || "text") : ""),
                        name: el.name || "",
                        id: el.id || "",
                        placeholder: el.placeholder || "",
                        value: el.value !== undefined ? el.value : ""
                    });
                }
                return out;
            });
        },
getForms: function () {
            return makeJson(function () {
                var out = [];
                var forms = document.forms || [];
                for (var i = 0; i < forms.length; i++) {
                    var form = forms[i];
                    var fields = [];
                    var els = form.querySelectorAll("input, textarea, select");
                    for (var j = 0; j < els.length; j++) {
                        var el = els[j];
                        var tag = el.tagName.toLowerCase();
                        fields.push({
                            tag: tag,
                            type: (tag === "input" ? (el.type || "text") : ""),
                            name: el.name || "",
                            value: el.value !== undefined ? el.value : ""
                        });
                    }
                    out.push({
                        action: form.action || "",
                        method: (form.method || "get").toUpperCase(),
                        fields: fields
                    });
                }
                return out;
            });
        },

        // Действия
        click: function (selector) {
            return makeJson(function () {
                var el = qs(selector);
                if (!el) {
                    return false;
                }
                el.click();
                return true;
            });
        },

        setValue: function (selector, value) {
            return makeJson(function () {
                var el = qs(selector);
                if (!el) {
                    return false;
                }
                var proto = Object.getPrototypeOf(el);
                var desc = proto && Object.getOwnPropertyDescriptor(proto, "value");
                var setter = desc && desc.set;
                if (setter) {
                    // React и прочие фреймворки отследят изменение
                    setter.call(el, String(value));
                } else {
                    el.value = String(value);
                }
                el.dispatchEvent(new Event("input", { bubbles: true }));
                el.dispatchEvent(new Event("change", { bubbles: true }));
                return true;
            });
        },

        submit: function (selector) {
            return makeJson(function () {
                var el = selector ? qs(selector) : document.querySelector("form");
                if (!el) {
                    return false;
                }
                if (el.tagName && el.tagName.toLowerCase() === "form") {
                    el.submit();
                } else {
                    var form = el.closest("form");
                    if (form) {
                        form.submit();
                    } else {
                        return false;
                    }
                }
                return true;
            });
        },

        // Прокрутка
        scrollTo: function (y) {
            return makeJson(function () {
                window.scrollTo(0, typeof y === "number" ? y : 0);
                return window.scrollY || document.documentElement.scrollTop || 0;
            });
        },

        scrollToBottom: function () {
            return makeJson(function () {
                var h = document.documentElement.scrollHeight || 0;
                window.scrollTo(0, h);
                return h;
            });
        },

        getScrollInfo: function () {
            return makeJson(function () {
                var de = document.documentElement;
                return {
                    scrollY: window.scrollY || de.scrollTop || 0,
                    scrollHeight: de.scrollHeight || 0,
                    innerHeight: window.innerHeight || 0,
                    readyState: document.readyState || ""
                };
            });
        },

        // Поиск селектора на странице
        exists: function (selector) {
            return makeJson(function () {
                return !!qs(selector);
            });
        }
    };

    window.lisichka = lisichka;

    // ── Подключение к Python через QWebChannel ───────────────────────────

    function initBridge() {
        if (!window.qt || !window.qt.webChannelTransport ||
            !window.QWebChannel) {
            return; // канал недоступен — работаем только через runJavaScript
        }
        try {
            new QWebChannel(window.qt.webChannelTransport, function (channel) {
                window.lisichka_bridge = channel.objects.lisichka;
                var b = window.lisichka_bridge;
                if (b && b.notify_loaded) {
                    b.notify_loaded(location.href || "");
                }
            });
        } catch (e) {
            // не критично: мост опционален
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initBridge);
    } else {
        initBridge();
    }
})();