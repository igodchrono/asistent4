# -*- coding: utf-8 -*-
"""RefineLoop — итеративное улучшение промта на основе обратной связи.

Триггеры refine:
- «не то», «слишком», «добавь», «убери», «поменяй», «сделай более»,
- 👍 / 👎 после генерации,
- явный запрос «перегенерируй с <правкой>».
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


_log = logging.getLogger("refine_loop")

_SUCCESSFUL_PATH = Path(__file__).resolve().parents[1] / "prompts" / "successful.jsonl"

_REFINE_RULES: Dict[str, List[Dict[str, Any]]] = {
    "темн": [
        {"action": "add_layer", "text": "bright, daylight, well-lit, sunny"},
        {"action": "remove_keyword", "keyword": "dark"},
        {"action": "adjust_param", "param": "cfg_scale", "delta": -0.5},
    ],
    "светл": [
        {"action": "add_layer", "text": "dim, moody lighting, shadows"},
        {"action": "adjust_param", "param": "cfg_scale", "delta": 0.3},
    ],
    "тускл": [
        {"action": "add_layer", "text": "vibrant colors, high contrast, saturated"},
        {"action": "adjust_param", "param": "cfg_scale", "delta": 0.5},
    ],
    "ярк": [
        {"action": "add_layer", "text": "soft colors, pastel, muted"},
        {"action": "adjust_param", "param": "cfg_scale", "delta": -0.3},
    ],
    "ярч": [
        {"action": "add_layer", "text": "vibrant colors, high contrast, saturated"},
        {"action": "adjust_param", "param": "cfg_scale", "delta": 0.5},
    ],
    "вариаци": [
        {"action": "mutate_prompt"},
        {"action": "mark_variation"},
    ],
    "детал": [
        {"action": "add_layer", "text": "intricate details, highres, sharp focus, texture"},
        {"action": "adjust_param", "param": "steps", "delta": 10},
    ],
    "размыт": [
        {"action": "add_layer", "text": "sharp focus, crisp, highres, detailed"},
        {"action": "remove_keyword", "keyword": "blurry"},
        {"action": "adjust_param", "param": "steps", "delta": 10},
    ],
    "аниме": [{"action": "set_style", "preset": "anime"}],
    "реалистичн": [{"action": "set_style", "preset": "photorealistic"}],
    "акварель": [{"action": "set_style", "preset": "watercolor"}],
    "масло": [{"action": "set_style", "preset": "oil_painting"}],
    "фон": [{"action": "add_to_negative", "text": "background, busy background, complex background"}],
    "лиц": [
        {"action": "add_layer", "text": "detailed face, sharp eyes, symmetry"},
        {"action": "adjust_param", "param": "cfg_scale", "delta": 0.3},
    ],
    "рук": [
        {"action": "add_to_negative", "text": "bad hands, extra hands, deformed hands"},
        {"action": "add_layer", "text": "detailed hands, proper hands"},
    ],
    "больше": [{"action": "adjust_param", "param": "steps", "delta": 5}],
    "меньше": [{"action": "adjust_param", "param": "steps", "delta": -5, "min": 10}],
    "быстр": [{"action": "set_quality", "preset": "draft"}],
    "высок": [{"action": "set_quality", "preset": "high"}],
    "ультра": [{"action": "set_quality", "preset": "ultra"}],
}


@dataclass
class RefineAction:
    action: str
    text: Optional[str] = None
    keyword: Optional[str] = None
    param: Optional[str] = None
    delta: Optional[float] = None
    preset: Optional[str] = None
    min: Optional[float] = None


@dataclass
class RefineResult:
    bundle_positive: str
    bundle_negative: str
    steps: int
    cfg_scale: float
    seed: Optional[int]
    actions_applied: List[RefineAction]
    feedback_text: str
    iteration: int = 0
    final: bool = False
class RefineLoop:
    """Итеративное улучшение промта на основе обратной связи.

    Args:
        max_iterations: максимум циклов улучшения.
        save_successful: сохранять успешные пары в successful.jsonl.
        generator_fn: функция, принимающая bundle → (путь к изображению).
    """

    def __init__(
        self,
        max_iterations: int = 3,
        save_successful: bool = True,
        generator_fn: Optional[Callable[..., Any]] = None,
    ):
        self.max_iterations = max_iterations
        self.save_successful = save_successful
        self.generator_fn = generator_fn

    def refine(
        self,
        user_feedback: str,
        current_bundle: Any,
        previous_generation: Optional[Any] = None,
    ) -> RefineResult:
        """Один проход улучшения промта."""
        actions = self._parse_feedback(user_feedback)
        bundle = current_bundle.clone() if hasattr(current_bundle, "clone") else current_bundle

        for act in actions:
            if act.action == "add_layer" and act.text:
                bundle.positive = f"{bundle.positive}, {act.text}"
            elif act.action == "remove_keyword" and act.keyword:
                bundle.positive = re.sub(
                    rf"\b{re.escape(act.keyword)}\b", "", bundle.positive, flags=re.I)
                bundle.negative = re.sub(
                    rf"\b{re.escape(act.keyword)}\b", "", bundle.negative, flags=re.I)
            elif act.action == "adjust_param":
                if act.param == "steps":
                    new_val = bundle.steps + int(act.delta or 0)
                    if act.min is not None:
                        new_val = max(int(act.min), new_val)
                    bundle.steps = max(5, min(150, new_val))
                elif act.param == "cfg_scale":
                    new_val = bundle.cfg_scale + (act.delta or 0)
                    if act.min is not None:
                        new_val = max(act.min, new_val)
                    bundle.cfg_scale = max(1.0, min(30.0, new_val))
            elif act.action == "set_style":
                bundle.style_preset = act.preset or bundle.style_preset
            elif act.action == "set_quality":
                bundle.quality_preset = act.preset or bundle.quality_preset
            elif act.action == "add_to_negative" and act.text:
                bundle.negative = f"{bundle.negative}, {act.text}"
            elif act.action == "mutate_prompt":
                # Переставить последние две части промта (лёгкая мутация)
                segs = [p.strip() for p in bundle.positive.split(",") if p.strip()]
                if len(segs) > 2:
                    segs[-1], segs[-2] = segs[-2], segs[-1]
                    bundle.positive = ", ".join(segs)
            elif act.action == "mark_variation":
                bundle.metadata["variation"] = True

        # Убрать двойные запятые
        bundle.positive = re.sub(r",\s*,", ",", bundle.positive).strip().strip(",")
        bundle.negative = re.sub(r",\s*,", ",", bundle.negative).strip().strip(",")

        result = RefineResult(
            bundle_positive=bundle.positive,
            bundle_negative=bundle.negative,
            steps=bundle.steps,
            cfg_scale=bundle.cfg_scale,
            seed=bundle.seed,
            actions_applied=actions,
            feedback_text=user_feedback,
            iteration=1,
            final=False,
        )

        _log.debug("refine: %d actions → steps=%d cfg=%.1f style=%s",
                    len(actions), result.steps, result.cfg_scale, bundle.style_preset)

        if self.generator_fn is not None:
            try:
                self.generator_fn(bundle)
            except Exception as e:
                _log.warning("refine generator error: %s", e)

        if self.save_successful:
            self._record_successful(user_feedback, bundle)

        return result

    def parse_and_refine(self, user_feedback: str, current_bundle: Any) -> RefineResult:
        return self.refine(user_feedback, current_bundle)

    def _parse_feedback(self, text: str) -> List[RefineAction]:
        """Распарсить текст обратной связи в список действий."""
        lower = text.lower()
        actions: List[RefineAction] = []
        seen: set = set()

        for keyword, rules in _REFINE_RULES.items():
            if keyword in lower:
                for rule in rules:
                    action_type = rule["action"]
                    if action_type in seen:
                        continue
                    seen.add(action_type)
                    actions.append(RefineAction(**rule))

        return actions

    def _record_successful(self, feedback: str, bundle: Any) -> None:
        """Записать успешную пару (запрос → промт) в successful.jsonl."""
        try:
            entry = {
                "timestamp": time.time(),
                "feedback": feedback,
                "positive": getattr(bundle, "positive", ""),
                "negative": getattr(bundle, "negative", ""),
                "style": getattr(bundle, "style_preset", ""),
                "quality": getattr(bundle, "quality_preset", ""),
                "steps": getattr(bundle, "steps", 25),
                "cfg_scale": getattr(bundle, "cfg_scale", 7.0),
            }
            _SUCCESSFUL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(str(_SUCCESSFUL_PATH), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            _log.warning("record_successful: %s", e)

    @staticmethod
    def is_refine_trigger(text: str) -> bool:
        """Является ли фраза триггером для refine."""
        lower = text.lower()
        triggers = [
            "не то", "слишком", "добавь", "добавить",
            "убери", "убрать", "поменяй", "поменять",
            "сделай более", "сделай менее",
            "перегенерируй", "перегенерировать",
            "исправь", "поправь", "подправь",
            "ещё раз", "по-другому",
        ]
        return any(t in lower for t in triggers)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    from prompt_builder import PromptBuilder

    pb = PromptBuilder()
    bundle = pb.build("нарисуй кота")

    rl = RefineLoop(save_successful=False)

    # Тест 1: слишком темно
    result = rl.parse_and_refine("слишком темно, добавь деталей", bundle)
    print(f"До: steps={bundle.steps}, cfg={bundle.cfg_scale}")
    print(f"После: steps={result.steps}, cfg={result.cfg_scale}")
    print(f"positive (первые 120): {result.bundle_positive[:120]}")
    print(f"negative (начало): {result.bundle_negative[:120]}")
    print(f"actions: {[a.action for a in result.actions_applied]}")

    # Тест 2: аниме
    bundle2 = pb.build("нарисуй пейзаж")
    result2 = rl.parse_and_refine("сделай более аниме", bundle2)
    print(f"\nСтиль после refine: {bundle2.style_preset}")
    print(f"actions: {[a.action for a in result2.actions_applied]}")
    print("\n--- Тесты триггеров ---")
    print(f"'слишком темно' → {RefineLoop.is_refine_trigger('слишком темно')}")
    print(f"'просто привет' → {RefineLoop.is_refine_trigger('просто привет')}")

    print("\nOK")