# -*- coding: utf-8 -*-
"""Главное окно: чат с картинками (превью / полный экран) и вложениями."""
from __future__ import annotations

import html
import re
import shutil
from datetime import datetime
from pathlib import Path

from PyQt5 import QtWidgets, QtCore, QtGui
from qasync import asyncSlot
import config
from settings_dialog import SettingsDialog

try:
    from ui.theme import WINDOW_QSS
except Exception:
    WINDOW_QSS = """
    QMainWindow { background-color: #1e1e1e; }
    QTextBrowser { background-color: #2d2d2d; color: #f0f0f0; border: 1px solid #444; border-radius: 6px; }
    QLineEdit { background-color: #2d2d2d; color: #f0f0f0; border: 1px solid #444; padding: 8px; border-radius: 6px; }
    QPushButton { background-color: #3a3a3a; color: #f0f0f0; border: 1px solid #555; padding: 6px 10px; border-radius: 5px; }
    QPushButton:hover { background-color: #4a4a4a; }
    QPushButton:disabled { background-color: #2a2a2a; color: #777; }
    QLabel { color: #ccc; }
    """

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_TEXT_EXT = {".txt", ".md", ".json", ".csv", ".log", ".py", ".ini", ".yaml", ".yml"}
_ANIM_RE = re.compile(r"\[ANIM:[a-zA-Z0-9_]+\]", re.I)
_FENCE_RE = re.compile(r"```([a-zA-Z0-9_+-]*)\r?\n?(.*?)```", re.S)
_INLINE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_FILE_MARK_RE = re.compile(r"\[(?:фото|файл):\s*([^\]]+)\]", re.I)


def _keep_lines(text: str) -> str:
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in t.split("\n")]
    out: list[str] = []
    blanks = 0
    for ln in lines:
        if not ln:
            blanks += 1
            if blanks <= 2:
                out.append("")
        else:
            blanks = 0
            out.append(ln)
    return "\n".join(out).strip("\n")


def _strip_anim(text: str) -> str:
    return _keep_lines(_ANIM_RE.sub("", text or ""))


def _html_line(line: str) -> str:
    i = 0
    while i < len(line) and line[i] in " \t":
        i += 1
    lead = line[:i].replace("\t", "    ").replace(" ", "&nbsp;")
    rest = html.escape(line[i:])

    def _code(m: re.Match) -> str:
        return (
            "<code style='background:#3a3a3a;padding:1px 4px;border-radius:3px;"
            f"font-family:Consolas,monospace;'>{m.group(1)}</code>"
        )

    rest = _INLINE_RE.sub(_code, rest)
    rest = _BOLD_RE.sub(r"<b>\1</b>", rest)
    rest = _ITALIC_RE.sub(r"<i>\1</i>", rest)
    return lead + rest


def _html_prose(text: str) -> str:
    if not text:
        return ""
    chunks: list[str] = []
    for ln in text.split("\n"):
        s = ln.strip()
        if s.startswith("### "):
            chunks.append(f"<div style='font-weight:bold;margin:6px 0 2px;'>{_html_line(s[4:])}</div>")
        elif s.startswith("## "):
            chunks.append(f"<div style='font-weight:bold;font-size:14px;margin:8px 0 2px;'>{_html_line(s[3:])}</div>")
        elif s.startswith("# "):
            chunks.append(f"<div style='font-weight:bold;font-size:15px;margin:8px 0 2px;'>{_html_line(s[2:])}</div>")
        elif re.match(r"^[-•]\s+", s):
            body = re.sub(r"^[-•]\s+", "", s)
            chunks.append(f"<div style='margin-left:12px;'>• {_html_line(body)}</div>")
        elif re.match(r"^\d+[.)]\s+", s):
            chunks.append(f"<div style='margin-left:12px;'>{_html_line(s)}</div>")
        else:
            chunks.append(_html_line(ln) + "<br>")
    return "".join(chunks)


