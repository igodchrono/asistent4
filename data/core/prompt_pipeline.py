# -*- coding: utf-8 -*-
"""PromptPipeline — оркестратор генерации промтов.

Порядок (п. 10.17):
  PromptBuilder.build() → PromptEnhancer.enhance() → генератор
  → метрики (GenerationMetrics) → RefineLoop при фидбеке.

Формат ответа (п. 10.21):
  🎨 Готово. Стиль: anime, качество: high. Seed: 12345678
  <изображение>
  Метрики: sharpness 0.82, aesthetic 0.74
"""
from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .prompt_builder import PromptBuilder, PromptBundle
    from .prompt_enhancer import PromptEnhancer
    from .refine_loop import RefineLoop, RefineResult
    from .prompt_ab import PromptAB, ABTestResult
    from .generation_history import GenerationHistory, get_history
    from .generation_metrics import compute_metrics
except ImportError:
    from prompt_builder import PromptBuilder, PromptBundle
    from prompt_enhancer import PromptEnhancer
    from refine_loop import RefineLoop, RefineResult
    from prompt_ab import PromptAB, ABTestResult
    from generation_history import GenerationHistory, get_history
    from generation_metrics import compute_metrics


_log = logging.getLogger("prompt_pipeline")

_QUALITY_TRIGGERS = {
    "быстро": "draft",
    "быстрый": "draft",
    "набросок": "draft",
    "высоком качестве": "high",
    "высокое качество": "high",
    "максимально высок": "ultra",
    "ультра": "ultra",
    "супер качество": "ultra",
}

_STYLE_TRIGGERS = {
    "аниме": "anime",
    "япон": "anime",
    "фотореалистичн": "photorealistic",
    "реалистичн": "photorealistic",
    "акварель": "watercolor",
    "масл": "oil_painting",
    "концепт": "concept_art",
    "киберпанк": "cyberpunk",
}


