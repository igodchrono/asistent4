# -*- coding: utf-8 -*-
"""Selftest БЛОК 10: качество генерации + система промтов.

Запуск (из папки data):
    python selftest_prompts.py

Покрывает TC-GEN-001 … TC-GEN-012.
"""
from __future__ import annotations

import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.prompt_builder import PromptBuilder, PromptBundle
from core.prompt_enhancer import PromptEnhancer
from core.refine_loop import RefineLoop
from core.prompt_ab import PromptAB
from core.generation_history import GenerationHistory
from core.prompt_pipeline import PromptPipeline
from core.generation_metrics import compute_metrics
from core import intents


PASS = 0
FAIL = 0
FAILS: list = []


def check(name: str, fn) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  OK   TC-{name}")
    except AssertionError as e:
        FAIL += 1
        FAILS.append(name)
        print(f"  FAIL TC-{name}: {e}")
    except Exception as e:
        FAIL += 1
        FAILS.append(name)
        print(f"  FAIL TC-{name} (exception): {e!r}")


# ── TC-GEN-001: «нарисуй кота» → слои качества+стиля+сюжета ──────────

def tc_001():
    pb = PromptBuilder()
    b = pb.build("нарисуй кота")
    assert b.positive, "positive empty"
    assert "masterpiece" in b.positive, "нет слоя качества"
    assert any(s in b.positive for s in ("anime", "photo", "watercolor", "oil")), "нет слоя стиля"
    assert "кота" in b.positive, "нет сюжета"
    assert b.negative and "lowres" in b.negative, "нет базового негатива"
    assert b.steps == 25 and b.cfg_scale == 7.0, f"стандартные параметры: {b.steps}/{b.cfg_scale}"


# ── TC-GEN-002: «в стиле аниме» → preset anime, cfg 7.0 ──────────────

def tc_002():
    pb = PromptBuilder()
    b = pb.build("нарисуй кота в стиле аниме", style_preset="anime")
    assert b.style_preset == "anime", f"style={b.style_preset}"
    assert b.cfg_scale == 7.0, f"cfg={b.cfg_scale}"
    assert "anime style" in b.positive, "нет префикса аниме"
    pipe = PromptPipeline(prompt_enhancer_enabled=False)
    style, _ = pipe.detect_style_quality("нарисуй кота в стиле аниме")
    assert style == "anime", f"детект стиля: {style}"


# ── TC-GEN-003: «нарисуй себя» → референс подставлен ─────────────────

def tc_003():
    pb = PromptBuilder()
    attrs = {"hair_color": "silver", "eye_color": "amber", "style": "anime"}
    b = pb.build(
        "нарисуй себя",
        sources={
            "reference": Path("characters/лисичка/images/ref.png"),
            "reference_attrs": attrs,
        },
    )
    assert b.reference_image is not None, "референс не подставлен"
    assert "silver hair" in b.positive, "нет атрибута волос"
    assert "amber eyes" in b.positive, "нет атрибута глаз"
    composed = PromptBuilder.compose_with_reference("нарисуй себя", attrs)
    assert "silver hair" in composed and "нарисуй себя" in composed


# ── TC-GEN-004: «добавь деталей» → +detailed, steps+10 ───────────────

def tc_004():
    pb = PromptBuilder()
    bundle = pb.build("нарисуй кота")
    rl = RefineLoop(save_successful=False)
    before = bundle.steps
    r = rl.parse_and_refine("добавь деталей", bundle)
    assert "intricate" in r.bundle_positive or "detail" in r.bundle_positive.lower(), \
        "нет +detailed в промте"
    assert r.steps == before + 10, f"steps: {before} → {r.steps}"
    assert RefineLoop.is_refine_trigger("добавь деталей") is True


# ── TC-GEN-005: «слишком темно» → refine +bright, +daylight ──────────

def tc_005():
    pb = PromptBuilder()
    bundle = pb.build("нарисуй пейзаж")
    rl = RefineLoop(save_successful=False)
    r = rl.parse_and_refine("слишком темно", bundle)
    assert "bright" in r.bundle_positive, "нет +bright"
    assert "daylight" in r.bundle_positive, "нет +daylight"
    assert r.cfg_scale < bundle.cfg_scale, "cfg не снизился"
    assert RefineLoop.is_refine_trigger("слишком темно") is True
# ── TC-GEN-006: «в высоком качестве» → quality=high, hires_fix ───────

def tc_006():
    pb = PromptBuilder()
    b = pb.build("нарисуй кота", quality_preset="high")
    assert b.quality_preset == "high", f"quality={b.quality_preset}"
    assert b.steps == 40, f"steps={b.steps}"
    quality_cfg = pb._quality_presets.get("high", {})
    assert quality_cfg.get("hires_fix") is True, "нет hires_fix"
    pipe = PromptPipeline(prompt_enhancer_enabled=False)
    _, quality = pipe.detect_style_quality("сделай в высоком качестве")
    assert quality == "high"


# ── TC-GEN-007: «покажи 3 варианта» → 3 seed'а, один промт ───────────