def _html_code(code: str) -> str:
    body = html.escape((code or "").replace("\t", "    ").rstrip("\n"))
    body = body.replace(" ", "&nbsp;").replace("\n", "<br>")
    return (
        '<pre style="background:#1a1a1a;color:#e8e8e8;padding:8px 10px;margin:8px 0;'
        "border:1px solid #444;border-radius:6px;"
        'font-family:Consolas,\'Cascadia Code\',monospace;font-size:12.5px;">'
        f"{body}</pre>"
    )


def _format_chat_html(text: str) -> str:
    t = text or ""
    chunks: list[str] = []
    pos = 0
    for m in _FENCE_RE.finditer(t):
        if m.start() > pos:
            chunks.append(_html_prose(t[pos:m.start()].strip("\n")))
        chunks.append(_html_code(m.group(2) or ""))
        pos = m.end()
    if pos == 0:
        return _html_prose(t)
    if pos < len(t):
        chunks.append(_html_prose(t[pos:].strip("\n")))
    return "".join(c for c in chunks if c)


class _GuiBridge(QtCore.QObject):
    """Вызов с любого потока в GUI (сигнал, не QTimer из python-thread)."""
    _run = QtCore.pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._run.connect(self._exec, QtCore.Qt.QueuedConnection)

    @QtCore.pyqtSlot(object)
    def _exec(self, fn) -> None:
        try:
            fn()
        except Exception as e:
            print(f"gui bridge: {e}", flush=True)

    def post(self, fn) -> None:
        self._run.emit(fn)


def attachments_root() -> Path:
    root = Path(__file__).resolve().parent / "attachments"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_EXT


