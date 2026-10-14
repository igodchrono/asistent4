# -*- coding: utf-8 -*-
"""PromptAB — A/B-сравнение промтов.

- Генерировать одну и ту же сцену двумя вариантами промта.
- Показывать оба в чате.
- Собирать 👍/👎.
- Обновлять веса слоёв в data/prompts/weights.json на основе фидбека.
"""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


_log = logging.getLogger("prompt_ab")

_WEIGHTS_PATH = Path(__file__).resolve().parents[1] / "prompts" / "weights.json"

_AB_LAYERS = ["quality", "style", "lighting", "composition", "details", "mood"]


@dataclass
class ABTestResult:
    prompt_a: str
    prompt_b: str
    layers_a: Dict[str, float]
    layers_b: Dict[str, float]
    image_a: Optional[str] = None
    image_b: Optional[str] = None
    winner: Optional[str] = None  # "a" | "b" | "tie"
    timestamp: float = field(default_factory=time.time)


class PromptAB:
    """A/B-сравнение промтов.

    Args:
        enabled: включено ли A/B.
        sample_rate: доля генераций с A/B (0.0-1.0).
        generator_fn: (prompt_a, prompt_b) → (image_a_path, image_b_path).
    """

    def __init__(
        self,
        enabled: bool = False,
        sample_rate: float = 0.1,
        generator_fn: Optional[Callable[[str, str], Any]] = None,
    ):
        self.enabled = enabled
        self.sample_rate = sample_rate
        self.generator_fn = generator_fn
        self._history: List[ABTestResult] = []

    def should_test(self) -> bool:
        if not self.enabled:
            return False
        return random.random() < self.sample_rate

    def generate_ab(
        self,
        base_bundle: Any,
        variant_fn: Optional[Callable[[str], str]] = None,
    ) -> ABTestResult:
        """Сгенерировать два варианта промта."""
        prompt_a = getattr(base_bundle, "positive", "")
        prompt_b = self._create_variant(prompt_a) if variant_fn is None else variant_fn(prompt_a)
        layers_a = self._extract_layers(base_bundle)
        layers_b = self._mutate_layers(layers_a)

        result = ABTestResult(
            prompt_a=prompt_a,
            prompt_b=prompt_b,
            layers_a=layers_a,
            layers_b=layers_b,
        )

        if self.generator_fn is not None:
            try:
                images = self.generator_fn(prompt_a, prompt_b)
                if isinstance(images, (tuple, list)) and len(images) >= 2:
                    result.image_a = str(images[0]) if images[0] else None
                    result.image_b = str(images[1]) if images[1] else None
            except Exception as e:
                _log.warning("AB generator error: %s", e)

        self._history.append(result)
        return result

    def record_feedback(self, result: ABTestResult, winner: str) -> None:
        """Записать фидбек: winner в ("a", "b", "tie")."""
        result.winner = winner
        self._update_weights(result)
        _log.debug("AB feedback: winner=%s", winner)

    def _create_variant(self, prompt: str) -> str:
        """Создать вариант: переставить части промта."""
        parts = [p.strip() for p in prompt.split(",")]
        if len(parts) > 3:
            mid = parts[1:-1]
            random.shuffle(mid)
            parts = [parts[0]] + mid + [parts[-1]]
        return ", ".join(parts)

    def _extract_layers(self, bundle: Any) -> Dict[str, float]:
        weights = getattr(bundle, "weights", {})
        return {layer: weights.get(layer, 1.0) for layer in _AB_LAYERS}

    def _mutate_layers(self, layers: Dict[str, float]) -> Dict[str, float]:
        mutated = dict(layers)
        layer = random.choice(_AB_LAYERS)
        current = mutated.get(layer, 1.0)
        delta = random.choice([-0.2, -0.1, 0.1, 0.2])
        mutated[layer] = max(0.5, min(1.5, current + delta))
        return mutated

    def _update_weights(self, result: ABTestResult) -> None:
        if result.winner == "tie":
            return
        better = result.layers_a if result.winner == "a" else result.layers_b
        worse = result.layers_b if result.winner == "a" else result.layers_a
        weights = self._load_weights()

        for layer in _AB_LAYERS:
            current = weights.get(layer, 1.0)
            b = better.get(layer, 1.0)
            w = worse.get(layer, 1.0)
            if b > w:
                weights[layer] = current * 1.05
            elif w > b:
                weights[layer] = current * 0.95
            weights[layer] = max(0.5, min(1.5, weights[layer]))

        self._save_weights(weights)
        _log.debug("weights updated: %s", weights)

    def _load_weights(self) -> Dict[str, float]:
        try:
            if _WEIGHTS_PATH.is_file():
                with open(str(_WEIGHTS_PATH), "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            _log.warning("load_weights: %s", e)
        return {layer: 1.0 for layer in _AB_LAYERS}

    def _save_weights(self, weights: Dict[str, float]) -> None:
        try:
            _WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(str(_WEIGHTS_PATH), "w", encoding="utf-8") as f:
                json.dump(weights, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _log.warning("save_weights: %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    ab = PromptAB(enabled=True, sample_rate=1.0)

    class MockBundle:
        positive = "masterpiece, anime style, a cat, soft light, detailed"
        weights = {
            "quality": 0.7, "style": 0.9, "lighting": 0.8,
            "composition": 1.0, "details": 0.8, "mood": 0.7,
        }

    result = ab.generate_ab(MockBundle())
    print(f"A: {result.prompt_a[:80]}…")
    print(f"B: {result.prompt_b[:80]}…")
    ab.record_feedback(result, "b")
    print(f"Winner: {result.winner}")
    print("OK")