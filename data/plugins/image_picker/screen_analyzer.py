# -*- coding: utf-8 -*-
"""ScreenAnalyzer — поиск, анализ и вырезание изображений с экрана.

Поток:
    1. capture() — снимок экрана (через mss).
    2. find_all_images() — детект прямоугольных областей
       (OpenCV контуры или эвристики PIL).
    3. extract_features() — цвета, яркость, контраст, эстетика.
    4. crop() — вырезать ровно bbox региона.

Зависимости (опционально):
    - opencv-python (cv2) для качественного детекта контуров.
    - PIL (Pillow) — обязателен.
"""

from __future__ import annotations

import hashlib
import io
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("screen_analyzer")

# Корень проекта (data/) — пути не зависят от cwd, см. plugins.screen.plugin
_ROOT = Path(__file__).resolve().parents[2]

# Файловый лог (БЛОК 9.11): data/logs/screen_picker.log
try:
    import logging.handlers as _lh
    _log_path = _ROOT / "logs" / "screen_picker.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    if not any(isinstance(h, _lh.RotatingFileHandler) for h in _log.handlers):
        _fh = _lh.RotatingFileHandler(
            str(_log_path), maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8"
        )
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        _log.addHandler(_fh)
except Exception:
    pass

_HAS_CV2 = False
try:
    import cv2
    import numpy as np
    _HAS_CV2 = True
except ImportError:
    cv2 = None
    np = None

try:
    from PIL import Image, ImageStat
except ImportError:
    Image = None
    ImageStat = None

try:
    import mss
except ImportError:
    mss = None


# ── Структуры данных ───────────────────────────────────────────────────

@dataclass
class ScreenRegion:
    """Область на скриншоте, содержащая изображение."""
    bbox: Tuple[int, int, int, int]      # x, y, w, h (относительно всего скрина)
    image: Any                            # PIL.Image — сам регион
    features: Dict[str, Any] = field(default_factory=dict)
    ocr_text: str = ""
    content_type: str = "unknown"          # photo | drawing | screenshot | icon | ui
    embedding: Optional[List[float]] = None

    @property
    def area(self) -> int:
        return self.bbox[2] * self.bbox[3]

    @property
    def aspect_ratio(self) -> float:
        w, h = self.bbox[2], self.bbox[3]
        if h == 0:
            return 1.0
        return w / h


# ── Основной класс ─────────────────────────────────────────────────────

