# -*- coding: utf-8 -*-
"""Селф-тест БЛОКА 9 (TC-PICK-001…008).

Запуск из папки data:
    ..\python\python.exe selftest_image_picker.py

Использует синтетические скриншоты (data/cache/test_pick_*.png),
чтобы не зависеть от живого монитора.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image, ImageDraw  # noqa: E402

from core.feedback_parser import FeedbackParser  # noqa: E402
from core.preference_memory import PreferenceMemory  # noqa: E402
from plugins.image_picker.screen_analyzer import ScreenAnalyzer  # noqa: E402
from plugins.image_picker.smart_picker import SmartPicker  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  OK   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}  {extra}")


# ── Генерация синтетических скриншотов ────────────────────────────────

def _rect_texture(w: int, h: int, rgb) -> Image.Image:
    """Прямоугольник с «фото»-текстурой (шум) определённого цвета."""
    img = Image.new("RGB", (w, h), rgb)
    d = ImageDraw.Draw(img)
    step = 8
    for y in range(0, h, step):
        for x in range(0, w, step):
            shade = [min(255, max(0, c + ((x // step + y // step) % 3) * 6 - 6)) for c in rgb]
            d.rectangle((x, y, x + step, y + step), fill=tuple(shade))
    d.rectangle((1, 1, w - 2, h - 2), outline=(255, 255, 255), width=2)
    return img


def make_screenshot(path: Path, blocks) -> None:
    """blocks: list[(x,y,w,h,rgb,tag)] → собранный скриншот 1200×700."""
    bg = Image.new("RGB", (1200, 700), (35, 35, 38))
    for (x, y, w, h, rgb, _tag) in blocks:
        tex = _rect_texture(w, h, rgb)
        bg.paste(tex, (x, y))
    bg.save(str(path), format="PNG")


def blocks_dark_first() -> list:
    """Пять картинок: первая тёмная, остальные светлее/насыщеннее."""
    return [
        (40, 40, 260, 180, (18, 18, 22), "dark"),
        (340, 40, 260, 180, (200, 60, 60), "red"),
        (640, 40, 260, 180, (60, 120, 220), "blue"),
        (940, 40, 220, 180, (220, 220, 60), "yellow"),
        (40, 260, 260, 180, (40, 200, 120), "green"),
    ]


def blocks_ui_only() -> list:
    """Скриншот только с однотонными UI-панелями (без текстур)."""
    return [
        (0, 0, 1200, 60, (60, 60, 64), "panel"),
        (0, 640, 1200, 60, (60, 60, 64), "panel"),
    ]


def _new_memory(tmp: Path) -> PreferenceMemory:
    return PreferenceMemory(storage=tmp / "user.json",
                            history=tmp / "history.jsonl")


# ── Тест-поток ─────────────────────────────────────────────────────────

def test_pick_crop_and_repeat(tmp: Path) -> None:
    print("\n[TC-PICK-001] выбор + crop")
    shot = tmp / "screen1.png"
    make_screenshot(shot, blocks_dark_first())
    analyzer = ScreenAnalyzer(min_region_size=80)
    mem = _new_memory(tmp)
    picker = SmartPicker(analyzer, mem, persona={"name": "лисичка", "card": "мила и нежна"})
    r = picker.pick("выбери что-то", screenshot=shot)
    check("pick вернул регион", r.region is not None, str(r))
    check("crop создан и это НЕ весь скриншот",
          r.crop_path is not None and r.crop_path.is_file())
    if r.crop_path and r.crop_path.is_file():
        im = Image.open(str(r.crop_path))
        check("crop меньше скриншота",
              im.width < 1200 and im.height < 700, f"{im.size}")

    print("\n[TC-PICK-002] повторный запрос → другой регион (diversity)")
    picker2 = SmartPicker(analyzer, _new_memory(tmp),
                          persona={"name": "лисичка", "card": "мила и нежна"})
    a = picker2.pick("выбери", screenshot=shot)
    b = picker2.pick("опять выбери", screenshot=shot)
    iou = SmartPicker._intersection(a.region.bbox, b.region.bbox)
    check("второй pick не пересекается >50%", iou <= 0.5, f"IoU={iou:.2f}")


def test_preference_dark(tmp: Path) -> None:
    print("\n[TC-PICK-003] после 👍 на тёмную → следующий pick тёмный")
    shot = tmp / "screen2.png"
    make_screenshot(shot, blocks_dark_first())
    analyzer = ScreenAnalyzer(min_region_size=80)
    mem = _new_memory(tmp)
    regions = analyzer.find_all_images(shot)
    check("найдено >= 4 региона", len(regions) >= 4, f"n={len(regions)}")
    if len(regions) < 4:
        return
    dark = min(regions, key=lambda r: r.features.get("brightness", 255))
    mem.record_choice(chosen=dark, rejected=None, explicit_feedback="нравится")
    light = max(regions, key=lambda r: r.features.get("brightness", 0))
    s_dark, s_light = mem.score(dark), mem.score(light)
    check("тёмный теперь скор выше светлого",
          s_dark > s_light, f"dark={s_dark:.2f} light={s_light:.2f}")
    top = mem.get_top_tags(3)
    check("в топ-тегах есть dark или чёрный",
          any(k in ("dark", "чёрный") for k, _ in top), str(top))


def test_preference_reject(tmp: Path) -> None:
    print("\n[TC-PICK-004] после 👎 на красный → красный не в топ-тегах")
    shot = tmp / "screen3.png"
    make_screenshot(shot, blocks_dark_first())
    analyzer = ScreenAnalyzer(min_region_size=80)
    mem = _new_memory(tmp)
    regions = analyzer.find_all_images(shot)
    red = min(regions, key=lambda r: abs(r.features.get("mean_color", (0, 0, 0))[0] - 200))
    mem.record_choice(chosen=red, rejected=None, explicit_feedback="не то")
    check("красный не в топ-5 тегов",
          "красный" not in [k for k, _ in mem.get_top_tags(5)],
          str(mem.get_top_tags(5)))


def test_pick_many(tmp: Path) -> None:
    print("\n[TC-PICK-005] pick_many → top-3, без дубликатов")
    shot = tmp / "screen4.png"
    make_screenshot(shot, blocks_dark_first())
    picker = SmartPicker(ScreenAnalyzer(min_region_size=80), _new_memory(tmp))
    results = picker.pick_many("похожее", n=3, screenshot=shot)
    check("вернулось 3 результата", len(results) == 3, f"n={len(results)}")
    bboxes = [r.region.bbox for r in results]
    uniq = {b for b in bboxes}
    check("все регионы уникальны", len(uniq) == len(bboxes))
    for r in results:
        if r.crop_path:
            check("у каждого crop есть файл", r.crop_path.is_file())


def test_no_images(tmp: Path) -> None:
    print("\n[TC-PICK-006] на экране только UI → нет изображений")
    shot = tmp / "screen5.png"
    make_screenshot(shot, blocks_ui_only())
    analyzer = ScreenAnalyzer(min_region_size=80)
    regions = analyzer.find_all_images(shot)
    check("регионов нет (только UI)", len(regions) == 0, f"n={len(regions)}")
    picker = SmartPicker(analyzer, _new_memory(tmp))
    r = picker.pick("выбери что-то", screenshot=shot)
    check("reason == no_regions", getattr(r, "reason", "") == "no_regions", str(r.reason))


def test_same_region_penalty(tmp: Path) -> None:
    print("\n[TC-PICK-007] тот же регион, что в прошлый раз → штраф/исключение")
    shot = tmp / "screen6.png"
    make_screenshot(shot, blocks_dark_first())
    analyzer = ScreenAnalyzer(min_region_size=80)
    mem = _new_memory(tmp)
    picker = SmartPicker(analyzer, mem)
    first = picker.pick("выбери", screenshot=shot)
    bbox = tuple(first.region.bbox)
    picker2 = SmartPicker(analyzer, _new_memory(tmp), exclude_bboxes=[bbox])
    second = picker2.pick("выбери", screenshot=shot)
    iou = SmartPicker._intersection(bbox, second.region.bbox)
    check("штрафован/исключён", iou <= 0.5, f"IoU={iou:.2f}")


def test_persona_pref(tmp: Path) -> None:
    print("\n[TC-PICK-008] персонаж + предпочтения учитываются одновременно")
    shot = tmp / "screen7.png"
    make_screenshot(shot, blocks_dark_first())
    analyzer = ScreenAnalyzer(min_region_size=80)
    mem = _new_memory(tmp)
    picker = SmartPicker(analyzer, mem, persona={"name": "мистика", "card": "тёмная и таинственная лисица"})
    r = picker.pick("выбери", screenshot=shot)
    check("выбор есть при мистической персоне", r.region is not None)
    if r.region is not None:
        check("persona_score участвует", r.scores.get("persona", 0) > 0, str(r.scores))
    mem.record_choice(chosen=r.region, explicit_feedback="нравится")
    check("предпочтения записаны", len(mem.get_preference_vector()) == 4, str(mem.get_preference_vector()))


def test_feedback_parser() -> None:
    print("\n[фидбэк] parsing")
    fp = FeedbackParser()
    cases = [
        ("👍", 1.0), ("нравится", 1.0), ("супер", 1.0),
        ("👎", -1.0), ("не то", -1.0), ("ужас", -1.0),
        ("давай другое", -0.5), ("покажи ещё похожее", 0.5),
        ("забудь что мне нравилось", 0.0),
    ]
    ok = True
    for text, expect in cases:
        fb = fp.parse(text)
        if expect == 0.0:
            is_ok = fb.kind == "reset" if "забудь" in text else abs(fb.signal) <= 1e-6
        else:
            is_ok = (fb.signal > 0 and expect > 0) or (fb.signal < 0 and expect < 0)
        if not is_ok:
            ok = False
            print(f"    bad: {text!r} → {fb}")
    check("распознавание обратной связи", ok)


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="pick_test_"))

    def case(name: str) -> Path:
        p = root / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    try:
        test_pick_crop_and_repeat(case("001"))
        test_preference_dark(case("003"))
        test_preference_reject(case("004"))
        test_pick_many(case("005"))
        test_no_images(case("006"))
        test_same_region_penalty(case("007"))
        test_persona_pref(case("008"))
        test_feedback_parser()
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n=== БЛОК 9: PASS={_PASS} FAIL={_FAIL} ===")
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    main()