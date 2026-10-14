# -*- coding: utf-8 -*-
"""GenerationMetrics — метрики качества сгенерированного изображения.

compute_metrics:
- sharpness (Лапласиан),
- brightness, contrast, saturation,
- aesthetic_score (эвристика или CLIP-IQA, если доступно),
- prompt_adherence (CLIP similarity, если доступно),
- face_quality (если детектор лиц доступен),
- nsfw_score (если классификатор доступен),
- artifact_score.

Логирование: data/logs/generation_metrics.jsonl
"""
from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


_log = logging.getLogger("generation_metrics")

_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
_METRICS_LOG = _LOG_DIR / "generation_metrics.jsonl"

_AESTHETIC_WEIGHTS = {
    "sharpness": 0.20,
    "brightness": 0.10,
    "contrast": 0.15,
    "saturation": 0.10,
    "aesthetic": 0.25,
    "prompt_adherence": 0.20,
}

_BRIGHTNESS_OK = (0.15, 0.85)
_CONTRAST_OK = (0.15, 0.75)
_SATURATION_OK = (0.10, 0.80)
def compute_metrics(
    image_path: Path,
    bundle: Any = None,
    use_clip: bool = False,
    prompt_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Вычислить метрики качества изображения.

    Args:
        image_path: путь к PNG/JPEG.
        bundle: PromptBundle (опционально).
        use_clip: если True — пробовать CLIP (пока заглушка).
        prompt_text: текст промта (альтернатива bundle).

    Returns:
        Словарь с метриками 0.0..1.0 + overall.
    """
    metrics = _compute_image_metrics(image_path)
    metrics["aesthetic_score"] = _heuristic_aesthetic(metrics)

    prompt = prompt_text or (getattr(bundle, "positive", "") if bundle else "")
    metrics["prompt_adherence"] = _prompt_adherence_heuristic(metrics, prompt) if prompt else None
    metrics["face_quality"] = None          # требует детектор лиц (не активен)
    metrics["artifact_score"] = _artifact_score(metrics)
    metrics["overall"] = _overall_score(metrics)

    _log_metrics(image_path, metrics, prompt)
    _log.debug("metrics for %s: overall=%.2f", Path(image_path).name, metrics["overall"])
    return metrics


def _compute_image_metrics(path: Path) -> Dict[str, Any]:
    """Sharpness / brightness / contrast / saturation через PIL+numpy."""
    try:
        from PIL import Image, ImageStat, ImageFilter
    except ImportError:
        return {"sharpness": 0.5, "brightness": 0.5, "contrast": 0.5, "saturation": 0.5}

    try:
        img = Image.open(str(path)).convert("RGB")
    except Exception:
        return {"sharpness": 0.5, "brightness": 0.5, "contrast": 0.5, "saturation": 0.5}

    w, h = img.size
    if w < 10 or h < 10:
        return {"sharpness": 0.5, "brightness": 0.5, "contrast": 0.5, "saturation": 0.5}

    # Sharpness (Laplacian variance)
    gray = img.convert("L")
    lap = gray.filter(ImageFilter.Kernel(
        (3, 3), (-1, -1, -1, -1, 8, -1, -1, -1, -1), scale=1))
    try:
        import numpy as np
        variance = float(np.var(np.array(lap, dtype=np.float32)))
        sharpness = min(1.0, math.log(1 + variance) / 8.0)
    except ImportError:
        stat = ImageStat.Stat(lap)
        var = stat.stddev[0] if stat.stddev else 0
        sharpness = min(1.0, var / 50.0)

    stat_rgb = ImageStat.Stat(img)
    brightness = min(1.0, max(0.0, sum(stat_rgb.mean) / (3 * 255.0)))

    try:
        mean = sum(stat_rgb.mean) / 3.0
        std = sum(stat_rgb.stddev) / 3.0
        contrast = min(1.0, std / (mean + 1e-6) / 1.5) if mean > 0 else 0.5
    except Exception:
        contrast = 0.5

    try:
        stat_hsv = ImageStat.Stat(img.convert("HSV"))
        saturation = min(1.0, stat_hsv.mean[1] / 255.0) if len(stat_hsv.mean) > 1 else 0.5
    except Exception:
        saturation = 0.5

    return {
        "sharpness": round(sharpness, 4),
        "brightness": round(brightness, 4),
        "contrast": round(contrast, 4),
        "saturation": round(saturation, 4),
        "width": w,
        "height": h,
        "format": str(getattr(img, "format", "PNG")),
    }
def _heuristic_aesthetic(metrics: Dict[str, Any]) -> float:
    """Эвристическая эстетическая оценка на основе метрик."""
    score = 0.0
    n = 0

    s = metrics.get("sharpness", 0.5)
    if s > 0.4:
        score += min(1.0, s * 1.2)
    elif s < 0.2:
        score += s * 2
    else:
        score += s
    n += 1

    b = metrics.get("brightness", 0.5)
    if _BRIGHTNESS_OK[0] <= b <= _BRIGHTNESS_OK[1]:
        score += 0.8 + 0.2 * (1 - abs(b - 0.5) * 2)
    else:
        score += 0.3
    n += 1

    c = metrics.get("contrast", 0.5)
    if _CONTRAST_OK[0] <= c <= _CONTRAST_OK[1]:
        score += 0.8 + 0.2 * (1 - abs(c - 0.4) * 2)
    else:
        score += 0.3
    n += 1

    sat = metrics.get("saturation", 0.5)
    if _SATURATION_OK[0] <= sat <= _SATURATION_OK[1]:
        score += 0.8 + 0.2 * (1 - abs(sat - 0.4) * 2)
    else:
        score += 0.3
    n += 1

    return round(score / n, 4) if n else 0.5


def _prompt_adherence_heuristic(metrics: Dict[str, Any], prompt: str) -> Optional[float]:
    """Эвристика соответствия промту (заглушка, 0.5 + штраф за экстремумы)."""
    base = 0.5
    b = metrics.get("brightness", 0.5)
    if b < 0.05 or b > 0.95:
        base -= 0.15
    s = metrics.get("sharpness", 0.5)
    if s < 0.1:
        base -= 0.1
    return round(max(0.0, min(1.0, base)), 4)


def _artifact_score(metrics: Dict[str, Any]) -> float:
    """0 = много артефактов, 1 = чисто."""
    score = 1.0
    s = metrics.get("sharpness", 0.5)
    if s < 0.15:
        score -= 0.3
    elif s > 0.85:
        score -= 0.1
    c = metrics.get("contrast", 0.5)
    if c > 0.8:
        score -= 0.2
    if c < 0.1:
        score -= 0.1
    sat = metrics.get("saturation", 0.5)
    if sat > 0.85:
        score -= 0.15
    if sat < 0.05:
        score -= 0.1
    return round(max(0.0, min(1.0, score)), 4)


def _overall_score(metrics: Dict[str, Any]) -> float:
    """Взвешенная итоговая оценка (0..1)."""
    score = 0.0
    total = 0.0
    for key, weight in _AESTHETIC_WEIGHTS.items():
        val = metrics.get(key)
        if val is not None:
            score += val * weight
            total += weight
    artifact = metrics.get("artifact_score", 1.0)
    score = (score / total if total > 0 else 0.5) * artifact
    return round(max(0.0, min(1.0, score)), 4)


def _log_metrics(path: Path, metrics: Dict[str, Any], prompt: str) -> None:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": time.time(),
            "image_path": str(path),
            "metrics": {k: v for k, v in metrics.items() if v is not None},
            "prompt": prompt[:200] if prompt else "",
        }
        with open(str(_METRICS_LOG), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        _log.warning("log_metrics: %s", e)


# ── Fast test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (256, 256), (100, 120, 140))
        d = ImageDraw.Draw(img)
        d.rectangle((40, 40, 200, 200), fill=(180, 50, 70))
        test_path = Path("__test_metrics.png")
        img.save(str(test_path))
    except ImportError:
        print("PIL not available, creating dummy file")
        test_path = Path("__dummy.png")
        test_path.write_text("dummy")

    m = compute_metrics(test_path, prompt_text="a cat on windowsill")
    for k, v in sorted(m.items()):
        print(f"  {k}: {v}")

    test_path.unlink(missing_ok=True)
    print("OK")