class FullscreenImage(QtWidgets.QDialog):
    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(path.name)
        self.setWindowState(QtCore.Qt.WindowMaximized)
        self.setStyleSheet("background:#111;")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self._path = path
        self._label = QtWidgets.QLabel()
        self._label.setAlignment(QtCore.Qt.AlignCenter)
        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._label)
        btns = QtWidgets.QHBoxLayout()
        plus = QtWidgets.QPushButton("＋ крупнее")
        minus = QtWidgets.QPushButton("－ мельче")
        fit = QtWidgets.QPushButton("по экрану")
        close = QtWidgets.QPushButton("закрыть")
        plus.clicked.connect(lambda: self._zoom(1.25))
        minus.clicked.connect(lambda: self._zoom(0.8))
        fit.clicked.connect(self._fit)
        close.clicked.connect(self.accept)
        for b in (plus, minus, fit, close):
            btns.addWidget(b)
        lay.addLayout(btns)
        lay.addWidget(self._scroll, 1)
        self._pm = QtGui.QPixmap(str(path))
        self._scale = 1.0
        self._fit()

    def _apply(self):
        if self._pm.isNull():
            self._label.setText("не удалось открыть изображение")
            return
        w = max(40, int(self._pm.width() * self._scale))
        h = max(40, int(self._pm.height() * self._scale))
        scaled = self._pm.scaled(w, h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        self._label.setPixmap(scaled)
        self._label.resize(scaled.size())

    def _fit(self):
        if self._pm.isNull():
            return
        avail = self._scroll.viewport().size()
        if avail.width() < 50 or avail.height() < 50:
            avail = QtCore.QSize(1200, 800)
        sx = avail.width() / max(1, self._pm.width())
        sy = avail.height() / max(1, self._pm.height())
        self._scale = min(sx, sy, 1.0)
        self._apply()

    def _zoom(self, factor: float):
        self._scale = max(0.1, min(6.0, self._scale * factor))
        self._apply()

    def mouseDoubleClickEvent(self, ev):
        self.accept()


class ChatWindow(QtWidgets.QMainWindow):
    def __init__(self, engine, loader):
        super().__init__()
        self.engine = engine
        self.loader = loader
        self._busy = False
        self._pending: list[Path] = []
        self._bridge = _GuiBridge(self)
        self._pub_queue: list[str] = []
        self.setWindowTitle(getattr(config, "WINDOW_TITLE", "Лисичка — ядро"))
        self.resize(int(getattr(config, "WINDOW_WIDTH", 920)), int(getattr(config, "WINDOW_HEIGHT", 720)))
        self.setStyleSheet(WINDOW_QSS)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        top = QtWidgets.QHBoxLayout()
        self.title_lab = QtWidgets.QLabel("🦊 Лисичка")
        self.title_lab.setStyleSheet("color: #f0c27a; font-size: 16px; font-weight: bold;")
        top.addWidget(self.title_lab)
        self.mode_btn = QtWidgets.QPushButton()
        self.mode_btn.setObjectName("modeBtn")
        self.mode_btn.setToolTip("Компаньон 18+ ↔ работа. Или скажи «давай по делу» / «режим лисы».")
        self.mode_btn.clicked.connect(self._toggle_mode)
        top.addWidget(self.mode_btn)
        top.addStretch(1)
        self.status_dot = QtWidgets.QLabel("●")
        self.status_dot.setStyleSheet("color: #0f0; font-size: 14px;")
        self.status_text = QtWidgets.QLabel("готово")
        self.status_text.setStyleSheet("color: #aaa;")
        top.addWidget(self.status_dot)
        top.addWidget(self.status_text)
        layout.addLayout(top)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        days_wrap = QtWidgets.QWidget()
        days_lay = QtWidgets.QVBoxLayout(days_wrap)
        days_lay.setContentsMargins(0, 0, 0, 0)
        days_lay.setSpacing(4)
        days_lab = QtWidgets.QLabel("Чаты")
        days_lab.setStyleSheet("color:#888; font-size:11px;")
        days_lay.addWidget(days_lab)
        chat_btns = QtWidgets.QHBoxLayout()
        self.new_chat_btn = QtWidgets.QPushButton("＋ чат")
        self.new_chat_btn.setToolTip("Новый чат. Факты и дневник не стираются.")
        self.new_chat_btn.clicked.connect(self._new_chat)
        self.del_chat_btn = QtWidgets.QPushButton("удалить")
        self.del_chat_btn.setToolTip("Удалить этот чат. Память персонажа остаётся.")
        self.del_chat_btn.clicked.connect(self._delete_chat)
        chat_btns.addWidget(self.new_chat_btn)
        chat_btns.addWidget(self.del_chat_btn)
        days_lay.addLayout(chat_btns)
        self.days_list = QtWidgets.QListWidget()
        self.days_list.setMaximumWidth(168)
        self.days_list.itemClicked.connect(self._on_chat_clicked)
        days_lay.addWidget(self.days_list, 1)
        split.addWidget(days_wrap)

        self.chat = QtWidgets.QTextBrowser()
        self.chat.setReadOnly(True)
        self.chat.setOpenExternalLinks(False)
        self.chat.setOpenLinks(False)
        self.chat.anchorClicked.connect(self._on_anchor)
        self.chat.setFont(QtGui.QFont("Segoe UI", 11))
        split.addWidget(self.chat)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([150, 770])
        layout.addWidget(split, 1)

        self.attach_bar = QtWidgets.QLabel("")
        self.attach_bar.setStyleSheet("color:#9ad; font-size:12px;")
        self.attach_bar.setWordWrap(True)
        layout.addWidget(self.attach_bar)

        row = QtWidgets.QHBoxLayout()
        self.input = QtWidgets.QLineEdit()
        self.input.setPlaceholderText("Сообщение… Enter — отправить")
        self.input.returnPressed.connect(self._on_send)
        self.attach_btn = QtWidgets.QPushButton("📎 Файл")
        self.attach_btn.setToolTip("Прикрепить картинку или файл для анализа")
        self.attach_btn.clicked.connect(self._attach)
        self.send_btn = QtWidgets.QPushButton("Отправить")
        self.send_btn.clicked.connect(self._on_send)
        self.voice_btn = QtWidgets.QPushButton("🎙 Голос")
        self.voice_btn.clicked.connect(self._toggle_voice)
        self.stop_speech_btn = QtWidgets.QPushButton("⏹ Стоп речь")
        self.stop_speech_btn.setToolTip("Остановить озвучивание ответа")
        self.stop_speech_btn.clicked.connect(self._stop_speech)
        self.settings_btn = QtWidgets.QPushButton("⚙ Настройки")
        self.settings_btn.clicked.connect(self._open_settings)
        row.addWidget(self.attach_btn)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        row.addWidget(self.voice_btn)
        row.addWidget(self.stop_speech_btn)
        row.addWidget(self.settings_btn)
        layout.addLayout(row)

        plugs = ", ".join(engine.app.plugins.keys()) or "нет"
        self.footer = QtWidgets.QLabel(f"Плагины: {plugs}  |  {getattr(config, 'API_URL', '')}")
        self.footer.setStyleSheet("color: #777; font-size: 11px;")
        layout.addWidget(self.footer)

        engine.app.state["gui"] = self
        engine.app.gui = self
        engine.app.window = self
        self.refresh_mode_chrome()
        self._refresh_days()
        self._append_sys("Ядро запущено. Подключение: " + str(getattr(config, "API_URL", "")))
        self._append_sys("Режим: кнопка сверху или «давай по делу» / «режим лисы». Картинка — клик на весь экран.")

    def show_dialog_resume(self, rows, note: str = "") -> None:
        """Показать хвост сохранённого диалога этого персонажа."""
        if note:
            self._append_sys(note)
        items = list(rows or [])
        if not items:
            return
        shown = items[-16:]
        if len(items) > len(shown):
            self._append_sys(f"…ещё {len(items) - len(shown)} реплик в памяти, в контекст уйдёт хвост + дневник")
        for m in shown:
            role = str((m.get("role") if isinstance(m, dict) else "") or "")
            text = str((m.get("content") if isinstance(m, dict) else m) or "")
            if not text.strip():
                continue
            who = "Вы" if role == "user" else "Ассистент"
            self._append(who, text[:2000])

    def attachments_dir(self) -> Path:
        day = attachments_root() / datetime.now().strftime("%Y-%m-%d")
        day.mkdir(parents=True, exist_ok=True)
        return day

    def _refresh_attach_bar(self) -> None:
        if not self._pending:
            self.attach_bar.setText("")
            return
        names = ", ".join(p.name for p in self._pending)
        self.attach_bar.setText(f"Вложения ({len(self._pending)}): {names}")

    def _attach(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Файл для чата / анализа",
            "",
            "Все (*);;Текст (*.txt *.md *.json *.csv *.py *.log *.docx *.pdf);;Картинки (*.png *.jpg *.jpeg *.gif *.webp *.bmp)",
        )
        if not paths:
            return
        dest_dir = self.attachments_dir()
        for raw in paths:
            src = Path(raw)
            if not src.is_file():
                continue
            dest = dest_dir / src.name
            i = 1
            while dest.exists():
                dest = dest_dir / f"{src.stem}_{i}{src.suffix}"
                i += 1
            shutil.copy2(src, dest)
            self._pending.append(dest)
        self._refresh_attach_bar()
        self._append_sys(f"Скопировано в {dest_dir}: {len(paths)} файл(ов)")

    def _on_anchor(self, url: QtCore.QUrl) -> None:
        if url.scheme() == "asistent":
            q = QtCore.QUrlQuery(url)
            raw = q.queryItemValue("p")
            path = Path(raw) if raw else Path()
            if url.host() == "save" and path.is_file():
                dest, _ = QtWidgets.QFileDialog.getSaveFileName(
                    self, "Сохранить файл", path.name, "Все (*)"
                )
                if dest:
                    try:
                        shutil.copy2(path, dest)
                        self._append_sys(f"Сохранено: {dest}")
                    except Exception as e:
                        self._append_sys(f"Не сохранилось: {e}")
                return
        path = Path(url.toLocalFile() or "")
        if not path.exists():
            s = url.toString()
            if s.startswith("file:"):
                path = Path(url.toLocalFile())
        if path.exists() and _is_image(path):
            FullscreenImage(path, self).exec_()
        elif path.exists():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def _html_text(self, text: str) -> str:
        return _format_chat_html(text or "")

    def _html_file(self, path: Path) -> str:
        path = Path(path)
        if not path.exists():
            return f'<div style="margin:6px 0;color:#888;">файл не найден: {html.escape(path.name)}</div>'
        uri = path.resolve().as_uri()
        name = html.escape(path.name)
        size = path.stat().st_size
        if size >= 1048576:
            sz = f"{size/1048576:.1f} МБ"
        elif size >= 1024:
            sz = f"{size/1024:.1f} КБ"
        else:
            sz = f"{size} байт"
        q = QtCore.QUrl("asistent://save")
        qq = QtCore.QUrlQuery()
        qq.addQueryItem("p", str(path.resolve()))
        q.setQuery(qq)
        save = html.escape(q.toString())
        if _is_image(path):
            return (
                f'<div style="margin:6px 0;">'
                f'<a href="{uri}" title="открыть на весь экран">'
                f'<img src="{uri}" width="220" style="border-radius:8px; border:1px solid #555;" />'
                f"</a><br><span style='color:#888;font-size:11px;'>🖼 {name} · {sz} · "
                f'<a href="{save}" style="color:#9cf;">сохранить как</a></span></div>'
            )
        return (
            f'<div style="margin:6px 0;padding:8px 10px;border:1px solid #555;border-radius:8px;'
            f'background:#333;display:inline-block;">'
            f'<a href="{uri}" style="color:#9cf;font-weight:bold;">📄 {name}</a>'
            f'<span style="color:#888;font-size:11px;"> · {sz} · </span>'
            f'<a href="{save}" style="color:#9cf;font-size:11px;">сохранить как</a></div>'
        )

    def _pull_file_marks(self, text: str) -> tuple[str, str]:
        extra = ""
        raw = text or ""
        for m in _FILE_MARK_RE.finditer(raw):
            fp = Path(str(m.group(1)).strip().strip('"'))
            if fp.exists():
                extra += self._html_file(fp)
        raw = _FILE_MARK_RE.sub("", raw)
        return raw, extra

    def _append_html(self, block: str) -> None:
        self.chat.moveCursor(QtGui.QTextCursor.End)
        self.chat.insertHtml(block + "<br>")
        self.chat.moveCursor(QtGui.QTextCursor.End)

    def _append_sys(self, text: str) -> None:
        self._append_html(
            '<div align="center" style="margin:6px 0;">'
            f'<span style="color:#888;font-size:11px;">• {self._html_text(text)}</span></div>'
        )

    def _bubble(self, who: str, body: str, extra: str = "") -> str:
        is_user = who == "Вы"
        is_err = who == "Ошибка"
        align = "right" if is_user else "left"
        if is_err:
            bg, name_c = "#4a2222", "#f66"
        elif is_user:
            bg, name_c = "#3d3428", "#f0c27a"
        else:
            bg, name_c = "#24332a", "#9ad7a0"
        return (
            f'<div align="{align}" style="margin:8px 4px;">'
            f'<table cellpadding="9" cellspacing="0" bgcolor="{bg}" '
            f'style="max-width:78%;border-radius:14px;">'
            f'<tr><td>'
            f'<div style="color:{name_c};font-size:11px;margin-bottom:3px;">{html.escape(who)}</div>'
            f"{body}{extra}</td></tr></table></div>"
        )

    def _append(self, who: str, text: str, files=None) -> None:
        text = _strip_anim(text) if who != "Вы" else (text or "")
        extra = ""
        for p in files or []:
            extra += self._html_file(Path(p))
        text, marks = self._pull_file_marks(text)
        extra += marks
        body = self._html_text(text)
        self._append_html(self._bubble(who, body, extra))

    def refresh_chrome(self) -> None:
        plugs = ", ".join(self.engine.app.plugins.keys()) or "нет"
        self.footer.setText(f"Плагины: {plugs}  |  {getattr(config, 'API_URL', '')}")
        self.refresh_mode_chrome()
        self._refresh_days()

    def refresh_mode_chrome(self) -> None:
        from core.mode import get_mode, label, WORK
        m = get_mode(self.engine.app)
        self.mode_btn.setText(label(m))
        self.mode_btn.setProperty("work", "true" if m == WORK else "false")
        self.mode_btn.style().unpolish(self.mode_btn)
        self.mode_btn.style().polish(self.mode_btn)
        cid = ""
        if hasattr(self.engine.app, "get_active_character"):
            cid = self.engine.app.get_active_character()
        else:
            cid = str(getattr(config, "ACTIVE_CHARACTER", "") or "")
        self.title_lab.setText(f"🦊 {cid or 'Лисичка'}")

    def _toggle_mode(self) -> None:
        from core.mode import get_mode, set_mode, WORK, COMPANION, label
        cur = get_mode(self.engine.app)
        nxt = WORK if cur != WORK else COMPANION
        set_mode(self.engine.app, nxt)
        self.refresh_mode_chrome()
        self._append_sys(f"Режим: {label(nxt)}")

    def _chat_store(self):
        mem = None
        try:
            mem = self.engine.app.plugins.get("memory")
        except Exception:
            mem = None
        return getattr(mem, "store", None) if mem else None

    def _refresh_days(self) -> None:
        self.days_list.clear()
        store = self._chat_store()
        if store is None or not hasattr(store, "list_chats"):
            return
        try:
            current = int(store.ensure_current_chat())
            self.engine.app.state["chat_id"] = current
            rows = store.list_chats() or []
        except Exception as e:
            print(f"chats list: {e}", flush=True)
            return
        for row in rows:
            cid = int(row.get("id") or 0)
            title = str(row.get("title") or "чат")
            n = int(row.get("n") or 0)
            it = QtWidgets.QListWidgetItem(f"{title[:18]}  ({n})")
            it.setData(QtCore.Qt.UserRole, cid)
            self.days_list.addItem(it)
            if cid == current:
                self.days_list.setCurrentItem(it)

    def _on_chat_clicked(self, item) -> None:
        if item is None:
            return
        cid = item.data(QtCore.Qt.UserRole)
        if cid is None:
            return
        self._open_chat(int(cid))

    def _open_chat(self, chat_id: int, note: str = "") -> None:
        store = self._chat_store()
        if store is None:
            return
        try:
            store.set_meta("current_chat_id", str(int(chat_id)))
            self.engine.app.state["chat_id"] = int(chat_id)
            rows = store.messages_for_chat(int(chat_id), limit=80) or []
        except Exception as e:
            self._append_sys(f"Не открылся чат: {e}")
            return
        tail = rows[-40:]
        self.engine.history = [
            {"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")}
            for m in tail
        ]
        self.chat.clear()
        self.show_dialog_resume(rows, note or "Чат")
        self._refresh_days()

    def _new_chat(self) -> None:
        store = self._chat_store()
        if store is None or not hasattr(store, "create_chat"):
            self._append_sys("Память не загружена — чат не создать.")
            return
        try:
            cid = store.create_chat("Новый чат")
        except Exception as e:
            self._append_sys(f"Не создался чат: {e}")
            return
        self.engine.app.state["chat_id"] = int(cid)
        self.engine.history = []
        self.chat.clear()
        self._append_sys("Новый чат. Факты и дневник на месте.")
        self._refresh_days()

    def _delete_chat(self) -> None:
        store = self._chat_store()
        if store is None or not hasattr(store, "delete_chat"):
            return
        cid = self.engine.app.state.get("chat_id")
        item = self.days_list.currentItem()
        if item is not None and item.data(QtCore.Qt.UserRole) is not None:
            cid = item.data(QtCore.Qt.UserRole)
        if cid is None:
            return
        title = item.text() if item is not None else "этот чат"
        ans = QtWidgets.QMessageBox.question(
            self,
            "Удалить чат",
            f"Удалить «{title}»?\nСообщения этого чата пропадут. Факты и дневник останутся.",
        )
        if ans != QtWidgets.QMessageBox.Yes:
            return
        try:
            store.delete_chat(int(cid))
            nxt = store.ensure_current_chat()
        except Exception as e:
            self._append_sys(f"Не удалился чат: {e}")
            return
        self._open_chat(int(nxt), "Чат удалён. Память не трогала.")

    def _on_day_clicked(self, item) -> None:
        self._on_chat_clicked(item)

    def post(self, fn) -> None:
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None and QtCore.QThread.currentThread() is app.thread():
                fn()
                return
        except Exception:
            pass
        getattr(self, "_bridge").post(fn)

    @QtCore.pyqtSlot(str)
    def publish_assistant_message(self, text: str) -> None:
        def _go():
            t = (text or "").strip()
            if not t:
                return
            if self._busy:
                self._pub_queue.append(t)
                return
            self._append("Ассистент", t)
            try:
                eng = self.engine
                if eng is not None:
                    if hasattr(eng, "history"):
                        eng.history.append({"role": "assistant", "content": t})
                        if hasattr(eng, "_trim_history"):
                            eng._trim_history()
                    if hasattr(eng, "_remember"):
                        eng._remember("assistant", t)
            except Exception:
                pass
        self.post(_go)

    @QtCore.pyqtSlot(str)
    def submit_text(self, text: str) -> None:
        def _go():
            t = (text or "").strip()
            if t and not self._busy:
                self.input.setText(t)
                self._on_send()
        self.post(_go)

    def _begin_assistant_stream(self) -> None:
        self._stream_raw = ""
        self.chat.moveCursor(QtGui.QTextCursor.End)
        self._stream_pos = self.chat.textCursor().position()
        self._append_html(self._bubble("Ассистент", ""))

    def _feed_assistant_stream(self, chunk: str) -> None:
        if not chunk:
            return
        self._stream_raw = getattr(self, "_stream_raw", "") + chunk
        vis = _ANIM_RE.sub("", chunk)
        if not vis:
            return
        self.chat.moveCursor(QtGui.QTextCursor.End)
        self.chat.insertHtml(_html_prose(vis))
        self.chat.moveCursor(QtGui.QTextCursor.End)
        bar = self.chat.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _finish_assistant_stream(self, replace: str | None = None) -> None:
        src = replace if replace else (getattr(self, "_stream_raw", "") or "")
        raw, extra = self._pull_file_marks(_strip_anim(src))
        try:
            cur = self.chat.textCursor()
            cur.setPosition(int(getattr(self, "_stream_pos", 0)))
            cur.movePosition(QtGui.QTextCursor.End, QtGui.QTextCursor.KeepAnchor)
            cur.removeSelectedText()
            self.chat.setTextCursor(cur)
        except Exception:
            pass
        body = _format_chat_html(raw)
        self._append_html(self._bubble("Ассистент", body, extra))

    def _voice_plugin(self):
        return self.engine.app.plugins.get("voice") or self.engine.app.state.get("voice_plugin")

    def _toggle_voice(self) -> None:
        voice = self._voice_plugin()
        if voice is None:
            self._append_sys("Голосовой плагин не загружен.")
            return
        if getattr(voice, "_thread", None) is not None and voice._thread.is_alive():
            voice.stop_listening()
            self.voice_btn.setText("🎙 Голос")
            self._append_sys("Микрофон выключен.")
        else:
            voice.start_listening()
            self.voice_btn.setText("⏹ Стоп микрофон")
            self._append_sys("Микрофон включён.")

    def _stop_speech(self) -> None:
        voice = self._voice_plugin()
        if voice is None:
            self._append_sys("Голосовой плагин не загружен.")
            return
        if hasattr(voice, "stop_speaking"):
            voice.stop_speaking()
            self._append_sys("Озвучивание остановлено.")
        else:
            self._append_sys("В этой версии голоса нет stop_speaking — замените plugins/voice/plugin.py")

    def set_status(self, mode: str, text: str = "") -> None:
        colors = {"idle": "#0f0", "thinking": "#ff0", "error": "#f44", "offline": "#888"}
        self.status_dot.setStyleSheet(f"color: {colors.get(mode, '#0f0')}; font-size: 14px;")
        self.status_text.setText(text or mode)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self)
        if dlg.exec_():
            self.refresh_chrome()
            try:
                from core.llm_client import LLMClient
                from core.mode import set_mode
                self.engine.llm = LLMClient.from_config(config)
                self.engine.app.llm = self.engine.llm
                self.engine.system_prompt = getattr(config, "SYSTEM_PROMPT", self.engine.system_prompt)
                set_mode(self.engine.app, str(getattr(config, "ASSISTANT_MODE", "companion") or "companion"))
                self.refresh_mode_chrome()
            except Exception as e:
                print(f"settings apply: {e}", flush=True)

    def _push_attachments_state(self, files):
        app = self.engine.app
        app.state["pending_attachments"] = [str(p) for p in files]
        app.state["last_attachments"] = [str(p) for p in files]

    @asyncSlot()
    async def _on_send(self) -> None:
        text = self.input.text().strip()
        files = list(self._pending)
        if not text and not files:
            return
        self.input.clear()
        self._pending.clear()
        self._refresh_attach_bar()
        self._append("Вы", text or "(вложение)", files)
        self._push_attachments_state(files)
        send_text = text
        if files:
            listed = ", ".join(Path(p).name for p in files)
            send_text = (text + "\n\n[ВЛОЖЕНИЯ: " + listed + "]").strip()
        self.send_btn.setEnabled(False)
        self._busy = True
        self.set_status("thinking", "думаю…")
        streaming = False
        buf: list[str] = []
        try:
            async for chunk in self.engine.handle_user(send_text):
                if not chunk:
                    continue
                if not streaming:
                    self._begin_assistant_stream()
                    streaming = True
                    self.set_status("thinking", "пишу…")
                buf.append(chunk)
                self._feed_assistant_stream(chunk)
            if streaming:
                self._finish_assistant_stream()
            else:
                self._append("Ассистент", "(пустой ответ)")
            self.set_status("idle", "готово")
        except Exception as e:
            if streaming:
                self._finish_assistant_stream()
            self._append("Ошибка", str(e))
            self.set_status("error", "ошибка")
        finally:
            self._busy = False
            self.send_btn.setEnabled(True)
            self.engine.app.state["pending_attachments"] = []
            q = list(getattr(self, "_pub_queue", []) or [])
            self._pub_queue = []
            for t in q:
                self.publish_assistant_message(t)

    def closeEvent(self, event) -> None:
        try:
            voice = self._voice_plugin()
            if voice is not None and hasattr(voice, "stop_speaking"):
                voice.stop_speaking()
        except Exception:
            pass
        try:
            if self.loader:
                self.loader.shutdown_all()
        except Exception:
            pass
        event.accept()
