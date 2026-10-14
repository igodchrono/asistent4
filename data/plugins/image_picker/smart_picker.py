# -*- coding: utf-8 -*-
"""SmartPicker — умный выбор изображения с экрана.

Объединяет:
    1. ScreenAnalyzer  — поиск регионов + фичи.
    2. PreferenceMemory — скор по прошлым предпочтениям.
    3. persona         — соответствие характеру персонажа.

Финальный скор:
    score = w_query*query_score + w_pref*pref_score + w_persona*persona_score
с штрафом за повтор (diversity): если регион пересекается с прошлым
выбором больше 50% → -0.3.

Поток pick():
    capture → find_all_images → скоринг → лучший → crop → record_choice.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.preference_memory import PreferenceMemory, tags_from_text

_log = logging.getLogger("smart_picker")

_PERSONA_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "tender": ("мил", "нежн", "пушист", "уют", "тёпл", "мягк"),
    "playful": ("весел", "игр", "забав", "ярк", "смеш", "мульт"),
    "mystic": ("таинств", "мисти", "туман", "лун", "звезд", "космос"),
    "artistic": ("худож", "живоп", "акварел", "масл", "рисов"),
    "dark": ("тёмн", "ночь", "мрачн", "черн", "неон", "киберпанк"),
    "cheerful": ("солнечн", "светл", "радост", "цветн", "праздник"),
}


def _persona_style(persona: Any) -> str:
    """Определить стиль персонажа из dict/строки/карточки.

    Returns:
        Строка-описание стиля для сравнения с тегами региона.
    """
    if persona is None:
        return ""
    if isinstance(persona, dict):
        parts = []
        for key in ("name", "style", "description", "card"):
            val = persona.get(key)
            if val:
                parts.append(str(val))
        return " ".join(parts)
    return str(persona)


@dataclass
class PickResult:
    """Результат выбора."""
    region: Any                       # ScreenRegion
    crop_path: Optional[Path] = None
    score: float = 0.0
    scores: Dict[str, float] = field(default_factory=dict)
    reason: str = ""
    is_repeat: bool = False
    ts: float = field(default_factory=time.time)


class SmartPicker:
    """Выбор лучшего изображения с экрана по запросу пользователя.

    Args:
        analyzer: ScreenAnalyzer.
        memory: PreferenceMemory.
        persona: персонаж (dict/строка/карточка) для persona_score.
        score_weights: веса {query, preference, persona}.
        diversity_penalty: штраф за повтор региона.
        crop_padding_px: рамка вокруг crop.
        exclude_bboxes: список bbox прошлых выборов (для anti-repeat).
    """

    def __init__(
        self,
        analyzer: Any,
        memory: Optional[PreferenceMemory] = None,
        persona: Any = None,
        score_weights: Optional[Dict[str, float]] = None,
        diversity_penalty: float = 0.3,
        crop_padding_px: int = 4,
        exclude_bboxes: Optional[List[Tuple[int, int, int, int]]] = None,
    ):
        self.analyzer = analyzer
        self.memory = memory or PreferenceMemory()
        self.persona = persona
        self.persona_style = _persona_style(persona)
        self.weights = {
            "query": 0.40,
            "preference": 0.35,
            "persona": 0.25,
        }
        if score_weights:
            self.weights.update(score_weights)
        self.diversity_penalty = diversity_penalty
        self.crop_padding = crop_padding_px
        self.exclude_bboxes: List[Tuple[int, int, int, int]] = list(exclude_bboxes or [])
        self.last_picks: List[PickResult] = []

    # ── Основной выбор ─────────────────────────────────────────────────

    def pick(self, user_request: str, screenshot: Optional[Path] = None) -> PickResult:
        """Выбрать один регион.

        Args:
            user_request: запрос пользователя.
            screenshot: путь к скриншоту (если None — снять экран).

        Returns:
            PickResult с crop_path (если удалось вырезать).
        """
        shot = screenshot
        if shot is None:
            shot = self.analyzer.capture(monitor_index=1)

        regions = self.analyzer.find_all_images(shot) if shot else []
        if not regions:
            return PickResult(region=None, reason="no_regions", scores={})

        scored = []
        for r in regions:
            q = self._query_score(user_request, r)
            p = self.memory.score(r)
            pe = self._persona_score(r)
            base = (
                self.weights["query"] * q
                + self.weights["preference"] * p
                + self.weights["persona"] * pe
            )
            rep = self._is_repeat(r.bbox)
            if rep:
                base -= self.diversity_penalty
            scored.append((base, r, q, p, pe, rep))

        scored.sort(key=lambda x: x[0], reverse=True)
        base, region, q, p, pe, rep = scored[0]

        crop_path = None
        try:
            crop_path = self.analyzer.crop(
                region, padding=self.crop_padding, source_screenshot=shot
            )
        except Exception as e:
            _log.warning("pick: crop %s", e)

        result = PickResult(
            region=region,
            crop_path=crop_path,
            score=base,
            scores={
                "query": round(q, 3),
                "preference": round(p, 3),
                "persona": round(pe, 3),
                "total": round(base, 3),
            },
            reason=self._make_reason(region, q, p, pe, rep),
            is_repeat=rep,
        )
        self.last_picks.append(result)

        rejected = [r for _, r, _, _, _, _ in scored[1:10]]
        try:
            self.memory.record_choice(
                chosen=region,
                rejected=rejected,
                request_text=user_request,
            )
        except Exception as e:
            _log.warning("pick: record_choice %s", e)

        _log.info(
            "pick: score=%.3f %s top=%s",
            base, result.reason, self.memory.get_top_tags(3),
        )
        return result

    def pick_many(
        self,
        user_request: str,
        n: int = 3,
        screenshot: Optional[Path] = None,
    ) -> List[PickResult]:
        """Top-N с diversity — не три одинаковых.

        Исключает регионы, пересекающиеся с уже выбранными (>50% IoU).
        """
        shot = screenshot
        if shot is None:
            shot = self.analyzer.capture(monitor_index=1)
        regions = self.analyzer.find_all_images(shot) if shot else []
        if not regions:
            return []

        picked: List[PickResult] = []
        remaining = list(regions)

        while len(picked) < n and remaining:
            scored = []
            for r in remaining:
                q = self._query_score(user_request, r)
                p = self.memory.score(r)
                pe = self._persona_score(r)
                base = (
                    self.weights["query"] * q
                    + self.weights["preference"] * p
                    + self.weights["persona"] * pe
                )
                for prev in picked:
                    if self._intersection(r.bbox, prev.region.bbox) > 0.5:
                        base -= self.diversity_penalty
                if self._is_repeat(r.bbox):
                    base -= self.diversity_penalty
                scored.append((base, r, q, p, pe))
            scored.sort(key=lambda x: x[0], reverse=True)
            if not scored:
                break
            base, region, q, p, pe = scored[0]
            remaining.remove(region)

            crop_path = None
            try:
                crop_path = self.analyzer.crop(
                    region, padding=self.crop_padding, source_screenshot=shot
                )
            except Exception:
                pass

            res = PickResult(
                region=region,
                crop_path=crop_path,
                score=base,
                scores={
                    "query": round(q, 3),
                    "preference": round(p, 3),
                    "persona": round(pe, 3),
                    "total": round(base, 3),
                },
                reason=self._make_reason(region, q, p, pe, False),
            )
            picked.append(res)

        for res in picked:
            self.last_picks.append(res)
        return picked

    # ── Скоринг ────────────────────────────────────────────────────────

    def _query_score(self, request: str, region: Any) -> float:
        """Соответствие региона текстовому запросу (0..1).

        Учитывает теги из текста (dark/light/colorful/anime...) и
        совпадение слов запроса с фичами региона.
        """
        low = (request or "").lower()
        if not low.strip():
            return 0.5

        tags = tags_from_text(low)
        feat = getattr(region, "features", {}) or {}
        score = 0.5
        cnt = 0

        brightness = feat.get("brightness", 128)
        sat = feat.get("saturation", 0)

        if "dark" in tags:
            score += 0.3 if brightness < 70 else 0.0
            cnt += 1
        if "light" in tags:
            score += 0.3 if brightness > 190 else 0.0
            cnt += 1
        if "colorful" in tags:
            score += 0.3 if sat > 50 else 0.0
            cnt += 1

        content = getattr(region, "content_type", "") or feat.get("content_type", "")
        for tag in ("photo", "art", "anime", "nature", "space"):
            if tag in tags:
                if tag == "photo" and content in ("photo", "screenshot"):
                    score += 0.25
                elif tag in ("art", "anime") and content in ("drawing", "photo"):
                    score += 0.25
                cnt += 1

        if "ярк" in low or "светл" in low:
            score += 0.15 if brightness > 150 else 0.0
        if "тём" in low:
            score += 0.15 if brightness < 110 else 0.0

        if cnt == 0:
            return max(0.0, min(1.0, score))
        return max(0.0, min(1.0, score / (0.5 + 0.5 * cnt / 2)))

    def _persona_score(self, region: Any) -> float:
        """0..1 — насколько регион соответствует стилю персонажа."""
        if not self.persona_style:
            return 0.5

        feat = getattr(region, "features", {}) or {}
        brightness = feat.get("brightness", 128)
        sat = feat.get("saturation", 0)

        style_tag = None
        for key, words in _PERSONA_KEYWORDS.items():
            if any(w in self.persona_style.lower() for w in words):
                style_tag = key
                break
        if style_tag is None:
            return 0.5

        if style_tag == "dark":
            return 1.0 if brightness < 80 else 0.0 if brightness > 180 else 0.5
        if style_tag in ("cheerful", "playful"):
            return 1.0 if brightness > 150 and sat > 30 else 0.4
        if style_tag == "tender":
            return 1.0 if 120 <= brightness <= 230 and sat < 60 else 0.4
        if style_tag == "mystic":
            return 1.0 if brightness < 110 or sat < 25 else 0.5
        if style_tag == "artistic":
            return 0.7 if sat > 40 else 0.4
        return 0.5

    # ── Anti-repeat ────────────────────────────────────────────────────

    @staticmethod
    def _intersection(
        a: Tuple[int, int, int, int],
        b: Tuple[int, int, int, int],
    ) -> float:
        """IoU двух bbox (x,y,w,h). 0..1."""
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
        iy = max(0, min(ay + ah, by + bh) - max(ay, by))
        inter = ix * iy
        union = aw * ah + bw * bh - inter
        if union <= 0:
            return 0.0
        return inter / union

    def _is_repeat(self, bbox: Tuple[int, int, int, int]) -> bool:
        """True, если bbox пересекается с прошлым выбором > 50%."""
        for pb in self.exclude_bboxes:
            if self._intersection(bbox, pb) > 0.5:
                return True
        for res in self.last_picks:
            if res.region is not None and self._intersection(bbox, res.region.bbox) > 0.5:
                return True
        return False

    @staticmethod
    def _make_reason(
        region: Any, q: float, p: float, pe: float, is_repeat: bool
    ) -> str:
        """Однострочное объяснение выбора."""
        feat = getattr(region, "features", {}) or {}
        bits = []
        if q >= 0.6:
            bits.append("подходит под запрос")
        if p >= 0.6:
            bits.append("похоже на то, что вы любили")
        if pe >= 0.6:
            bits.append("в моём стиле")
        if is_repeat:
            bits.append("повтор — штраф")
        return ", ".join(bits) if bits else "средний вариант"