def tc_007():
    phrase = "покажи 3 варианта кота"
    got = intents.classify(phrase)
    assert got is not None and got.get("intent") == "imggen", f"intent={got}"
    assert got["args"].get("count") == 3, f"count={got['args'].get('count')}"


# ── TC-GEN-008: «повтори с тем же seed» → тот же seed из history ─────

def tc_008():
    tmp = Path(tempfile.mkdtemp()) / "h.jsonl"
    hist = GenerationHistory(tmp)
    hist.record("a cat", seed=424242, style="anime", quality="standard")
    last = hist.last()
    assert last["seed"] == 424242
    found = hist.get_by_seed(424242)
    assert found is not None, "seed не найден"
    hist2 = GenerationHistory(tmp)  # перечитываем из файла
    assert hist2.get_by_seed(424242)["seed"] == 424242, "seed не персистится"
    tmp.unlink(missing_ok=True)


# ── TC-GEN-009: «сделай вариацию» → тот же seed + мутация промта ─────

def tc_009():
    pb = PromptBuilder()
    b = pb.build("нарисуй кота", overrides={"seed": 777})
    assert b.seed == 777
    pipe = PromptPipeline(prompt_enhancer_enabled=False)
    variant = pipe.refine_bundle("сделай вариацию", b)
    assert variant.seed != b.seed, "seed не мутировал"
    assert variant.positive != b.positive or variant.steps != b.steps, "промт не изменён"
    b2 = pb.build("нарисуй кота", overrides={"seed": 777})
    assert b2.positive == b.positive, "разные промты для одного seed"


class _MockBundle:
    positive = "masterpiece, anime style, a cat, soft light, detailed"
    weights = {
        "quality": 0.7, "style": 0.9, "lighting": 0.8,
        "composition": 1.0, "details": 0.8, "mood": 0.7,
    }


# ── TC-GEN-010: Фидбек 👍 → веса слоёв обновлены ─────────────────────

def tc_010():
    ab = PromptAB(enabled=True, sample_rate=1.0)
    result = ab.generate_ab(_MockBundle())
    assert result.prompt_a and result.prompt_b
    ab.record_feedback(result, "a")
    assert result.winner == "a"
    assert len(ab._history) >= 1


# ── TC-GEN-011: Фидбек 👎 → refine_loop запущен ──────────────────────

def tc_011():
    pb = PromptBuilder()
    bundle = pb.build("нарисуй кота")
    rl = RefineLoop(save_successful=False)
    r = rl.parse_and_refine("не то, сделай ярче", bundle)
    assert r.actions_applied, "нет действий refine"
    assert r.bundle_positive != bundle.positive
    assert r.iteration == 1


# ── TC-GEN-012: A/B включен → 2 варианта в чате ──────────────────────

def tc_012():
    ab = PromptAB(enabled=True, sample_rate=1.0)
    result = ab.generate_ab(_MockBundle())
    assert result.prompt_a != result.prompt_b, "варианты совпадают"
    assert result.layers_a and result.layers_b
    ab_disabled = PromptAB(enabled=False)
    assert ab_disabled.should_test() is False


# ── Метрики (бонус) ──────────────────────────────────────────────────

def tc_metrics():
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("   ??? TC-metrics: нет PIL, пропускаю")
        return
    tmp = Path(tempfile.mkdtemp()) / "m.png"
    img = Image.new("RGB", (128, 128), (70, 80, 90))
    ImageDraw.Draw(img).rectangle((20, 20, 100, 100), fill=(200, 60, 80))
    img.save(str(tmp))
    m = compute_metrics(tmp, prompt_text="a cat")
    assert 0.0 <= m["overall"] <= 1.0, "overall вне диапазона"
    assert "sharpness" in m and "brightness" in m
    tmp.unlink(missing_ok=True)


# ── Pipeline-интеграция ──────────────────────────────────────────────

def tc_pipeline():
    pipe = PromptPipeline(prompt_enhancer_enabled=False)
    b = pipe.build_bundle("нарисуй кота в высоком качестве", persona=None)
    assert b.quality_preset == "high"
    assert b.seed is not None
    entry = pipe.record_generation(b, image_path=None)
    assert "seed" in entry
    r = pipe.refine_bundle("добавь деталей", b)
    assert r.steps > b.steps or "detail" in r.positive.lower()
    fmt = PromptPipeline.format_success(b)
    assert "🎨" in fmt and "Seed" in fmt


def main():
    print(f"== БЛОК 10: selftest_prompts ==")
    for name, fn in [
        ("GEN-001", tc_001), ("GEN-002", tc_002), ("GEN-003", tc_003),
        ("GEN-004", tc_004), ("GEN-005", tc_005), ("GEN-006", tc_006),
        ("GEN-007", tc_007), ("GEN-008", tc_008), ("GEN-009", tc_009),
        ("GEN-010", tc_010), ("GEN-011", tc_011), ("GEN-012", tc_012),
        ("metrics", tc_metrics), ("pipeline", tc_pipeline),
    ]:
        check(name, fn)
    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILS:
        print("Fails:", ", ".join(FAILS))
    raise SystemExit(FAIL)


if __name__ == "__main__":
    main()