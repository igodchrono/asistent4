# -*- coding: utf-8 -*-
"""PreferenceMemory — память предпочтений пользователя.

Хранит, что пользователю нравилось / не нравилось из выборов с экрана,
обновляет вектор предпочтений после обратной связи (👍/👎/«ещё»),
умеет скор. региона 0..1, top-теги, экспоненциальное забывание (decay)
и сброс.

Хранение:
    - data/preferences/user.json      — текущее состояние предпочтений
    - data/preferences/history.jsonl  — машиночитаемая история выборов
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("preference_memory")

# Корень проекта (data/) — пути не зависят от cwd
_ROOT = Path(__file__).resolve().parents[1]

# Файловый лог (БЛОК 9.11): data/logs/preferences.log
try:
    import logging.handlers as _lh
    _log_path = _ROOT / "logs" / "preferences.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    if not any(isinstance(h, _lh.RotatingFileHandler) for h in _log.handlers):
        _fh = _lh.RotatingFileHandler(
            str(_log_path), maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8"
        )
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        _log.addHandler(_fh)
except Exception:
    pass

# ── Паттерны цветов и тегов ─────────────────────────────────────────────

_COLOR_NAMES: Dict[str, Tuple[int, int, int]] = {
    "красный": (220, 40, 40),
    "оранжевый": (230, 120, 30),
    "жёлтый": (230, 200, 40),
    "зелёный": (50, 160, 60),
    "голубой": (60, 160, 230),
    "синий": (40, 60, 200),
    "фиолетовый": (140, 60, 200),
    "розовый": (220, 100, 160),
    "белый": (255, 255, 255),
    "чёрный": (20, 20, 20),
    "серый": (128, 128, 128),
    "коричневый": (120, 70, 30),
}

# Стемминг русских слов → англ. теги
_TAG_STEMS: Dict[str, str] = {
    "тёмн": "dark", "темн": "dark", "тёмный": "dark", "тёмная": "dark",
    "светл": "light", "светлый": "light", "светлая": "light",
    "ярк": "bright", "яркий": "bright", "яркая": "bright",
    "сер": "gray", "серый": "gray", "серая": "gray",
    "цветн": "colorful", "цветной": "colorful", "цветная": "colorful",
    "кот": "cat", "кошк": "cat", "котик": "cat", "кошка": "cat",
    "собак": "dog", "пёс": "dog", "пес": "dog", "собачка": "dog",
    "лис": "fox", "лиса": "fox", "лисичк": "fox",
    "аниме": "anime", "арт": "art", "картин": "art",
    "фото": "photo", "пейзаж": "landscape", "природ": "nature",
    "космос": "space", "звезд": "stars", "звезда": "stars",
    "город": "city", "машин": "car", "авто": "car",
    "море": "sea", "океан": "sea", "река": "river",
    "цвет": "flower", "цветы": "flower", "цветок": "flower",
    "мульт": "cartoon", "мультик": "cartoon",
    "реалистичн": "realistic", "реализм": "realistic",
    "минимализм": "minimal", "минималистичн": "minimal",
    "эстет": "aesthetic", "эстетик": "aesthetic",
    "неон": "neon", "неонов": "neon",
    "киберпанк": "cyberpunk", "футуризм": "futuristic",
    "старый": "retro", "винтаж": "retro", "ретро": "retro",
}


# ── Хелперы ─────────────────────────────────────────────────────────────

def _color_distance(c1: Tuple[int, int, int], c2: Tuple[int, int, int]) -> float:
    """Евклидово расстояние между цветами в RGB (нормировано 0..1)."""
    d = (c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2
    return math.sqrt(d) / math.sqrt(3 * 255 * 255)


def tags_from_text(text: str) -> List[str]:
    """Извлечь англ. теги из русского текста («тёмная» → 'dark')."""
    low = (text or "").lower()
    return [tag for stem, tag in _TAG_STEMS.items() if stem in low]


def color_name_from_rgb(rgb: Tuple[int, int, int]) -> str:
    """Ближайшее по расстоянию название цвета."""
    best, best_d = "серый", 1e9
    for name, c in _COLOR_NAMES.items():
        d = _color_distance(rgb, c)
        if d < best_d:
            best, best_d = name, d
    return best


# ── Структуры ───────────────────────────────────────────────────────────

@dataclass
class Preference:
    """Текущее состояние предпочтений пользователя."""
    feature_vector: List[float] = field(default_factory=list)
    tags: Dict[str, float] = field(default_factory=dict)        # тег → вес
    colors: Dict[str, float] = field(default_factory=dict)      # имя → вес
    styles: Dict[str, float] = field(default_factory=dict)      # стиль → вес
    last_updated: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Preference:
        safe = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**safe)


class PreferenceMemory:
    """Память предпочтений с сохранением в JSON и историей в JSONL.

    Args:
        storage: путь к JSON-файлу состояния.
        history: путь к JSONL-файлу истории.
        decay_half_life_days: период полураспада весов.
    """

    def __init__(
        self,
        storage: Optional[Path] = None,
        history: Optional[Path] = None,
        decay_half_life_days: int = 30,
    ):
        self.storage = Path(storage) if storage is not None else (
            _ROOT / "preferences" / "user.json"
        )
        self.history = (
            Path(history) if history else self.storage.with_name("history.jsonl")
        )
        self.decay_half_life = decay_half_life_days * 86400.0
        self.preference = Preference()
        self.load()

    # ── Загрузка / сохранение ─────────────────────────────────────────

    def load(self) -> None:
        """Загрузить предпочтения из JSON, если файл существует."""
        try:
            if self.storage.is_file():
                data = json.loads(self.storage.read_text(encoding="utf-8"))
                self.preference = Preference.from_dict(data)
                self.decay()
        except Exception as e:
            _log.warning("load: %s", e)
            self.preference = Preference()

    def save(self) -> None:
        """Сохранить текущее состояние в JSON."""
        try:
            self.storage.parent.mkdir(parents=True, exist_ok=True)
            self.storage.write_text(
                json.dumps(self.preference.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            _log.error("save: %s", e)

    def _append_history(self, entry: Dict[str, Any]) -> None:
        """Добавить запись в history.jsonl."""
        try:
            self.history.parent.mkdir(parents=True, exist_ok=True)
            if self.history.is_file() and self.history.stat().st_size > 5 * 1024 * 1024:
                self._trim_history()
            with self.history.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            _log.warning("append_history: %s", e)

    def _trim_history(self, keep: int = 2000) -> None:
        """Оставить последние `keep` записей в history.jsonl."""
        try:
            lines = self.history.read_text(encoding="utf-8").splitlines()
            if len(lines) > keep:
                self.history.write_text(
                    "\n".join(lines[-keep:]) + "\n", encoding="utf-8"
                )
        except Exception:
            pass

    # ── Основные операции ─────────────────────────────────────────────

    def record_choice(
        self,
        chosen: Any,
        rejected: Optional[List[Any]] = None,
        explicit_feedback: Optional[str] = None,
        request_text: str = "",
    ) -> None:
        """Зафиксировать выбор и обновить предпочтения.

        Args:
            chosen: ScreenRegion (или dict с features), который выбрали.
            rejected: регионы, которые отклонили.
            explicit_feedback: текстовый сигнал («нравится», «не то»).
            request_text: исходный запрос («выбери тёмное»).
        """
        strength = 1.0
        sign = 1.0
        text_signal = (explicit_feedback or "") + " " + request_text

        # Усиление / ослабление по явной реакции
        if explicit_feedback:
            low = explicit_feedback.lower()
            for strong_pos in ("супер", "отлично", "идеально", "замечательно", "❤", "🔥"):
                if strong_pos in low:
                    strength = 1.5
            for strong_neg in ("ужас", "отврат", "плохо", "худш", "фу"):
                if strong_neg in low:
                    strength = 1.5
            for neg in ("не нрав", "не то", "ненавиж", "не под", "👎"):
                if neg in low:
                    sign = -1.0
            for mild_neg in ("давай другое", "другое", "не так"):
                if mild_neg in low:
                    sign = -0.5

        chosen_feat = self._features_of(chosen)
        if chosen_feat:
            self._update_from_features(chosen_feat, sign * strength)

        # Отклонённые — слабый минус
        for reg in rejected or []:
            rf = self._features_of(reg)
            if rf:
                self._update_from_features(rf, -0.2 * strength)

        # Теги из текста запроса — усиление
        for tag in tags_from_text(text_signal):
            self.preference.tags[tag] = self.preference.tags.get(tag, 0.0) + 0.4 * strength * sign

        self.preference.last_updated = time.time()
        self.save()

        self._append_history({
            "ts": time.time(),
            "feedback": explicit_feedback or "",
            "request": request_text,
            "chosen_feat": self._features_of(chosen, flat=True),
            "rejected_count": len(rejected or []),
        })
        _log.info("record_choice: sign=%.2f strength=%.2f", sign, strength)

    @staticmethod
    def _features_of(region: Any, flat: bool = False) -> Optional[Dict[str, Any]]:
        """Взять features из ScreenRegion или словаря."""
        feat = None
        if isinstance(region, dict):
            feat = region.get("features") or region
        elif hasattr(region, "features"):
            feat = region.features
        if not feat:
            return None
        if flat:
            out: Dict[str, Any] = {}
            for k, v in feat.items():
                out[k] = list(v) if isinstance(v, tuple) else v
            return out
        return feat

    def _update_from_features(self, feat: Dict[str, Any], weight: float) -> None:
        """Обновить веса: цвета, стили, вектор."""
        if feat.get("mean_color"):
            name = color_name_from_rgb(tuple(feat["mean_color"]))
            self.preference.colors[name] = self.preference.colors.get(name, 0.0) + weight

        content = feat.get("content_type", "")
        if content in ("photo", "drawing", "screenshot"):
            self.preference.styles[content] = (
                self.preference.styles.get(content, 0.0) + weight
            )

        brightness = feat.get("brightness", 128)
        if brightness < 70:
            self.preference.tags["dark"] = self.preference.tags.get("dark", 0.0) + weight
        elif brightness > 190:
            self.preference.tags["light"] = self.preference.tags.get("light", 0.0) + weight
        else:
            self.preference.tags["mid"] = self.preference.tags.get("mid", 0.0) + weight / 2

        sat = feat.get("saturation", 0)
        if sat > 50:
            self.preference.tags["colorful"] = self.preference.tags.get("colorful", 0.0) + weight

        vec = [
            float(brightness) / 255.0 * 2.0,   # яркость — главный сигнал
            float(sat) / 255.0,
            float(feat.get("aesthetic_score", 0.5)) * 0.5,
            float(feat.get("contrast", 0)) / 255.0 * 0.5,
        ]
        cur = self.preference.feature_vector
        if not cur or len(cur) != len(vec):
            cur = [0.0] * len(vec)
        for i in range(len(vec)):
            cur[i] += vec[i] * weight
        self.preference.feature_vector = cur

    def score(self, region: Any) -> float:
        """0..1 — насколько регион похож на прошлые предпочтения."""
        feat = self._features_of(region)
        if not feat:
            return 0.5

        s = 0.0
        cnt = 0

        # Цвет
        if feat.get("mean_color") and self.preference.colors:
            name = color_name_from_rgb(tuple(feat["mean_color"]))
            col_w = self.preference.colors.get(name, 0.0)
            s += min(1.0, abs(col_w) / 3.0) if col_w > 0 else 0.0
            cnt += 1

        # Яркость/насыщенность через вектор
        brightness = feat.get("brightness", 128)
        sat = feat.get("saturation", 0)
        vec = [
            float(brightness) / 255.0 * 2.0,
            float(sat) / 255.0,
            float(feat.get("aesthetic_score", 0.5)) * 0.5,
            float(feat.get("contrast", 0)) / 255.0 * 0.5,
        ]
        if self.preference.feature_vector:
            strength = max(1.0, sum(abs(v) for v in self.preference.feature_vector))
            sim = self._cosine(vec, self.preference.feature_vector)
            s += max(0.0, sim) * min(1.0, strength / 2.0)
            cnt += 1

        # Теги яркости
        tag = "dark" if brightness < 70 else "light" if brightness > 190 else None
        if tag and self.preference.tags.get(tag, 0.0) > 0:
            s += min(1.0, self.preference.tags[tag] / 3.0)
            cnt += 1

        if cnt == 0:
            return 0.5
        return max(0.0, min(1.0, s / cnt))

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        n = min(len(a), len(b))
        if n == 0:
            return 0.0
        aa = bb = dot = 0.0
        for i in range(n):
            dot += a[i] * b[i]
            aa += a[i] * a[i]
            bb += b[i] * b[i]
        if aa == 0 or bb == 0:
            return 0.0
        return dot / (math.sqrt(aa) * math.sqrt(bb))

    def get_preference_vector(self) -> List[float]:
        return list(self.preference.feature_vector)

    def get_top_tags(self, n: int = 5) -> List[Tuple[str, float]]:
        tags = self.preference.tags.copy()
        tags.update(self.preference.colors)
        tags.update(self.preference.styles)
        top = sorted(tags.items(), key=lambda kv: kv[1], reverse=True)
        return top[:n]

    def decay(self, half_life_days: Optional[int] = None) -> None:
        """Экспоненциальное забывание старых предпочтений."""
        if half_life_days is not None:
            self.decay_half_life = half_life_days * 86400.0
        now = time.time()
        age = now - self.preference.last_updated
        if age <= 0 or self.decay_half_life <= 0:
            return
        factor = 0.5 ** (age / self.decay_half_life)
        if factor >= 0.99:
            return

        def _scale(d: Dict[str, float]) -> Dict[str, float]:
            return {k: v * factor for k, v in d.items() if v * factor > 0.01}

        self.preference.tags = _scale(self.preference.tags)
        self.preference.colors = _scale(self.preference.colors)
        self.preference.styles = _scale(self.preference.styles)
        self.preference.feature_vector = [v * factor for v in self.preference.feature_vector]
        self.preference.last_updated = now
        self.save()

    def reset(self) -> None:
        """Полный сброс предпочтений."""
        self.preference = Preference()
        self.save()
        self._append_history({"ts": time.time(), "event": "reset"})
        _log.info("reset: preferences cleared")

    def __repr__(self) -> str:
        return f"PreferenceMemory(top={self.get_top_tags(3)})"