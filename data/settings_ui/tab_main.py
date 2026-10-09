# settings_ui/tab_main.py — Основные: LLM API + сохранённые профили
from PyQt5 import QtWidgets, QtCore
import json
import urllib.request
import config
from core.llm_client import normalize_api_url


class MainTabMixin:
    def _setup_main_tab(self, tab):
        layout = QtWidgets.QVBoxLayout(tab)

        layout.addWidget(QtWidgets.QLabel(
            "API URL — IP или localhost и порт. Примеры:\n"
            "http://127.0.0.1:1234/v1   или   http://192.168.0.10:1234"
        ))
        self.api_url_edit = QtWidgets.QLineEdit()
        self.api_url_edit.setPlaceholderText("http://127.0.0.1:1234/v1")
        layout.addWidget(self.api_url_edit)

        layout.addWidget(QtWidgets.QLabel("API Key (для LM Studio можно lm-studio):"))
        self.api_key_edit = QtWidgets.QLineEdit()
        self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        layout.addWidget(self.api_key_edit)

        layout.addWidget(QtWidgets.QLabel("Модель (имя как в LM Studio):"))
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setEditable(True)
        layout.addWidget(self.model_combo)

        row = QtWidgets.QHBoxLayout()
        refresh_btn = QtWidgets.QPushButton("🔄 Обновить список моделей")
        refresh_btn.setToolTip("GET /v1/models у сервера")
        refresh_btn.clicked.connect(self.load_models_list)
        test_btn = QtWidgets.QPushButton("Проверить связь")
        test_btn.clicked.connect(self.test_connection)
        row.addWidget(refresh_btn)
        row.addWidget(test_btn)
        layout.addLayout(row)

        self.conn_status = QtWidgets.QLabel("")
        self.conn_status.setWordWrap(True)
        self.conn_status.setStyleSheet("color: #aaa;")
        layout.addWidget(self.conn_status)

        # ── Сохранённые профили подключения ────────────────────────
        sep1 = QtWidgets.QFrame()
        sep1.setFrameShape(QtWidgets.QFrame.HLine)
        sep1.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(sep1)

        prof_label = QtWidgets.QLabel("💾 Сохранённые профили подключения:")
        prof_label.setStyleSheet("font-weight: bold; margin-top: 6px;")
        layout.addWidget(prof_label)

        prof_row = QtWidgets.QHBoxLayout()
        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        self.profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        prof_row.addWidget(self.profile_combo, 1)

        self.btn_save_profile = QtWidgets.QPushButton("➕ Сохранить как…")
        self.btn_save_profile.clicked.connect(self._save_profile_dialog)
        self.btn_del_profile = QtWidgets.QPushButton("🗑️ Удалить")
        self.btn_del_profile.clicked.connect(self._delete_profile)
        prof_row.addWidget(self.btn_save_profile)
        prof_row.addWidget(self.btn_del_profile)
        layout.addLayout(prof_row)

        # ── остальные настройки ────────────────────────────────────

        layout.addWidget(QtWidgets.QLabel("Temperature (0.0 – 2.0):"))
        self.temperature_edit = QtWidgets.QLineEdit()
        layout.addWidget(self.temperature_edit)

        layout.addWidget(QtWidgets.QLabel("Max Tokens:"))
        self.max_tokens_edit = QtWidgets.QLineEdit()
        layout.addWidget(self.max_tokens_edit)

        layout.addWidget(QtWidgets.QLabel("Режим (компаньон 18+ / работа):"))
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem("🦊 Компаньон — личный, 18+", "companion")
        self.mode_combo.addItem("💼 Работа — сначала задача, без флирта", "work")
        layout.addWidget(self.mode_combo)

        layout.addWidget(QtWidgets.QLabel("Реплик в контексте (12–80):"))
        self.history_tail_spin = QtWidgets.QSpinBox()
        self.history_tail_spin.setRange(12, 80)
        self.history_tail_spin.setValue(40)
        layout.addWidget(self.history_tail_spin)

        self.economy_check = QtWidgets.QCheckBox("🌱 Экономный режим — генерация только по запросу")
        self.economy_check.setToolTip("Отключает авто-суммаризацию, проактивные сообщения и инициативные генерации. Ассистент отвечает только когда вы её спросили.")
        layout.addWidget(self.economy_check)

        layout.addWidget(QtWidgets.QLabel("System prompt (ядро):"))
        self.system_edit = QtWidgets.QPlainTextEdit()
        self.system_edit.setMaximumHeight(120)
        layout.addWidget(self.system_edit)

        layout.addStretch()

    def _base_url(self) -> str:
        return normalize_api_url(self.api_url_edit.text().strip() or config.API_URL)

    # ── профили ─────────────────────────────────────────────────────

    def _rebuild_profile_combo(self) -> None:
        profiles = list(getattr(config, "SAVED_PROFILES", []) or [])
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItem("— выберите профиль —", None)
        for p in profiles:
            name = (p.get("name") or "").strip()
            if name:
                self.profile_combo.addItem(name, name)
        self.profile_combo.blockSignals(False)

    def _on_profile_selected(self, idx: int) -> None:
        name = self.profile_combo.itemData(idx)
        if not name:
            return
        profiles = list(getattr(config, "SAVED_PROFILES", []) or [])
        for p in profiles:
            if p.get("name") == name:
                self.api_url_edit.setText(p.get("api_url", ""))
                self.api_key_edit.setText(p.get("api_key", ""))
                model = p.get("model", "")
                if model:
                    self.model_combo.setEditText(model)
                return

    def _save_profile_dialog(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Сохранить профиль", "Название профиля:",
            text=""
        )
        if not ok or not name:
            return
        name = name.strip()
        if not name:
            return
        profile = {
            "name": name,
            "api_url": normalize_api_url(self.api_url_edit.text().strip()),
            "api_key": self.api_key_edit.text().strip() or "lm-studio",
            "model": self.model_combo.currentText().strip(),
        }
        profiles = list(getattr(config, "SAVED_PROFILES", []) or [])
        for i, p in enumerate(profiles):
            if p.get("name") == name:
                profiles[i] = profile
                break
        else:
            profiles.append(profile)
        config.SAVED_PROFILES = profiles
        self._rebuild_profile_combo()
        idx = self.profile_combo.findData(name)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        self.conn_status.setStyleSheet("color: #0f0;")
        self.conn_status.setText(f"✅ Профиль «{name}» сохранён")

    def _delete_profile(self) -> None:
        name = self.profile_combo.currentData()
        if not name:
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Удалить профиль",
            f"Удалить профиль «{name}»?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        profiles = list(getattr(config, "SAVED_PROFILES", []) or [])
        config.SAVED_PROFILES = [p for p in profiles if p.get("name") != name]
        self._rebuild_profile_combo()
        self.conn_status.setStyleSheet("color: #aaa;")
        self.conn_status.setText(f"🗑️ Профиль «{name}» удалён")

    # ── список моделей / тест ───────────────────────────────────────

    def load_models_list(self):
        api_url = self._base_url()
        key = self.api_key_edit.text().strip() or getattr(config, "API_KEY", "lm-studio")
        self.conn_status.setText(f"Запрос моделей: {api_url}/models …")
        QtWidgets.QApplication.processEvents()
        errors = []
        names = []
        url = api_url.rstrip("/") + "/models"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"******",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            models = data.get("data") or data.get("models") or []
            for m in models:
                if isinstance(m, dict):
                    names.append(str(m.get("id") or m.get("name") or m))
                else:
                    names.append(str(m))
        except Exception as e:
            errors.append(f"{url}: {e}")

        current = self.model_combo.currentText().strip()
        if names:
            self.model_combo.clear()
            self.model_combo.addItems(names)
            if current:
                idx = self.model_combo.findText(current)
                if idx >= 0:
                    self.model_combo.setCurrentIndex(idx)
                else:
                    self.model_combo.setEditText(current)
            self.conn_status.setStyleSheet("color: #0f0;")
            self.conn_status.setText(f"OK: моделей {len(names)}\n{api_url}")
        else:
            if self.model_combo.count() == 0:
                self.model_combo.addItem(
                    current or getattr(config, "MODEL_NAME", "local-model") or "local-model"
                )
            self.conn_status.setStyleSheet("color: #f66;")
            self.conn_status.setText(
                "Не удалось получить модели.\n" + "\n".join(errors[:4])
            )

    def test_connection(self):
        api_url = self._base_url()
        key = self.api_key_edit.text().strip() or "lm-studio"
        model = self.model_combo.currentText().strip() or "local-model"
        self.conn_status.setText("Тест chat/completions…")
        QtWidgets.QApplication.processEvents()
        url = api_url.rstrip("/") + "/chat/completions"
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
            "stream": False,
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                url, data=body,
                headers={"Authorization": f"******", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            self.conn_status.setStyleSheet("color: #0f0;")
            self.conn_status.setText(f"Связь OK: {reply[:120]!r}")
        except Exception as e:
            self.conn_status.setStyleSheet("color: #f66;")
            self.conn_status.setText(f"Нет связи: {url}\n{e}")

    # ── загрузка / сохранение ───────────────────────────────────────

    def load_main_settings(self) -> None:
        self.api_url_edit.setText(str(getattr(config, "API_URL", "")))
        self.api_key_edit.setText(str(getattr(config, "API_KEY", "")))
        self.model_combo.clear()
        self.model_combo.addItem(str(getattr(config, "MODEL_NAME", "") or "local-model"))
        self.temperature_edit.setText(str(getattr(config, "TEMPERATURE", 0.4)))
        self.max_tokens_edit.setText(str(getattr(config, "MAX_TOKENS", 1000)))
        mode = str(getattr(config, "ASSISTANT_MODE", "companion") or "companion")
        idx = self.mode_combo.findData(mode)
        self.mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        try:
            self.history_tail_spin.setValue(int(getattr(config, "HISTORY_TAIL", 40) or 40))
        except (TypeError, ValueError):
            self.history_tail_spin.setValue(40)
        self.system_edit.setPlainText(str(getattr(config, "SYSTEM_PROMPT", "") or ""))
        self.economy_check.setChecked(bool(getattr(config, "LLM_ECONOMY_MODE", False)))
        self._rebuild_profile_combo()
        QtCore.QTimer.singleShot(300, self.load_models_list)

    def collect_main_settings(self) -> dict:
        try:
            temp = float(self.temperature_edit.text().strip().replace(",", ".") or "0.4")
        except ValueError:
            temp = 0.4
        try:
            tokens = int(self.max_tokens_edit.text().strip() or "1000")
        except ValueError:
            tokens = 1000
        return {
            "API_URL": normalize_api_url(self.api_url_edit.text().strip()),
            "API_KEY": self.api_key_edit.text().strip() or "lm-studio",
            "MODEL_NAME": self.model_combo.currentText().strip(),
            "TEMPERATURE": temp,
            "MAX_TOKENS": tokens,
            "ASSISTANT_MODE": str(self.mode_combo.currentData() or "companion"),
            "HISTORY_TAIL": int(self.history_tail_spin.value()),
            "SYSTEM_PROMPT": self.system_edit.toPlainText(),
            "LLM_ECONOMY_MODE": self.economy_check.isChecked(),
            "SAVED_PROFILES": list(getattr(config, "SAVED_PROFILES", []) or []),
        }
