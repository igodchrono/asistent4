# -*- coding: utf-8 -*-
"""Промт-якоря: не даём новому промту потерять детали предыдущего.

Проблема: даже с референсом LLM может «упростить» промт
(«the same figure» вместо полного описания). Решение — извлечь из
предыдущего промта ключевые теги (персонаж, одежда, окружение, стиль,
свет) и принудительно вернуть их в новый промт / инструкцию правки.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


VOCAB_ANCHOR: Dict[str, List[str]] = {
    "character": [
        "fox girl", "fox ears", "fox features", "fluffy tail", "furry tail",
        "orange hair", "long red hair", "long flowing red hair", "red hair",
        "blonde", "white hair", "silver hair", "black hair", "brown hair",
        "blue eyes", "green eyes", "red eyes", "golden eyes", "purple eyes",
        "cute anime girl", "anime girl", "kitsune", "nekomimi", "cat ears",
        "rabbit ears", "freckles", "ponytail", "twin tails", "twintails",
        "long hair", "bangs", "cute girl", "beautiful girl",
        "лисичка", "рыжие волосы", "лисьи уши", "пушистый хвост",
        "голубые глаза", "аниме-девушка",
    ],
    "clothing": [
        "black lace", "lace", "lingerie", "dress", "mini dress", "kimono",
        "yukata", "skirt", "shirt", "blouse", "jacket", "coat", "hoodie",
        "sweater", "stockings", "thigh-highs", "gloves", "boots", "heels",
        "high heels", "collar", "ribbon", "bow", "necklace", "earrings",
        "crown", "hat", "cap", "veil", "apron", "silk", "velvet", "leather",
        "satin", "sheer", "transparent", "bikini", "swimsuit", "garter",
        "choker", "black lingerie", "naked", "topless",
        "белье", "бельё", "кружево", "платье",
    ],
    "setting": [
        "wooden chair", "chair", "throne", "sofa", "couch", "bed", "desk",
        "table", "window", "curtains", "mirror", "room", "bedroom", "bathroom",
        "studio", "street", "city", "alley", "garden", "park", "forest",
        "beach", "sea", "snow", "snowfield", "night", "rooftop", "balcony",
        "kitchen", "library", "office", "car", "interior", "stage",
        "dark room", "dim room", "neon sign", "lantern",
        "стул", "кресло", "комната", "спальня", "стена", "фон",
    ],
    "style": [
        "anime illustration", "anime", "manga", "illustration", "watercolor",
        "oil painting", "pixel art", "3d render", "photorealistic", "photo",
        "art nouveau", "ukiyo-e", "comic", "cartoon", "chibi", "semi-realistic",
        "vintage", "concept art", "digital art", "cel shading", "painterly",
        "аниме", "иллюстрация",
    ],
    "lighting": [
        "soft light", "soft lighting", "dim light", "low light", "warm rim light",
        "rim light", "neon light", "neon glow", "moody lighting", "dramatic lighting",
        "golden hour", "candlelight", "backlight", "back lighting", "studio lighting",
        "volumetric light", "vignette", "glowing", "sunlight", "moonlight",
        "window light", "warm light", "cool light", "even light",
        "мягкий свет", "свет",
    ],
}


ANCHOR_CATEGORY_ORDER: List[str] = ["character", "clothing", "setting", "style", "lighting"]

ANCHOR_CATEGORY_LABELS: Dict[str, str] = {
    "character": "Персонаж",
    "clothing": "Одежда",
    "setting": "Окружение",
    "style": "Стиль",
    "lighting": "Свет",
}


def extract_anchors(prompt: str, extra_text: str = "",
                    vocab: Optional[Dict[str, List[str]]] = None) -> Dict[str, List[str]]:
    """
    Возвращает словарь {category: [matched terms]} — ключевые теги промта.

    :param prompt:     английский промт либо инструкция правки.
    :param extra_text: дополнительный текст поиска (например, карточка персонажа).
    :param vocab:      переопределённый словарь якорей (по умолчанию VOCAB_ANCHOR).
    """
    v = vocab or VOCAB_ANCHOR
    text = (" " + (prompt or "").lower() + "\n" + (extra_text or "").lower())
    out: Dict[str, List[str]] = {}
    for cat in ANCHOR_CATEGORY_ORDER:
        found: List[str] = []
        for term in v.get(cat) or []:
            t = term.lower()
            if t in text:
                found.append(term)
        seen = set()
        uniq: List[str] = []
        for x in found:
            if x.lower() not in seen:
                seen.add(x.lower())
                uniq.append(x)
        out[cat] = uniq
    return out


def anchors_any(anchors: Dict[str, List[str]]) -> bool:
    return bool(anchors and any(v for v in (anchors or {}).values()))


def anchors_text(anchors: Dict[str, List[str]]) -> str:
    """Человекочитаемый текст якорей для вставки в запрос к LLM."""
    if not anchors_any(anchors):
        return ""
    parts: List[str] = []
    for cat in ANCHOR_CATEGORY_ORDER:
        vals = anchors.get(cat) or []
        if vals:
            parts.append(f"{ANCHOR_CATEGORY_LABELS.get(cat, cat)}: {', '.join(vals)}")
    return "\n".join(parts)


def apply_anchors(new_prompt: str, anchors: Dict[str, List[str]],
                  max_extra: int = 400) -> str:
    """Добавляет недостающие якоря в новый промт.

    Вставляет только те теги, которых ещё нет в new_prompt. Если всё уже есть —
    возвращает промт без изменений.
    """
    if not anchors_any(anchors):
        return new_prompt
    prompt = (new_prompt or "").strip()
    low = prompt.lower()
    missing: List[str] = []
    for cat in ANCHOR_CATEGORY_ORDER:
        for term in anchors.get(cat) or []:
            if term.lower() not in low:
                missing.append(term)
    if not missing:
        return prompt
    suffix = ", ".join(missing)
    if len(suffix) > max_extra:
        suffix = suffix[:max_extra].rsplit(",", 1)[0]
    return (prompt + ", " + suffix) if prompt else suffix


def prompt_similarity(a: str, b: str) -> float:
    """Jaccard по словам — доля общих лексем."""
    def toks(s: str) -> set:
        return set(re.findall(r"[a-zа-яё0-9]+", (s or "").lower().replace("ё", "е")))
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def check_prompt_drift(new_prompt: str, previous: str,
                       min_length_ratio: float = 0.7,
                       min_similarity: float = 0.6) -> Tuple[bool, Dict[str, float]]:
    """Проверка полноты нового промта против предыдущего.

    Возвращает (drift_detected, stats). Подозрительно, если новый промт
    короче предыдущего более чем на 30%% либо почти не пересекается по словам.
    """
    stats: Dict[str, float] = {}
    np_, pp = (new_prompt or "").strip(), (previous or "").strip()
    if not pp or len(pp) < 40 or not np_:
        return False, stats
    ratio = len(np_) / len(pp)
    sim = prompt_similarity(np_, pp)
    stats = {
        "new_len": len(np_),
        "prev_len": len(pp),
        "length_ratio": round(ratio, 3),
        "similarity": round(sim, 3),
    }
    if ratio < min_length_ratio or sim < min_similarity:
        return True, stats
    return False, stats
