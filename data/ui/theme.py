# ui/theme.py — стили главного окна (мессенджер)

WINDOW_QSS = """
    QMainWindow { background-color: #161616; }
    QTextBrowser {
        background-color: #1c1c1c;
        color: #f0f0f0;
        border: 1px solid #333;
        border-radius: 10px;
        padding: 8px;
        selection-background-color: #4a3a28;
    }
    QLineEdit {
        background-color: #2a2a2a;
        color: #f0f0f0;
        border: 1px solid #444;
        padding: 10px 12px;
        border-radius: 10px;
        font-size: 13px;
    }
    QLineEdit:focus { border: 1px solid #c9a227; }
    QPushButton {
        background-color: #333;
        color: #f0f0f0;
        border: 1px solid #555;
        padding: 7px 12px;
        border-radius: 8px;
    }
    QPushButton:hover { background-color: #444; }
    QPushButton:disabled { background-color: #2a2a2a; color: #777; }
    QPushButton#modeBtn {
        background-color: #3a2e1c;
        color: #f0c27a;
        border: 1px solid #c9a227;
        font-weight: bold;
        min-width: 130px;
    }
    QPushButton#modeBtn[work="true"] {
        background-color: #1c2a3a;
        color: #9ad;
        border: 1px solid #4a8;
    }
    QListWidget {
        background-color: #1a1a1a;
        color: #ddd;
        border: 1px solid #333;
        border-radius: 10px;
        padding: 4px;
        outline: none;
    }
    QListWidget::item { padding: 8px 6px; border-radius: 6px; }
    QListWidget::item:selected { background: #3a2e1c; color: #f0c27a; }
    QListWidget::item:hover { background: #2a2a2a; }
    QSplitter::handle { background: #2a2a2a; width: 4px; }
    QLabel { color: #ccc; }
"""

STATUS_STYLES = {
    "idle": ("#0f0", "готово"),
    "listening": ("#0af", "слушаю…"),
    "thinking": ("#ff0", "думаю…"),
    "searching": ("#fa0", "ищу…"),
    "speaking": ("#c6f", "говорю…"),
    "error": ("#f44", "ошибка"),
    "offline": ("#888", "офлайн"),
}