class PromptPipeline:
    """Полный конвейер промтов.

    Args:
        llm: LLM-менеджер для PromptEnhancer (опционально).
        prompt_enhancer_enabled: включать ли автоусиление.
        refine_enabled: включать ли refine loop.
        ab_enabled: включать ли A/B-тестирование.
        ab_sample_rate: доля генераций с A/B.
    """

    def __init__(
        self,
        llm: Any = None,
        prompt_enhancer_enabled: bool = True,
        refine_enabled: bool = True,
        ab_enabled: bool = False,
        ab_sample_rate: float = 0.1,
    ):
        self.builder = PromptBuilder()
        self.enhancer = PromptEnhancer(
            llm_manager=llm, enabled=prompt_enhancer_enabled)
        self.refine = RefineLoop(max_iterations=3, save_successful=True)
        self.metrics_enabled = True
        self.history = get_history()
        self.ab = PromptAB(enabled=ab_enabled, sample_rate=ab_sample_rate)

    def build_bundle(
        self,
        user_request: str,
        persona: Optional[str] = None,
        context: Any = None,
        intent: str = "imggen",
        sources: Optional[Dict[str, Any]] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> PromptBundle:
        """Собрать bundle (промт + параметры)."""
        style, quality = self._detect_style_quality(user_request, persona)
        overrides = overrides or {}

        bundle = self.builder.build(
            user_request=user_request,
            persona=persona,
            context=context,
            intent=intent,
            sources=sources,
            style_preset=overrides.get("style") or style or None,
            quality_preset=overrides.get("quality") or quality or None,
            overrides=overrides,
        )

        # Автоусиление
        if self.enhancer.enabled and self.enhancer._llm:
            user_req = bundle.metadata.get("user_request", user_request)
            enhanced = self.enhancer.enhance(user_req, persona=persona, context=context)
            if enhanced and len(enhanced) > 4:
                bundle.positive = enhanced
                _log.debug("prompt enhanced: %d → %d chars",
                            len(bundle.positive), len(enhanced))

        if bundle.seed is None:
            bundle.seed = overrides.get("seed") or random.randint(0, 2**31 - 1)

        return bundle

    def detect_style_quality(self, user_request: str, persona: Optional[str] = None):
        return self._detect_style_quality(user_request, persona)

    def _detect_style_quality(
        self, text: str, persona: Optional[str] = None
    ) -> Tuple[str, str]:
        low = (text or "").lower()
        style = ""
        quality = ""
        for trigger, preset in _STYLE_TRIGGERS.items():
            if trigger in low:
                style = preset
                break
        for trigger, preset in _QUALITY_TRIGGERS.items():
            if trigger in low:
                quality = preset
                break
        return style, quality

    def record_generation(
        self,
        bundle: PromptBundle,
        image_path: Optional[str] = None,
        feedback: str = "",
    ) -> Dict[str, Any]:
        metrics = None
        if self.metrics_enabled and image_path and Path(image_path).is_file():
            try:
                metrics = compute_metrics(Path(image_path), bundle=bundle)
            except Exception as e:
                _log.warning("metrics: %s", e)
        return self.history.record(
            prompt=bundle.positive,
            negative=bundle.negative,
            seed=bundle.seed,
            bundle=bundle,
            image_path=image_path,
            metrics=metrics,
            feedback=feedback,
            style=bundle.style_preset,
            quality=bundle.quality_preset,
        )
        return entry

    def refine_bundle(self, user_feedback: str, current_bundle: PromptBundle) -> PromptBundle:
        result = self.refine.refine(user_feedback, current_bundle)
        bundle = current_bundle.clone()
        bundle.positive = result.bundle_positive
        bundle.negative = result.bundle_negative
        bundle.steps = result.steps
        bundle.cfg_scale = result.cfg_scale
        bundle.seed = (bundle.seed + 1) if bundle.seed is not None else random.randint(0, 2**31 - 1)
        return bundle

    def is_refine_request(self, text: str) -> bool:
        return RefineLoop.is_refine_trigger(text)

    def maybe_run_ab(self, bundle: PromptBundle) -> Optional[ABTestResult]:
        if not self.ab.should_test():
            return None
        return self.ab.generate_ab(bundle)

    @staticmethod
    def format_success(bundle: PromptBundle, metrics: Optional[Dict[str, Any]] = None) -> str:
        lines = [
            f"🎨 Готово. Стиль: {bundle.style_preset}, качество: {bundle.quality_preset}."
        ]
        if bundle.seed is not None:
            lines.append(f"Seed: {bundle.seed}")
        if metrics and metrics.get("overall") is not None:
            lines.append(
                f"Метрики: overall {metrics['overall']:.2f}, "
                f"sharpness {metrics.get('sharpness', 0):.2f}"
            )
        return "\n".join(lines)

    @staticmethod
    def format_refine(user_feedback: str, bundle: PromptBundle) -> str:
        lines = [
            f"🎨 Перегенерировала с учётом «{user_feedback.rstrip('!. ')}»."
        ]
        if bundle.seed is not None:
            lines.append(f"Seed: {bundle.seed}")
        return "\n".join(lines)


_shared_pipeline: Optional[PromptPipeline] = None


def get_pipeline(llm: Any = None) -> PromptPipeline:
    global _shared_pipeline
    if _shared_pipeline is None:
        _shared_pipeline = PromptPipeline(llm=llm)
    elif llm is not None:
        _shared_pipeline.enhancer._llm = llm
    return _shared_pipeline


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    pipe = PromptPipeline(prompt_enhancer_enabled=False)

    bundle = pipe.build_bundle("нарисуй кота в высоком качестве", persona=None)
    print(f"positive: {bundle.positive[:100]}…")
    print(f"style={bundle.style_preset} quality={bundle.quality_preset} seed={bundle.seed}")
    assert bundle.quality_preset == "high", f"expected high, got {bundle.quality_preset}"

    style, quality = pipe.detect_style_quality("сделай аниме кота")
    assert style == "anime"
    assert pipe.is_refine_request("слишком темно") is True
    assert pipe.is_refine_request("привет") is False

    refined = pipe.refine_bundle("слишком темно, добавь деталей", bundle)
    print(f"\nrefined cfg={refined.cfg_scale} steps={refined.steps}")
    assert "bright" in refined.positive
    assert bundle.cfg_scale != refined.cfg_scale

    print("\nOK")