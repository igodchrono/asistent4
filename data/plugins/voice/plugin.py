# -*- coding: utf-8 -*-
"""Офлайн-распознавание русской речи и озвучивание ответов."""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any, Optional

from core.plugin_api import AppContext, Plugin, SettingField


class PluginImpl(Plugin):
    id = "voice"
    name = "Голос"
    version = "1.2.0"
    description = "Vosk STT + Silero/pyttsx3 TTS. Wake-фраза при постоянном прослушивании."
    settings_tab = "own"
    settings_tab_title = "Голос"
    settings_schema = [
        SettingField("enabled", "Включить голосовой плагин", "bool", False),
        SettingField("listen_enabled", "Слушать микрофон постоянно", "bool", False),
        SettingField("wake_enabled", "Нужна фраза активации", "bool", True),
        SettingField("wake_phrase", "Фраза активации", "str", "лисичка"),
        SettingField("wake_window_seconds", "Окно команды после активации (сек)", "int", 12, min_value=3, max_value=60),
        SettingField("speak_enabled", "Озвучивать ответы", "bool", True),
        SettingField("model_path", "Путь к модели Vosk", "str", "vosk-model-small-ru-0.22"),
        SettingField("sample_rate", "Частота микрофона", "int", 16000, min_value=8000, max_value=48000),
        SettingField("voice_rate", "Скорость речи", "int", 175, min_value=80, max_value=300),
        SettingField("voice_volume", "Громкость речи", "float", 1.0, min_value=0.0, max_value=1.0),
        SettingField("voice_id", "Голос озвучивания", "choice", "xenia", choices=["xenia", "kseniya", "baya", "aidar", "eugene"]),
        SettingField("microphone_index", "Микрофон", "int", -1, min_value=-1, max_value=256),
        SettingField("speaker_index", "Динамик", "int", -1, min_value=-1, max_value=256),
        SettingField("stt_engine", "Движок распознавания", "choice", "vosk", choices=["vosk"]),
        SettingField("tts_engine", "Движок озвучивания", "choice", "silero", choices=["silero", "pyttsx3"]),
    ]

    def __init__(self) -> None:
        self.app: Optional[AppContext] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._speak_stop = threading.Event()
        self._speak_gen = 0
        self._speech_queue: queue.Queue[str] = queue.Queue()
        self._model = None
        self._recognizer = None
        self._speaker_lock = threading.Lock()
        self._pyttsx_engine = None
        self._settings_widgets = {}
        self._armed_until = 0.0

    def setup_settings_tab(self, tab: Any, app: AppContext) -> bool:
        from PyQt5 import QtWidgets

        self.app = app
        layout = QtWidgets.QVBoxLayout(tab)
        layout.addWidget(QtWidgets.QLabel("<b>Голосовой ввод</b>"))
        listen = QtWidgets.QCheckBox("Слушать микрофон постоянно")
        listen.setChecked(bool(self._setting("listen_enabled", False)))
        layout.addWidget(listen)
        wake_en = QtWidgets.QCheckBox("Только после фразы активации (рекомендуется)")
        wake_en.setChecked(bool(self._setting("wake_enabled", True)))
        layout.addWidget(wake_en)
        speak_en = QtWidgets.QCheckBox("Озвучивать ответы ассистента")
        speak_en.setChecked(bool(self._setting("speak_enabled", True)))
        layout.addWidget(speak_en)
        wake_phrase = QtWidgets.QLineEdit(str(self._setting("wake_phrase", "лисичка") or "лисичка"))
        wake_phrase.setPlaceholderText("например: лисичка")
        wake_win = QtWidgets.QSpinBox()
        wake_win.setRange(3, 60)
        wake_win.setValue(int(self._setting("wake_window_seconds", 12) or 12))
        form0 = QtWidgets.QFormLayout()
        form0.addRow("Фраза активации", wake_phrase)
        form0.addRow("Секунд на команду после фразы", wake_win)
        layout.addLayout(form0)
        layout.addWidget(QtWidgets.QLabel("Модель Vosk и язык: русский офлайн"))
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)
        stt = QtWidgets.QComboBox()
        stt.addItem("Vosk — офлайн, русский", "vosk")
        tts = QtWidgets.QComboBox()
        tts.addItem("Silero — офлайн, русский", "silero")
        tts.addItem("pyttsx3 — системные голоса Windows", "pyttsx3")
        tts.setCurrentIndex(max(0, tts.findData(self._setting("tts_engine", "silero"))))
        form.addRow("Распознавание речи", stt)
        form.addRow("Синтез речи", tts)
        self._settings_widgets = {"stt_engine": stt, "tts_engine": tts}
        voices = QtWidgets.QComboBox()
        voices.addItem("xenia — Silero, женский", "xenia")
        voices.addItem("kseniya — Silero, женский", "kseniya")
        voices.addItem("baya — Silero, женский", "baya")
        voices.addItem("aidar — Silero, мужской", "aidar")
        voices.addItem("eugene — Silero, мужской", "eugene")
        voices.addItem("Системный голос по умолчанию", "")
        for voice_id, name in self._available_voices():
            voices.addItem(name, voice_id)
        selected = str(self._setting("voice_id", "xenia") or "xenia")
        index = voices.findData(selected)
        if index >= 0:
            voices.setCurrentIndex(index)

        microphones = QtWidgets.QComboBox()
        microphones.addItem("Системный микрофон по умолчанию", -1)
        for index, name in self._available_microphones():
            microphones.addItem(name, index)
        selected_mic = int(self._setting("microphone_index", -1) or -1)
        mic_index = microphones.findData(selected_mic)
        if mic_index >= 0:
            microphones.setCurrentIndex(mic_index)

        form.addRow("Голос озвучивания", voices)
        form.addRow("Микрофон", microphones)
        speakers = QtWidgets.QComboBox()
        speakers.addItem("Системный динамик по умолчанию", -1)
        for index, name in self._available_speakers():
            speakers.addItem(name, index)
        form.addRow("Динамик", speakers)
        self._settings_widgets.update({"voice_id": voices, "microphone_index": microphones, "speaker_index": speakers})
        rate = QtWidgets.QSpinBox()
        rate.setRange(80, 300)
        rate.setValue(int(self._setting("voice_rate", 175) or 175))
        volume = QtWidgets.QSlider()
        volume.setOrientation(1)
        volume.setRange(0, 100)
        volume.setValue(int(float(self._setting("voice_volume", 1.0) or 1.0) * 100))
        form.addRow("Скорость речи", rate)
        form.addRow("Громкость речи", volume)
        self._settings_widgets.update({
            "voice_rate": rate,
            "voice_volume": volume,
            "listen_enabled": listen,
            "wake_enabled": wake_en,
            "speak_enabled": speak_en,
            "wake_phrase": wake_phrase,
            "wake_window_seconds": wake_win,
        })
        layout.addWidget(QtWidgets.QLabel("<b>Внешний TTS API</b> (совместимость со старой версией)"))
        api_url = QtWidgets.QLineEdit(str(self._setting("tts_api_url", "") or ""))
        api_url.setPlaceholderText("https://api.openai.com/v1 или адрес своего сервера")
        api_key = QtWidgets.QLineEdit(str(self._setting("tts_api_key", "") or ""))
        api_key.setEchoMode(QtWidgets.QLineEdit.Password)
        api_model = QtWidgets.QLineEdit(str(self._setting("tts_model", "tts-1") or "tts-1"))
        form.addRow("TTS API URL", api_url)
        form.addRow("TTS API ключ", api_key)
        form.addRow("TTS модель", api_model)
        self._settings_widgets.update({"tts_api_url": api_url, "tts_api_key": api_key, "tts_model": api_model})
        btns = QtWidgets.QHBoxLayout()
        test_btn = QtWidgets.QPushButton("Проверить голос")
        test_btn.clicked.connect(lambda: self.speak("Проверка голосовой озвучки успешно выполнена."))
        stop_btn = QtWidgets.QPushButton("⏹ Стоп воспроизведение")
        stop_btn.clicked.connect(self.stop_speaking)
        refresh_btn = QtWidgets.QPushButton("Обновить устройства")
        refresh_btn.clicked.connect(lambda: self.setup_settings_tab(tab, app))
        btns.addWidget(test_btn)
        btns.addWidget(stop_btn)
        btns.addWidget(refresh_btn)
        layout.addLayout(btns)
        return True

    def collect_settings_tab(self) -> dict:
        if not self._settings_widgets:
            return {}
        return {
            "listen_enabled": self._settings_widgets["listen_enabled"].isChecked(),
            "wake_enabled": self._settings_widgets["wake_enabled"].isChecked(),
            "speak_enabled": self._settings_widgets["speak_enabled"].isChecked(),
            "wake_phrase": self._settings_widgets["wake_phrase"].text().strip() or "лисичка",
            "wake_window_seconds": int(self._settings_widgets["wake_window_seconds"].value()),
            "voice_id": self._settings_widgets["voice_id"].currentData() or "",
            "microphone_index": int(self._settings_widgets["microphone_index"].currentData() or -1),
            "speaker_index": int(self._settings_widgets["speaker_index"].currentData() or -1),
            "voice_rate": int(self._settings_widgets["voice_rate"].value()),
            "voice_volume": self._settings_widgets["voice_volume"].value() / 100.0,
            "stt_engine": self._settings_widgets["stt_engine"].currentData(),
            "tts_engine": self._settings_widgets["tts_engine"].currentData(),
            "tts_api_url": self._settings_widgets["tts_api_url"].text().strip(),
            "tts_api_key": self._settings_widgets["tts_api_key"].text().strip(),
            "tts_model": self._settings_widgets["tts_model"].text().strip(),
        }

    def on_load(self, app: AppContext) -> None:
        self.app = app
        app.state["voice_plugin"] = self
        if app.get_plugin_setting(self.id, "enabled", False) and app.get_plugin_setting(self.id, "listen_enabled", False):
            self.start_listening()

    def on_shutdown(self, app: AppContext) -> None:
        self.stop_speaking()
        self.stop_listening()

    def on_character_changed(self, character_id: str, previous_id: str, app: AppContext) -> None:
        self.app = app
        if app.get_plugin_setting(self.id, "enabled", False) and app.get_plugin_setting(self.id, "listen_enabled", False):
            self.start_listening()
        else:
            self.stop_listening()

    def on_after_llm(self, reply: str, app: AppContext) -> str:
        if app.get_plugin_setting(self.id, "enabled", False) and app.get_plugin_setting(self.id, "speak_enabled", True):
            self.speak(reply)
        return reply

    def start_listening(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._listen_loop, name="voice-listener", daemon=True)
        self._thread.start()
        return True

    def stop_listening(self) -> None:
        self._stop.set()
        self._thread = None

    def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._cancel_speech(quiet=True)
        self._speak_stop.clear()
        self._speak_gen += 1
        gen = self._speak_gen
        threading.Thread(target=self._speak_worker, args=(text, gen), name="voice-speaker", daemon=True).start()

    def stop_speaking(self) -> None:
        """Остановить текущее озвучивание сразу."""
        self._cancel_speech(quiet=False)

    def _cancel_speech(self, quiet: bool = False) -> None:
        self._speak_stop.set()
        self._speak_gen += 1
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        eng = self._pyttsx_engine
        if eng is not None:
            try:
                eng.stop()
            except Exception:
                pass
        if not quiet:
            print("voice: воспроизведение остановлено", flush=True)

    def _listen_loop(self) -> None:
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model
        except ImportError as exc:
            print(f"voice: установите vosk и sounddevice: {exc}", flush=True)
            return
        try:
            model_path = Path(self._setting("model_path", "vosk-model-small-ru-0.22"))
            if not model_path.is_absolute():
                model_path = Path(__file__).resolve().parents[2] / model_path
            if not model_path.exists():
                print(f"voice: модель Vosk не найдена: {model_path}", flush=True)
                return
            sample_rate = int(self._setting("sample_rate", 16000) or 16000)
            self._model = Model(str(model_path))
            self._recognizer = KaldiRecognizer(self._model, sample_rate)
            audio_queue: queue.Queue[bytes] = queue.Queue()

            def callback(indata, frames, callback_time, status):
                if status:
                    print(f"voice microphone: {status}", flush=True)
                audio_queue.put(bytes(indata))

            mic_index = int(self._setting("microphone_index", -1) or -1)
            stream_options = {"device": mic_index} if mic_index >= 0 else {}
            with sd.RawInputStream(samplerate=sample_rate, blocksize=8000, dtype="int16", channels=1, callback=callback, **stream_options):
                wake = ""
                if self._setting("wake_enabled", True):
                    wake = str(self._setting("wake_phrase", "лисичка") or "лисичка")
                    print(f"voice: микрофон включён, wake=«{wake}»", flush=True)
                else:
                    print("voice: микрофон включён (без wake-фразы)", flush=True)
                while not self._stop.is_set():
                    try:
                        data = audio_queue.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if self._recognizer.AcceptWaveform(data):
                        result = json.loads(self._recognizer.Result()).get("text", "").strip()
                        if result:
                            self._submit_text(result)
        except Exception as exc:
            print(f"voice: ошибка микрофона: {exc}", flush=True)

    def _submit_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        # Wake-фраза при постоянном прослушивании
        if self.app and self._setting("listen_enabled", False) and self._setting("wake_enabled", True):
            import time as _time
            phrase = str(self._setting("wake_phrase", "лисичка") or "лисичка").strip().lower()
            low = text.lower()
            window_sec = int(self._setting("wake_window_seconds", 12) or 12)
            armed = _time.time() < float(getattr(self, "_armed_until", 0) or 0)

            if phrase and phrase in low:
                # «лисичка, который час» → команда без фразы
                rest = low
                for p in (phrase + ",", phrase + " ", phrase):
                    if rest.startswith(p):
                        rest = rest[len(p):].strip()
                        break
                else:
                    # фраза в середине/конце
                    rest = low.replace(phrase, " ", 1).strip()
                    rest = " ".join(rest.split())
                self._armed_until = _time.time() + window_sec
                if not rest:
                    print(f"voice: wake «{phrase}» — слушаю команду ~{window_sec}с", flush=True)
                    return
                text = rest
            elif not armed:
                # игнор фона без активации
                return
            # armed и без wake в этой реплике — принимаем как команду

        window = getattr(self.app, "window", None) if self.app else None
        if window is not None and hasattr(window, "submit_text"):
            print(f"voice → chat: {text}", flush=True)
            try:
                from PyQt5.QtCore import QMetaObject, Qt, Q_ARG
                QMetaObject.invokeMethod(
                    window, "submit_text", Qt.QueuedConnection, Q_ARG(str, text)
                )
            except Exception:
                if hasattr(window, "post"):
                    window.post(lambda t=text: window.submit_text(t))
                else:
                    window.submit_text(text)

    def _speak_worker(self, text: str, gen: int) -> None:
        if self._speak_stop.is_set() or gen != self._speak_gen:
            return
        with self._speaker_lock:
            if self._speak_stop.is_set() or gen != self._speak_gen:
                return
            try:
                if str(self._setting("tts_engine", "silero") or "silero") == "silero":
                    self._speak_silero(text, gen)
                    return
                self._speak_pyttsx3(text, gen)
            except Exception as exc:
                print(f"voice: ошибка озвучивания: {exc}", flush=True)

    def _speak_pyttsx3(self, text: str, gen: int) -> None:
        import pyttsx3
        engine = pyttsx3.init()
        self._pyttsx_engine = engine
        engine.setProperty("rate", int(self._setting("voice_rate", 175) or 175))
        voice_id = str(self._setting("voice_id", "") or "")
        if voice_id and voice_id not in ("xenia", "kseniya", "baya", "aidar", "eugene"):
            engine.setProperty("voice", voice_id)
        engine.setProperty("volume", float(self._setting("voice_volume", 1.0) or 1.0))
        if self._speak_stop.is_set() or gen != self._speak_gen:
            engine.stop()
            return
        engine.say(text)
        engine.runAndWait()
        engine.stop()
        self._pyttsx_engine = None

    def _ensure_silero(self):
        if getattr(self, "_silero_model", None) is not None:
            return self._silero_model
        import torch
        loaded = torch.hub.load(
            "snakers4/silero-models", "silero_tts",
            language="ru", speaker="v4_ru", trust_repo=True,
        )
        model = loaded[0] if isinstance(loaded, (tuple, list)) else loaded
        if model is None:
            raise RuntimeError("torch.hub вернул пустую модель Silero")
        try:
            moved = model.to("cpu")
            if moved is not None:
                model = moved
        except Exception:
            pass
        if not hasattr(model, "apply_tts"):
            raise RuntimeError(f"Silero без apply_tts: {type(model)}")
        self._silero_model = model
        print(f"voice: Silero ready speakers={getattr(model, 'speakers', [])}", flush=True)
        return model

    def _speak_silero(self, text: str, gen: int) -> None:
        import sounddevice as sd
        if self._speak_stop.is_set() or gen != self._speak_gen:
            return
        try:
            model = self._ensure_silero()
        except Exception as exc:
            print(f"voice: Silero не загрузилась ({exc}), fallback pyttsx3", flush=True)
            self._speak_pyttsx3(text, gen)
            return
        if self._speak_stop.is_set() or gen != self._speak_gen:
            return
        voice = str(self._setting("voice_id", "xenia") or "xenia")
        speakers = list(getattr(model, "speakers", []) or [])
        if speakers and voice not in speakers:
            voice = "xenia" if "xenia" in speakers else speakers[0]
        audio = model.apply_tts(
            text=text, speaker=voice, sample_rate=48000, put_accent=True, put_yo=True,
        )
        if self._speak_stop.is_set() or gen != self._speak_gen:
            return
        samples = audio.detach().cpu().numpy()
        sd.stop()
        sd.play(samples, 48000, blocking=False)
        # ждать конец или стоп
        try:
            import numpy as np
            dur = float(len(samples)) / 48000.0
        except Exception:
            dur = 30.0
        step = 0.05
        waited = 0.0
        while waited < dur + 0.2:
            if self._speak_stop.is_set() or gen != self._speak_gen:
                sd.stop()
                return
            self._speak_stop.wait(step)
            waited += step
        sd.stop()

    def _setting(self, key: str, default: Any = None) -> Any:
        if self.app is None:
            return default
        return self.app.get_plugin_setting(self.id, key, default)

    @staticmethod
    def _available_voices() -> list[tuple[str, str]]:
        try:
            import pyttsx3
            engine = pyttsx3.init()
            result = []
            for voice in engine.getProperty("voices") or []:
                voice_id = str(getattr(voice, "id", "") or "")
                name = str(getattr(voice, "name", "") or voice_id)
                languages = getattr(voice, "languages", []) or []
                language = " ".join(str(item) for item in languages)
                label = f"{name} ({language})" if language else name
                if voice_id:
                    result.append((voice_id, label))
            engine.stop()
            return result
        except Exception as exc:
            print(f"voice: не удалось получить список голосов: {exc}", flush=True)
            return []

    @staticmethod
    def _available_speakers() -> list[tuple[int, str]]:
        try:
            import sounddevice as sd
            return [(index, str(device.get("name") or f"Динамик {index}")) for index, device in enumerate(sd.query_devices()) if int(device.get("max_output_channels", 0) or 0) > 0]
        except Exception:
            return []

    @staticmethod
    def _available_microphones() -> list[tuple[int, str]]:
        try:
            import sounddevice as sd
            result = []
            for index, device in enumerate(sd.query_devices()):
                if int(device.get("max_input_channels", 0) or 0) > 0:
                    result.append((index, str(device.get("name") or f"Микрофон {index}")))
            return result
        except Exception as exc:
            print(f"voice: не удалось получить список микрофонов: {exc}", flush=True)
            return []
