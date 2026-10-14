# -*- coding: utf-8 -*-
"""PromptBuilder — единая система сборки промтов для генерации изображений.

Слои (порядок сборки):
  [качество]  [стиль]  [персонаж]  [сюжет]  [композиция]  [свет]  [детали]  [настроение]

Каждый слой опционален — включается по правилам.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


_log = logging.getLogger("prompt_builder")


# ── Константы ──────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[1]

_STYLES_PATH = _ROOT / "prompts" / "styles.yaml"
_QUALITY_PATH = _ROOT / "prompts" / "quality.yaml"
_LIBRARY_DIR = _ROOT / "prompts" / "library"

_DEFAULT_STYLE_PRESET = "anime"
_DEFAULT_QUALITY = "standard"

_QUALITY_LAYER = {
    "draft": "lores, draft quality, quick sketch",
    "standard": "masterpiece, best quality, ultra-detailed, 8k",
    "high": "masterpiece, best quality, ultra-detailed, 8k, highres, sharp focus",
    "ultra": "masterpiece, best quality, ultra-detailed, 8k, highres, sharp focus, professional, award-winning",
}

_NEGATIVE_BASE = (
    "lowres, bad anatomy, bad hands, text, watermark, signature, "
    "blurry, jpeg artifacts, deformed, disfigured, extra limbs"
)

_STYLE_NEGATIVE_MAP: Dict[str, str] = {
    "anime": "3d, realistic, photo, photorealistic",
    "photorealistic": "cartoon, anime, painting, illustration, watercolor",
    "watercolor": "photo, 3d, sharp edges, digital, vector",
    "oil_painting": "digital, photo, flat, vector",
    "concept_art": "lowres, amateur, sketch, photo",
    "cyberpunk": "daylight, rural, historical, vintage, medieval",
}
# ── PromptBundle ───────────────────────────────────────────────────────


@dataclass
class PromptBundle:
    """Полностью собранный промт со всеми параметрами генерации.

    Поля:
        positive       — положительный промт (готовая строка).
        negative       — негативный промт.
        weights        — веса слоёв {"subject": 1.2, "style": 0.9, ...}.
        style_preset   — имя стилевого пресета.
        quality_preset — имя пресета качества.
        seed           — сид генерации (None = случайный).
        steps          — количество шагов.
        cfg_scale      — CFG scale.
        resolution     — (ширина, высота).
        reference_image  — путь к референсу (img2img).
        reference_strength — сила референса (0.0-1.0).
        source_screenshot — путь к скриншоту (init_image).
        source_strength   — сила скриншота (0.0-1.0).
        metadata       — произвольные метаданные.
    """
    positive: str = ""
    negative: str = ""
    weights: Dict[str, float] = field(default_factory=lambda: {
        "subject": 1.2, "style": 0.9, "quality": 0.7, "negative": 1.0,
    })
    style_preset: str = _DEFAULT_STYLE_PRESET
    quality_preset: str = _DEFAULT_QUALITY
    seed: Optional[int] = None
    steps: int = 25
    cfg_scale: float = 7.0
    resolution: Tuple[int, int] = (768, 768)
    reference_image: Optional[Path] = None
    reference_strength: float = 0.0
    source_screenshot: Optional[Path] = None
    source_strength: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d.get("reference_image"):
            d["reference_image"] = str(d["reference_image"])
        if d.get("source_screenshot"):
            d["source_screenshot"] = str(d["source_screenshot"])
        if d.get("metadata") is None:
            d["metadata"] = {}
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PromptBundle":
        d = dict(d)
        for key in ("reference_image", "source_screenshot"):
            val = d.get(key)
            if val and not isinstance(val, Path):
                d[key] = Path(val)
        return cls(**d)

    def clone(self) -> "PromptBundle":
        """Полная копия."""
        return PromptBundle(
            positive=self.positive,
            negative=self.negative,
            weights=dict(self.weights),
            style_preset=self.style_preset,
            quality_preset=self.quality_preset,
            seed=self.seed,
            steps=self.steps,
            cfg_scale=self.cfg_scale,
            resolution=self.resolution,
            reference_image=self.reference_image,
            reference_strength=self.reference_strength,
            source_screenshot=self.source_screenshot,
            source_strength=self.source_strength,
            metadata=dict(self.metadata),
        )# ── YAML / card helpers ────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        _log.warning("YAML not found: %s", path)
        return {}
    with open(str(path), "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def read_card(character_id: str) -> str:
    """Читает карточку персонажа через character_catalog, если доступен."""
    try:
        from character_catalog import read_character_card
        return (read_character_card(str(character_id)) or "").strip()
    except Exception:
        return ""


# ── PromptBuilder ─────────────────────────────────────────────────────


class PromptBuilder:
    """Собирает финальный промт из слоёв, пресетов и контекста.

    Args:
        styles_path: путь к styles.yaml (по умолч. data/prompts/styles.yaml).
        quality_path: путь к quality.yaml.
        library_dir: директория с библиотечными шаблонами.
    """

    def __init__(
        self,
        styles_path: Optional[Path] = None,
        quality_path: Optional[Path] = None,
        library_dir: Optional[Path] = None,
    ):
        self._styles_path = Path(styles_path) if styles_path else _STYLES_PATH
        self._quality_path = Path(quality_path) if quality_path else _QUALITY_PATH
        self._library_dir = Path(library_dir) if library_dir else _LIBRARY_DIR
        self._styles: dict = {}
        self._quality_presets: dict = {}
        self._library: Dict[str, dict] = {}
        self._reload()

    # ── Публичный API ─────────────────────────────────────────────────

    def build(
        self,
        user_request: str,
        persona: Optional[str] = None,
        context: Optional[Any] = None,
        intent: str = "imggen",
        sources: Optional[Dict[str, Any]] = None,
        style_preset: Optional[str] = None,
        quality_preset: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> PromptBundle:
        """Собрать финальный промт.

        Args:
            user_request: запрос пользователя («нарисуй кота»).
            persona: имя активного персонажа (character_id) или None.
            context: ConversationContext (необязательно).
            intent: имгген-интент (imggen / imggen_edit / …).
            sources: словарь ссылок {reference, uploaded, screenshot}.
            style_preset: принудительный стиль.
            quality_preset: принудительное качество.
            overrides: переопределения отдельных параметров.

        Returns:
            PromptBundle с готовым промтом.
        """
        sources = sources or {}
        overrides = overrides or {}

        # 1. Определить пресеты
        style = self._resolve_style(persona, style_preset, context)
        quality = self._resolve_quality(quality_preset, context)

        # 2. Собрать слои
        pos_parts, neg_parts = self._collect_layers(
            user_request, persona, style, quality, intent, sources, context,
        )

        # 3. Применить пресеты
        positive = self._compose_positive(pos_parts, overrides.get("weights"))
        negative = self._compose_negative(neg_parts, persona, style)

        # 4. Параметры из пресетов
        style_cfg = self._styles.get(style, {})
        quality_cfg = self._quality_presets.get(quality, {})

        steps = overrides.get("steps") or quality_cfg.get("steps") or style_cfg.get("steps") or 25
        cfg_scale = overrides.get("cfg_scale") or quality_cfg.get("cfg_scale") or style_cfg.get("cfg_scale") or 7.0
        res = overrides.get("resolution") or quality_cfg.get("resolution") or [768, 768]

        # 5. Референс / скриншот
        ref_path = self._resolve_ref(sources)
        ref_strength = overrides.get("reference_strength") or 0.0
        scr_path = self._resolve_screenshot(sources)
        scr_strength = overrides.get("source_strength") or 0.0

        # 6. Seed
        seed = overrides.get("seed") or None

        # 7. Собрать bundle
        bundle = PromptBundle(
            positive=positive,
            negative=negative,
            weights=dict(self._default_weights(pos_parts)),
            style_preset=style,
            quality_preset=quality,
            seed=seed,
            steps=int(steps),
            cfg_scale=float(cfg_scale),
            resolution=(int(res[0]), int(res[1])),
            reference_image=ref_path,
            reference_strength=float(ref_strength),
            source_screenshot=scr_path,
            source_strength=float(scr_strength),
            metadata={
                "intent": intent,
                "user_request": user_request,
                "persona": persona or "",
                "timestamp": time.time(),
            },
        )

        _log.debug("PromptBundle built: style=%s quality=%s tokens=%d",
                    style, quality, len(positive) + len(negative))

        return bundle


    # ── Сборка слоёв ──────────────────────────────────────────────────

    def _collect_layers(
        self,
        user_request: str,
        persona: Optional[str],
        style: str,
        quality: str,
        intent: str,
        sources: Dict[str, Any],
        context: Optional[Any],
    ):
        pos = []
        neg = []
        quality_text = _QUALITY_LAYER.get(quality, _QUALITY_LAYER[_DEFAULT_QUALITY])
        pos.append((quality_text, 0.7))
        style_cfg = self._styles.get(style, {})
        style_prefix = style_cfg.get("positive_prefix", "")
        if style_prefix:
            pos.append((style_prefix, 0.9))
        persona_text = self._persona_prompt(persona)
        if persona_text:
            pos.append((persona_text, 1.0))
        cleaned_request = self._clean_request(user_request)
        if cleaned_request:
            pos.append((cleaned_request, 1.3))
        comp = self._extract_compound(user_request)
        pos.extend(comp)
        scr_info = sources.get("screenshot_analysis", {})
        ref_info = sources.get("reference_attrs", {})
        pos.extend(self._sourced_phrases(scr_info, ref_info))
        ctx_text = self._context_prompt(context, persona, style)
        if ctx_text:
            pos.append((ctx_text, 0.5))
        neg.append(_NEGATIVE_BASE)
        style_neg = _STYLE_NEGATIVE_MAP.get(style)
        if style_neg:
            neg.append(style_neg)
        card_neg = self._card_negative(persona)
        if card_neg:
            neg.append(card_neg)
        return pos, neg

    def _compose_positive(self, parts, weights_override=None):
        return self._apply_weights(parts, weights_override)

    def _apply_weights(self, parts, weights_override=None):
        segments = []
        for text, weight in parts:
            text = text.strip()
            if not text:
                continue
            w = weights_override.get(text, weight) if weights_override else weight
            if w is not None and abs(w - 1.0) > 0.01:
                segments.append(f"({text}:{w:.1f})" if w >= 0 else text)
            else:
                segments.append(text)
        return ", ".join(segments)

    def _compose_negative(self, parts, persona=None, style=""):
        seen = set()
        unique = []
        for part in parts:
            for token in re.split(r",\s*", part):
                token = token.strip().lower()
                if token and token not in seen:
                    seen.add(token)
                    unique.append(token)
        return ", ".join(unique)

    def _apply_style_preset(self, text, preset):
        return text
# ── Извлечение атрибутов из запроса ───────────────────────────────

    _COMP_KEYWORDS = {
        "close-up": 1.0, "closeup": 1.0, "full body": 1.0,
        "wide shot": 0.8, "portrait": 1.0, "headshot": 1.0,
        "крупный план": 1.0, "в полный рост": 1.0, "общий план": 0.8,
        "портрет": 1.0,
    }
    _LIGHT_KEYWORDS = {
        "soft light": 0.8, "golden hour": 1.0, "dramatic lighting": 1.0,
        "cinematic lighting": 1.0, "natural lighting": 0.8, "backlight": 0.9,
        "мягкий свет": 0.8, "золотой час": 1.0, "контровой свет": 0.9,
        "драматичный свет": 1.0,
    }
    _MOOD_KEYWORDS = {
        "playful": 0.7, "serene": 0.7, "melancholic": 0.8, "epic": 1.0,
        "romantic": 0.8, "dark": 0.9, "mysterious": 0.8, "cheerful": 0.7,
        "игривый": 0.7, "спокойный": 0.7, "меланхоличный": 0.8,
        "эпичный": 1.0, "романтичный": 0.8, "мрачный": 0.9,
    }

    def _extract_compound(self, text: str) -> List[Tuple[str, float]]:
        parts: List[Tuple[str, float]] = []
        lower = text.lower()
        for kw, w in self._COMP_KEYWORDS.items():
            if kw in lower:
                parts.append((kw, w))
        for kw, w in self._LIGHT_KEYWORDS.items():
            if kw in lower:
                parts.append((kw, w))
        for kw, w in self._MOOD_KEYWORDS.items():
            if kw in lower:
                parts.append((kw, w))
        return parts

    def _persona_prompt(self, persona: Optional[str]) -> str:
        if not persona:
            return ""
        card_text = read_card(persona)
        if not card_text:
            return ""
        attrs: List[str] = []
        try:
            card_json = json.loads(card_text)
            gen = card_json.get("generation") or card_json.get("image_generation") or {}
            if gen.get("attributes"):
                if isinstance(gen["attributes"], dict):
                    for key, val in gen["attributes"].items():
                        if isinstance(val, str) and val.strip():
                            attrs.append(f"{key}: {val.strip()}")
        except Exception:
            pass
        m = re.search(
            r"(?:visual|appearance|внешность|описание внешности)[:\s]*?(.+?)(?:\n\n|\Z)",
            card_text, re.I | re.S,
        )
        if m:
            desc = m.group(1).strip()[:300]
            if desc:
                attrs.append(desc)
        return "; ".join(attrs[:5]) if attrs else ""

    def _card_negative(self, persona: Optional[str]) -> str:
        if not persona:
            return ""
        card_text = read_card(persona)
        if not card_text:
            return ""
        try:
            card_json = json.loads(card_text)
            gen = card_json.get("generation") or card_json.get("image_generation") or {}
            neg = gen.get("negative_prompt") or ""
            forbidden = gen.get("forbidden_elements") or []
            parts = [neg] if neg else []
            if forbidden:
                parts.extend(f"no {fe}" for fe in forbidden)
            return ", ".join(parts) if parts else ""
        except Exception:
            return ""

    def _context_prompt(self, context, persona=None, style=""):
        if context is None:
            return ""
        try:
            topic = context.get_topic() or ""
            entities = context.get_entities() or {}
            subject = entities.get("last_image_subject") or entities.get("subject") or ""
            prev_style = entities.get("last_style") or ""
            if prev_style and prev_style != style:
                return f"({prev_style} style)"
            if subject and topic:
                return subject
            if topic:
                return topic
        except Exception:
            pass
        return ""

    def _resolve_ref(self, sources: Dict[str, Any]) -> Optional[Path]:
        ref = sources.get("reference")
        return Path(ref) if ref and not isinstance(ref, Path) else ref

    def _resolve_screenshot(self, sources: Dict[str, Any]) -> Optional[Path]:
        scr = sources.get("screenshot")
        return Path(scr) if scr and not isinstance(scr, Path) else scr

    def _sourced_phrases(self, screen_analysis, reference_attrs):
        parts = []
        if reference_attrs.get("hair_color"):
            parts.append((f"{reference_attrs['hair_color']} hair", 1.0))
        if reference_attrs.get("eye_color"):
            parts.append((f"{reference_attrs['eye_color']} eyes", 1.0))
        if reference_attrs.get("style"):
            parts.append((reference_attrs["style"], 0.8))
        if screen_analysis.get("dominant_colors"):
            colors = ", ".join(screen_analysis["dominant_colors"][:3])
            parts.append((f"color palette: {colors}", 0.6))
        if screen_analysis.get("mood"):
            parts.append((f"mood: {screen_analysis['mood']}", 0.7))
        return parts

    def _clean_request(self, text: str) -> str:
        text = re.sub(
            r"\b(нарисуй|нарисовать|сгенерируй|сгенерировать|создай|сделай|изобрази"
            r"|draw|generate|create|покажи|найди)\b",
            "", text, flags=re.I).strip()
        return re.sub(r"\s+", " ", text).strip()

    def _default_weights(self, parts):
        return {p[0]: p[1] for p in parts}

    @staticmethod
    def compose_with_reference(user_request, reference_attrs):
        pieces = []
        if reference_attrs.get("hair_color"):
            pieces.append(f"{reference_attrs['hair_color']} hair")
        if reference_attrs.get("eye_color"):
            pieces.append(f"{reference_attrs['eye_color']} eyes")
        if reference_attrs.get("style"):
            pieces.append(reference_attrs["style"])
        pieces.append(user_request)
        return ", ".join(pieces)

    @staticmethod
    def compose_with_screenshot(user_request, screen_analysis):
        parts = [user_request]
        if screen_analysis.get("dominant_colors"):
            parts.append(f"color palette: {', '.join(screen_analysis['dominant_colors'][:3])}")
        if screen_analysis.get("mood"):
            parts.append(f"mood: {screen_analysis['mood']}")
        return ", ".join(parts)

    def _resolve_style(self, persona, override, context):
        if override:
            return override
        card_style = self._card_default_style(persona)
        if card_style:
            return card_style
        if context is not None:
            try:
                ctx_style = context.get_entities().get("last_style", "")
                if ctx_style:
                    return ctx_style
            except Exception:
                pass
        return _DEFAULT_STYLE_PRESET

    def _resolve_quality(self, override, context):
        if override:
            return override
        if context is not None:
            try:
                ctx_quality = context.get_entities().get("last_quality", "")
                if ctx_quality:
                    return ctx_quality
            except Exception:
                pass
        return _DEFAULT_QUALITY

    def _card_default_style(self, persona):
        if not persona:
            return ""
        card_text = read_card(persona)
        if not card_text:
            return ""
        try:
            card_json = json.loads(card_text)
            gen = card_json.get("generation") or {}
            return gen.get("default_style") or ""
        except Exception:
            return ""

    def load_template(self, template_name):
        if template_name in self._library:
            return self._library[template_name]
        path = self._library_dir / f"{template_name}.yaml"
        if not path.is_file():
            _log.warning("Template not found: %s", path)
            return None
        data = _load_yaml(path)
        tpl = (data or {}).get(template_name) or data
        if tpl:
            self._library[template_name] = tpl
        return tpl

    def fill_template(self, template_name, slots):
        tpl = self.load_template(template_name)
        if not tpl:
            return None
        template = tpl.get("template", "")
        defaults = tpl.get("defaults", {})
        filled = defaults.copy()
        # Общие слоты: стиль/качество подставляем из пресетов
        if "style" not in filled:
            style_cfg = self._styles.get(_DEFAULT_STYLE_PRESET, {})
            filled["style"] = style_cfg.get("positive_prefix", _DEFAULT_STYLE_PRESET)
        if "quality" not in filled:
            filled["quality"] = _QUALITY_LAYER.get(_DEFAULT_QUALITY, "masterpiece")
        filled.update(slots or {})
        # Пропущенные слоты → пустая строка (опциональные)
        placeholders = re.findall(r"\{([^{}]+)\}", template)
        for ph in placeholders:
            filled.setdefault(ph, "")
        try:
            result = template.format(**filled)
        except KeyError as e:
            _log.error("Missing slot %s in template %s", e, template_name)
            return None
        # Схлопнуть пустые слоты: "a girl, , soft light" → "a girl, soft light"
        while ", ," in result:
            result = result.replace(", ,", ",")
        return result.strip(", ").strip()

    def _reload(self):
        self._styles = _load_yaml(self._styles_path)
        self._quality_presets = _load_yaml(self._quality_path)
        self._library.clear()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    pb = PromptBuilder()
    b = pb.build("нарисуй рыжего кота в солнечном свете, крупный план")
    print("=== POSITIVE ===")
    print(b.positive)
    print("\n=== NEGATIVE ===")
    print(b.negative)
    print(f"\nStyle: {b.style_preset}, Quality: {b.quality_preset}, Steps: {b.steps}, CFG: {b.cfg_scale}")
    print()
    ref = pb.build(
        "дорисуй фон",
        sources={
            "reference_attrs": {"hair_color": "red", "eye_color": "green"},
            "screenshot_analysis": {"dominant_colors": ["#FF6633", "#332211"], "mood": "sunny"},
        },
    )
    print("=== С референсом ===")
    print(ref.positive[:220])
