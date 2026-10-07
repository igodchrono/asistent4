# -*- coding: utf-8 -*-
"""Вкладка «Персонаж»: выбор + правка карточки. Показывается только если есть персонажи на диске."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5 import QtWidgets, QtCore
import config


class PersonaTabMixin:
    def _setup_persona_tab(self, tab: QtWidgets.QWidget) -> None:
        layout = QtWidgets.QVBoxLayout(tab)

        layout.addWidget(QtWidgets.QLabel(
            "Персонажи — папки <code>personas/characters/&lt;id&gt;/</code>. "
            "Карточка — файл <code>&lt;id&gt;.md</code> (или первый .md в папке)."
        ))

        row = QtWidgets.QHBoxLayout()
        self.character_combo = QtWidgets.QComboBox()
        row.addWidget(self.character_combo, 1)
        btn_refresh = QtWidgets.QPushButton("Обновить список")
        btn_refresh.clicked.connect(self.reload_characters_list)
        row.addWidget(btn_refresh)
        layout.addLayout(row)

        self.character_path_lab = QtWidgets.QLabel("")
        self.character_path_lab.setStyleSheet("color: #aaa;")
        self.character_path_lab.setWordWrap(True)
        layout.addWidget(self.character_path_lab)

        layout.addWidget(QtWidgets.QLabel("Карточка персонажа (можно править и сохранить):"))
        self.character_card_edit = QtWidgets.QPlainTextEdit()
        self.character_card_edit.setPlaceholderText("Текст карточки .md …")
        layout.addWidget(self.character_card_edit, 1)

        row2 = QtWidgets.QHBoxLayout()
        self.btn_reload_card = QtWidgets.QPushButton("Сбросить правки")
        self.btn_reload_card.clicked.connect(self._reload_current_card)
        self.btn_save_card = QtWidgets.QPushButton("Сохранить карточку на диск")
        self.btn_save_card.clicked.connect(self._save_current_card)
        row2.addWidget(self.btn_reload_card)
        row2.addStretch(1)
        row2.addWidget(self.btn_save_card)
        layout.addLayout(row2)

        self.character_status = QtWidgets.QLabel("")
        self.character_status.setStyleSheet("color: #aaa;")
        layout.addWidget(self.character_status)

        self.character_combo.currentIndexChanged.connect(self._on_character_selected)
        self._card_path: Optional[Path] = None
        self._loading_card = False

    def reload_characters_list(self) -> None:
        from character_catalog import list_characters_meta
        metas = list_characters_meta()
        want = getattr(config, "ACTIVE_CHARACTER", None)
        self.character_combo.blockSignals(True)
        self.character_combo.clear()
        for m in metas:
            label = f"{m.get('title') or m['id']}  ({m['id']})"
            self.character_combo.addItem(label, m["id"])
        idx = self.character_combo.findData(want)
        if idx < 0 and self.character_combo.count():
            idx = 0
        if idx >= 0:
            self.character_combo.setCurrentIndex(idx)
        self.character_combo.blockSignals(False)
        self._on_character_selected()

    def _on_character_selected(self, *_args) -> None:
        cid = self.character_combo.currentData()
        if not cid:
            self.character_path_lab.setText("")
            self.character_card_edit.setPlainText("")
            self._card_path = None
            return
        from character_catalog import character_meta, character_card_path, read_character_card
        m = character_meta(str(cid))
        self.character_path_lab.setText(
            f"Папка: {m.get('path')}\n"
            f"avatar/: {'да' if m.get('has_avatar') else 'нет'}"
        )
        path = character_card_path(str(cid))
        self._card_path = path
        self._loading_card = True
        self.character_card_edit.setPlainText(read_character_card(str(cid), max_chars=200000))
        self._loading_card = False
        self.character_status.setText(f"Файл: {path}" if path else "Карточки нет — при сохранении будет создана")

    def _reload_current_card(self) -> None:
        self._on_character_selected()
        self.character_status.setText("Карточка перечитана с диска")

    def _save_current_card(self) -> None:
        cid = self.character_combo.currentData()
        if not cid:
            return
        from character_catalog import character_dir, character_card_path
        d = character_dir(str(cid))
        d.mkdir(parents=True, exist_ok=True)
        path = character_card_path(str(cid)) or (d / f"{cid}.md")
        try:
            path.write_text(self.character_card_edit.toPlainText(), encoding="utf-8")
            self._card_path = path
            self.character_status.setStyleSheet("color: #0f0;")
            self.character_status.setText(f"Сохранено: {path}")
        except Exception as e:
            self.character_status.setStyleSheet("color: #f66;")
            self.character_status.setText(f"Ошибка записи: {e}")

    def load_persona_settings(self) -> None:
        if hasattr(self, "character_combo"):
            self.reload_characters_list()

    def collect_persona_settings(self) -> dict:
        if not hasattr(self, "character_combo"):
            return {}
        cid = self.character_combo.currentData()
        if not cid:
            return {}
        # автосохранение карточки при «Сохранить» в диалоге
        try:
            self._save_current_card()
        except Exception:
            pass
        return {"ACTIVE_CHARACTER": str(cid)}