class ScreenAnalyzer:
    """Анализ экрана: захват, поиск изображений, извлечение фич, crop.

    Args:
        min_region_size: минимальная сторона области в px.
        max_regions: максимум возвращаемых регионов.
        exclude_ui: исключать UI-элементы по эвристикам.
    """

    def __init__(
        self,
        min_region_size: int = 100,
        max_regions: int = 50,
        exclude_ui: bool = True,
    ):
        self.min_region_size = min_region_size
        self.max_regions = max_regions
        self.exclude_ui = exclude_ui

    # ── Захват экрана ────────────────────────────────────────────────

    def capture(self, mode: str = "full", monitor_index: int = 1) -> Optional[Path]:
        """Сделать скриншот и сохранить во временный файл.

        Args:
            mode: "full" — весь монитор, "active" — активное окно.
            monitor_index: индекс монитора (1-based).

        Returns:
            Путь к сохранённому PNG или None при ошибке.
        """
        cache_dir = _ROOT / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        out = cache_dir / "screen_last.png"

        if mss is None:
            _log.error("capture: mss not installed")
            return None

        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                if monitor_index < 0 or monitor_index >= len(monitors):
                    monitor_index = 1
                mon = monitors[monitor_index]
                raw = sct.grab(mon)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                img.save(str(out), format="PNG")
                _log.debug("capture: %s (%dx%d)", out, img.width, img.height)
                return out
        except Exception as e:
            _log.error("capture: %s", e)
            return None

    # ── Поиск изображений ────────────────────────────────────────────

    def find_all_images(self, screenshot: Path) -> List[ScreenRegion]:
        """Найти все изображения-кандидаты на скриншоте.

        Использует OpenCV (контуры) если доступен, иначе PIL-эвристики.

        Returns:
            Список ScreenRegion, отсортированный по убыванию площади.
        """
        if not screenshot or not screenshot.is_file():
            _log.warning("find_all_images: file not found %s", screenshot)
            return []

        regions: List[ScreenRegion] = []

        if _HAS_CV2 and cv2 and np:
            regions = self._find_via_cv2(screenshot)
        else:
            regions = self._find_via_pil(screenshot)

        # Фильтр по размеру и соотношению сторон
        regions = [
            r for r in regions
            if r.bbox[2] >= self.min_region_size
            and r.bbox[3] >= self.min_region_size
            and 0.3 <= r.aspect_ratio <= 3.0
        ]

        # Исключить UI-элементы
        if self.exclude_ui:
            regions = [r for r in regions if not self._is_ui_element(r)]

        # Извлечь фичи для каждого
        for r in regions:
            try:
                self.extract_features(r)
            except Exception as e:
                _log.warning("extract_features: %s", e)

        # Сортировка по площади (убывание)
        regions.sort(key=lambda r: r.area, reverse=True)

        # Лимит
        if len(regions) > self.max_regions:
            regions = regions[:self.max_regions]

        _log.debug("find_all_images: %d regions", len(regions))
        return regions

    def _find_via_cv2(self, screenshot: Path) -> List[ScreenRegion]:
        """Детект изображений через OpenCV контуры."""
        img = cv2.imread(str(screenshot))
        if img is None:
            return []

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Адаптивная бинаризация
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 11, 2
        )

        # Морфология для объединения близких контуров
        kernel = np.ones((5, 5), np.uint8)
        closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        pil_img = Image.open(str(screenshot))

        regions: List[ScreenRegion] = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            # Фильтр: не слишком маленькие
            if w < self.min_region_size or h < self.min_region_size:
                continue
            # Фильтр: не слишком вытянутые
            if w > h * 4 or h > w * 4:
                continue
            try:
                region_img = pil_img.crop((x, y, x + w, y + h))
                regions.append(ScreenRegion(
                    bbox=(x, y, w, h),
                    image=region_img,
                    content_type="photo",  # будет уточнено
                ))
            except Exception:
                continue

        return regions

    def _find_via_pil(self, screenshot: Path) -> List[ScreenRegion]:
        """Детект через PIL: поиск прямоугольных областей с контентом.

        Разбивает скриншот на сетку и оставляет ячейки с высокой
        вариативностью цвета (не сплошная заливка).
        """
        try:
            pil_img = Image.open(str(screenshot))
        except Exception as e:
            _log.error("find_via_pil: %s", e)
            return []

        w, h = pil_img.size
        step = self.min_region_size // 2
        tile = self.min_region_size * 2

        regions: List[ScreenRegion] = []
        seen: set = set()

        for y in range(0, h - self.min_region_size, step):
            for x in range(0, w - self.min_region_size, step):
                x2 = min(x + tile, w)
                y2 = min(y + tile, h)
                if x2 - x < self.min_region_size or y2 - y < self.min_region_size:
                    continue

                # Проверка на разнообразие цветов
                try:
                    bit = pil_img.crop((x, y, x2, y2))
                    stat = ImageStat.Stat(bit)
                    std = stat.stddev
                    if not std:
                        continue
                    mean_std = sum(std) / len(std)
                    # Если std > 30 — есть контент (не сплошной цвет)
                    if mean_std < 30:
                        continue
                except Exception:
                    continue

                # Проверить, не пересекается ли с уже найденным
                cx, cy = (x + x2) // 2, (y + y2) // 2
                key = (cx // (step * 2), cy // (step * 2))
                if key in seen:
                    continue
                seen.add(key)

                regions.append(ScreenRegion(
                    bbox=(x, y, x2 - x, y2 - y),
                    image=bit,
                    content_type="photo",
                ))

        return regions

    # ── Извлечение фич ────────────────────────────────────────────────

    def extract_features(self, region: ScreenRegion) -> Dict[str, Any]:
        """Извлечь признаки из региона: цвета, яркость, контраст, эстетика.

        Заполняет region.features и возвращает их.
        """
        img = region.image
        if img is None:
            return {}

        features: Dict[str, Any] = {}

        # 1. Доминирующие цвета (упрощённо — средние каналы)
        try:
            stat = ImageStat.Stat(img)
            mean = stat.mean
            features["mean_color"] = (
                int(mean[0]) if len(mean) > 0 else 0,
                int(mean[1]) if len(mean) > 1 else 0,
                int(mean[2]) if len(mean) > 2 else 0,
            )
            std = stat.stddev
            features["std_color"] = (
                int(std[0]) if len(std) > 0 else 0,
                int(std[1]) if len(std) > 1 else 0,
                int(std[2]) if len(std) > 2 else 0,
            )
        except Exception:
            features["mean_color"] = (128, 128, 128)
            features["std_color"] = (0, 0, 0)

        # 2. Яркость (0..255)
        try:
            gray = img.convert("L")
            gray_stat = ImageStat.Stat(gray)
            features["brightness"] = gray_stat.mean[0] if gray_stat.mean else 128
            features["contrast"] = gray_stat.stddev[0] if gray_stat.stddev else 0
        except Exception:
            features["brightness"] = 128
            features["contrast"] = 0

        # 3. Насыщенность (если RGB)
        if img.mode == "RGB":
            try:
                rp, gp, bp = img.split()
                r_mean = ImageStat.Stat(rp).mean[0] or 0
                g_mean = ImageStat.Stat(gp).mean[0] or 0
                b_mean = ImageStat.Stat(bp).mean[0] or 0
                features["saturation"] = (
                    abs(r_mean - g_mean) + abs(g_mean - b_mean) + abs(b_mean - r_mean)
                ) / 3
            except Exception:
                features["saturation"] = 0

        # 4. Эстетический скор (эвристика)
        features["aesthetic_score"] = self._aesthetic_score(features)

        # 5. Определить content_type
        features["content_type"] = self._classify_content_type(img, features)
        region.content_type = features["content_type"]

        region.features = features
        return features

    @staticmethod
    def _aesthetic_score(features: Dict[str, Any]) -> float:
        """Эстетический скор 0..1 на основе эвристик.

        Учитывает: яркость (не слишком тёмное/светлое), контраст,
        насыщенность.
        """
        score = 0.5  # базовый

        brightness = features.get("brightness", 128)
        if brightness < 30:
            score -= 0.2
        elif brightness > 240:
            score -= 0.1
        elif 60 <= brightness <= 200:
            score += 0.1

        contrast = features.get("contrast", 0)
        if contrast > 40:
            score += 0.1
        elif contrast < 10:
            score -= 0.1

        saturation = features.get("saturation", 0)
        if saturation > 30:
            score += 0.1
        elif saturation < 5:
            score -= 0.05

        return max(0.0, min(1.0, score))

    @staticmethod
    def _classify_content_type(img: Any, features: Dict[str, Any]) -> str:
        """Определить тип контента: photo | drawing | icon | ui | text."""
        w, h = img.size if hasattr(img, "size") else (0, 0)
        if w == 0 or h == 0:
            return "unknown"

        area = w * h
        contrast = features.get("contrast", 0)
        saturation = features.get("saturation", 0)

        if area < 200 * 200 and saturation < 20:
            return "icon"
        if contrast < 10 and saturation < 15:
            return "ui"
        if contrast > 80 and saturation < 10:
            return "text"
        if saturation > 50:
            return "drawing"
        if 10 <= saturation <= 50:
            return "photo"
        return "screenshot"

    # ── UI-фильтр ──────────────────────────────────────────────────────

    def _is_ui_element(self, region: ScreenRegion) -> bool:
        """Эвристика: является ли регион UI-элементом.

        Возвращает True, если регион похож на кнопку/панель/скроллбар.
        """
        feat = region.features
        if not feat:
            return False

        w, h = region.bbox[2], region.bbox[3]

        # Скроллбары и навигация — узкие и длинные
        if min(w, h) < 20 and max(w, h) > 100:
            return True

        # Иконки
        if feat.get("content_type") == "icon":
            return True

        return False

    # ── Вырезание области ────────────────────────────────────────────

    def crop(
        self,
        region: ScreenRegion,
        output: Optional[Path] = None,
        padding: int = 4,
        source_screenshot: Optional[Path] = None,
    ) -> Optional[Path]:
        """Вырезать регион из скриншота и сохранить как PNG.

        Args:
            region: регион для вырезания.
            output: путь для сохранения (по умолчанию авто).
            padding: дополнительная рамка в px.
            source_screenshot: исходный скриншот (если не указан,
                              берётся region.image).

        Returns:
            Путь к сохранённому PNG.
        """
        cropped = None
        if source_screenshot and source_screenshot.is_file():
            try:
                full = Image.open(str(source_screenshot))
            except Exception as e:
                _log.error("crop: open source %s", e)
                return None
            x, y, w, h = region.bbox
            x1 = max(0, x - padding)
            y1 = max(0, y - padding)
            x2 = x + w + padding
            y2 = y + h + padding
            try:
                cropped = full.crop((x1, y1, x2, y2))
            except Exception as e:
                _log.error("crop: %s", e)
                return None

        if cropped is None:
            cropped = region.image
            if cropped is None:
                return None

        # Определить имя файла
        if output is None:
            pick_dir = _ROOT / "assistant_picks"
            pick_dir.mkdir(parents=True, exist_ok=True)
            buf = io.BytesIO()
            cropped.save(buf, format="PNG")
            digest = hashlib.md5(buf.getvalue()).hexdigest()[:12]
            ts = int(time.time())
            output = pick_dir / f"crop_{digest}_{ts}.png"
        else:
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)

        try:
            cropped.save(str(output), format="PNG")
            _log.debug("crop: %s (%dx%d)", output, cropped.width, cropped.height)
            return output
        except Exception as e:
            _log.error("crop: save %s", e)
            return None