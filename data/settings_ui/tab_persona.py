# -*- coding: utf-8 -*-
"""Вкладка «Персонаж»: выбор + правка карточки + сброс/бекап/референс."""
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

        # ── Секция управления памятью и файлами ─────────────────────
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(sep)

        mem_label = QtWidgets.QLabel("Управление памятью и файлами персонажа:")
        mem_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(mem_label)

        # Строка референса
        ref_row = QtWidgets.QHBoxLayout()
        self.ref_status = QtWidgets.QLabel("")
        self.ref_status.setStyleSheet("color: #aaa;")
        ref_row.addWidget(self.ref_status, 1)
        btn_load_ref = QtWidgets.QPushButton("📷 Загрузить референс…")
        btn_load_ref.clicked.connect(self._load_ref_dialog)
        ref_row.addWidget(btn_load_ref)
        btn_clear_ref = QtWidgets.QPushButton("❌ Удалить референс")
        btn_clear_ref.clicked.connect(self._clear_ref)
        ref_row.addWidget(btn_clear_ref)
        layout.addLayout(ref_row)

        # Строка сброса / бекапа
        action_row = QtWidgets.QHBoxLayout()
        btn_backup = QtWidgets.QPushButton("💾 Сохранить копию персонажа")
        btn_backup.clicked.connect(self._backup_character)
        btn_reset = QtWidgets.QPushButton("⚠️ Сбросить память персонажа")
        btn_reset.setStyleSheet("QPushButton { color: #f44; }")
        btn_reset.clicked.connect(self._reset_character)
        action_row.addWidget(btn_backup)
        action_row.addStretch(1)
        action_row.addWidget(btn_reset)
        layout.addLayout(action_row)

        # Статус управления
        self.persona_status = QtWidgets.QLabel("")
        self.persona_status.setStyleSheet("color: #aaa;")
        self.persona_status.setWordWrap(True)
        layout.addWidget(self.persona_status)

        # Статус карточки (используется в _save_current_card / _reload)
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
            self._update_ref_status(cid)
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
        self._update_ref_status(cid)

    def _update_ref_status(self, cid: Optional[str] = None) -> None:
        if not cid:
            cid = self.character_combo.currentData()
        if not cid:
            self.ref_status.setText("Референс: нет")
            return
        from character_catalog import has_character_reference
        p = has_character_reference(str(cid))
        if p:
            self.ref_status.setText(f"✅ Референс: {p.name} ({p.parent.name}/)")
        else:
            self.ref_status.setText("❌ Референс не загружен — персонаж не помнит свою внешность")

    def _load_ref_dialog(self) -> None:
        cid = self.character_combo.currentData()
        if not cid:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Сначала выберите персонажа.")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Выберите референсное изображение", "",
            "Изображения (*.png *.jpg *.jpeg *.bmp *.webp);;Все файлы (*.*)"
        )
        if not path:
            return
        from character_catalog import set_character_reference
        result = set_character_reference(str(cid), path)
        self._update_ref_status(cid)
        self.persona_status.setText(result)

    def _clear_ref(self) -> None:
        cid = self.character_combo.currentData()
        if not cid:
            return
        from character_catalog import has_character_reference
        p = has_character_reference(str(cid))
        if not p:
            self.persona_status.setText("Референс не загружен — удалять нечего.")
            return
        try:
            p.unlink()
            self._update_ref_status(cid)
            self.persona_status.setText("✅ Референс удалён.")
        except Exception as e:
            self.persona_status.setText(f"❌ Ошибка удаления: {e}")

    def _backup_character(self) -> None:
        cid = self.character_combo.currentData()
        if not cid:
            return
        from character_catalog import backup_character
        result = backup_character(str(cid))
        self.persona_status.setText(result)

    def _reset_character(self) -> None:
        cid = self.character_combo.currentData()
        if not cid:
            return
        reply = QtWidgets.QMessageBox.question(
            self,
            "Сброс персонажа",
            f"Вы уверены, что хотите полностью сбросить персонажа «{cid}»?\n\n"
            "Будет удалено:\n"
            "• Вся история сообщений, факты, дневник\n"
            "• Референс внешности (ref.png)\n"
            "• Все скриншоты и сгенерированные картинки\n"
            "• Заметки и эмоциональная память\n\n"
            "Карточка персонажа и аватар-спрайты НЕ будут затронуты.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        from character_catalog import reset_character
        result = reset_character(str(cid))
        self._update_ref_status(cid)
        self.persona_status.setText(result)

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
