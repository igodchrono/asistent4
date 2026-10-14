# -*- coding: utf-8 -*-
"""Тесты «то же самое, но X» — продолжение генерации.

Запуск:
    python/python.exe -m pytest tests/test_imggen_continuation.py -v
или без pytest:
    python/python.exe tests/test_imggen_continuation.py

TC-CONT-001 «то же самое, но спиной» → refs=[A], wf=i2i, якоря сохранены
TC-CONT-002 «а теперь в полный рост» → refs=[предыдущий]
TC-CONT-003 «повтори» → refs=[предыдущий], тот же seed
TC-CONT-004 «сгенерируй заново с нуля» → НЕ continuation (явный сброс)
TC-CONT-005 «нарисуй кота в шляпе» → НЕ continuation (новая сцена)
TC-CONT-006 короткий новый промт (<70% длины) → warning + retry (drift)
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PLUGIN_DIR = DATA_DIR / "plugins" / "phone_media"
sys.path.insert(0, str(DATA_DIR))
sys.path.insert(0, str(PLUGIN_DIR))

from core.continuation_detector import (  # noqa: E402
    CONTINUATION_TRIGGERS,
    continuation_trigger,
    is_continuation,
    is_reset_request,
)
from core.generation_history import (  # noqa: E402
    GenerationHistory,
    GenerationRecord,
    get_history,
    reset_history,
)
from core.prompt_anchors import (  # noqa: E402
    apply_anchors,
    check_prompt_drift,
    extract_anchors,
    prompt_similarity,
)
from plugin import PluginImpl  # noqa: E402


PROMPT_A = (
    "The image is a vertical anime illustration of a cute fox girl with "
    "long flowing red hair, fox ears, a fluffy tail and bright blue eyes. "
    "She is sitting on a wooden chair in a dark room wearing black lace "
    "lingerie and a choker. The lighting is soft and dim with a warm rim "
    "light. The overall composition is a clear view of only this scene."
)


class FakeApp:
    """Минимальный app для unit-тестов (не тащит PyQt/ChatEngine)."""

    def __init__(self, plugin_settings=None):
        self._settings = dict(plugin_settings or {})
        self.state = {}
        self._gen_history = None

    def get_plugin_setting(self, plugin_id, key, default=None):
        block = self._settings.get(plugin_id) or {}
        if not isinstance(block, dict):
            return default
        return block.get(key, default)

    def get_active_character(self):
        return "Лисичка"


def make_tmp_image(tmp: Path, name: str = "gen_1791955911.png") -> Path:
    img = tmp / name
    img.write_bytes(b"\x89PNG\r\n\x1a\n-fake-")
    return img


def seed_history(tmp: Path, img: Path, seed: int = 1791955911) -> GenerationHistory:
    h = GenerationHistory(tmp / "history.jsonl")
    h.add(GenerationRecord(
        id=img.stem, timestamp=time.time(), prompt=PROMPT_A,
        workflow="t2i", output_path=str(img), seed=seed, kind="plain",
    ))
    return h


def fresh_plugin_settings() -> dict:
    return {
        "phone_media": {
            "continuation": {
                "enabled": True,
                "use_last_as_reference": True,
                "preserve_seed": True,
                "anchor_extraction": True,
                "min_prompt_similarity": 0.6,
                "i2i_strength": 0.7,
            },
            "workflows": {"t2i": "qwen21_t2i.json", "i2i": "i2i"},
        }
    }


# ───── continuation_detector ───────────────────────────────────────────

def test_cont_001_to_je_samoe_no_spinoj():
    assert is_continuation("то же самое, но спиной") is True
    assert continuation_trigger("то же самое, но спиной") in ("то же самое", "то же")
    assert not is_reset_request("то же самое, но спиной")


def test_cont_002_a_teper_v_polnyj_rost():
    assert is_continuation("а теперь то же самое в полный рост") is True
    assert is_continuation("а теперь в полный рост") is True


def test_cont_003_povtori():
    assert is_continuation("повтори") is True
    assert is_continuation("ещё раз") is True


def test_cont_004_reset_raznitsu():
    assert is_continuation("сгенерируй заново с нуля") is False
    assert is_continuation("нарисуй новую картинку ") is False
    assert is_reset_request("заново с нуля") is True


def test_cont_005_new_scene_not_continuation():
    assert is_continuation("нарисуй кота в шляпе") is False
    assert is_continuation("нарисуй дракона ") is False
    assert is_continuation("спасибо, всё отлично") is False


def test_cont_triggers_list_present():
    for t in ("то же самое", "спиной", "повтори", "а теперь"):
        assert t in CONTINUATION_TRIGGERS


# ───── prompt_anchors ──────────────────────────────────────────────────

def test_anchors_extraction():
    a = extract_anchors(PROMPT_A)
    assert "fox girl" in a.get("character", [])
    assert "black lace" in a.get("clothing", [])
    assert any("chair" in t or "wooden" in t for t in a.get("setting", []))
    assert any("anime" in t for t in a.get("style", []))


def test_anchors_apply_restores_missing():
    anchors = extract_anchors(PROMPT_A)
    new = "A fox girl standing in a dark room."
    out = apply_anchors(new, anchors)
    assert "black lace" in out
    assert "long flowing red hair" in out


def test_cont_006_short_new_prompt_drift():
    short = "a girl"
    drift, stats = check_prompt_drift(short, PROMPT_A, min_length_ratio=0.7)
    assert drift is True
    assert stats["length_ratio"] < 0.7
    # почти той же длины и с теми же деталями — дрейфа нет
    full = PROMPT_A.replace(
        "She is sitting on a wooden chair in a dark room",
        "She is standing, seen from behind, in the same dark room",
    )
    drift2, stats2 = check_prompt_drift(full, PROMPT_A, min_length_ratio=0.7)
    assert drift2 is False


def test_prompt_similarity():
    assert prompt_similarity(PROMPT_A, PROMPT_A) == 1.0
    assert prompt_similarity(PROMPT_A, "a girl") < 0.5


# ───── generation_history ──────────────────────────────────────────────

def test_history_persists_and_find():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        img1 = make_tmp_image(tmp, "gen_1.png")
        img2 = make_tmp_image(tmp, "gen_2.png")
        h = GenerationHistory(tmp / "h.jsonl")
        h.add(GenerationRecord("gen_1", time.time(), "cat on a chair", workflow="t2i",
                               output_path=str(img1), seed=11))
        h.add(GenerationRecord("gen_2", time.time(), "fox girl in black lace lingerie",
                               workflow="i2i", output_path=str(img2), refs=[str(img1)], seed=12))
        assert h.count() == 2
        assert h.last().id == "gen_2"
        assert h.last().ref_paths() == [Path(str(img1))]
        assert [r.id for r in h.last_n(1)] == ["gen_2"]
        assert [r.id for r in h.find_by_query("lace")] == ["gen_2"]
        assert [r.id for r in h.find_by_query("on a chair")] == ["gen_1"]
        # перезагрузка из файла
        h2 = GenerationHistory(tmp / "h.jsonl")
        assert h2.count() == 2
        assert h2.last().prompt == h.last().prompt


def test_legacy_record_returns_dict():
    with tempfile.TemporaryDirectory() as td:
        h = GenerationHistory(Path(td) / "h.jsonl")
        d = h.record("legacy cat", negative="lowres", seed=7, image_path="/tmp/x.png")
        assert isinstance(d, dict)
        assert d["seed"] == 7
        assert h.last().prompt == "legacy cat"


def test_history_singleton_reset():
    reset_history()
    h1 = get_history()
    h2 = get_history()
    assert h1 is h2
    reset_history()


# ───── plugin routing (imggen) ─────────────────────────────────────────

def _make_app_with_history(tmp: Path):
    img = make_tmp_image(tmp)
    h = seed_history(tmp, img)
    app = FakeApp(fresh_plugin_settings())
    app._gen_history = h
    return app, img


def test_cont_001_plugin_plan():
    with tempfile.TemporaryDirectory() as td:
        app, img = _make_app_with_history(Path(td))
        p = PluginImpl()
        plan = p._cont_plan(app, "то же самое, но спиной")
        assert plan is not None
        assert plan["refs"] == [str(img)]
        assert plan["seed"] == 1791955911
        assert p._family(plan["wf"]) == "edit"  # i2i-семейство
        assert plan["wf"] in ("i2i", "qwen21_i2i", "qwen21_i2i.json")
        assert plan["raw"].startswith("REVISION")
        assert "ORIGIN:" in plan["raw"] and "PREVIOUS:" in plan["raw"] and "CHANGE:" in plan["raw"]
        assert PROMPT_A in plan["raw"]


def test_cont_002_plan_a_teper():
    with tempfile.TemporaryDirectory() as td:
        app, img = _make_app_with_history(Path(td))
        p = PluginImpl()
        plan = p._cont_plan(app, "а теперь то же самое в полный рост")
        assert plan and plan["refs"] == [str(img)]


def test_cont_003_plan_povtori_same_seed():
    with tempfile.TemporaryDirectory() as td:
        app, _ = _make_app_with_history(Path(td))
        p = PluginImpl()
        plan = p._cont_plan(app, "повтори")
        assert plan and plan["seed"] == 1791955911


def test_cont_004_plan_reset_none():
    with tempfile.TemporaryDirectory() as td:
        app, _ = _make_app_with_history(Path(td))
        p = PluginImpl()
        assert p._cont_plan(app, "сгенерируй заново с нуля") is None


def test_cont_005_plan_new_scene_none():
    with tempfile.TemporaryDirectory() as td:
        app, _ = _make_app_with_history(Path(td))
        p = PluginImpl()
        assert p._cont_plan(app, "нарисуй кота в шляпе") is None


def test_cont_settings_enabled_flag():
    app = FakeApp(fresh_plugin_settings())
    p = PluginImpl()
    cfg = p._cont_settings(app)
    assert cfg["enabled"] is True
    assert cfg["i2i_strength"] == 0.7
    off = FakeApp({"phone_media": {"continuation": {"enabled": False}}})
    plan_none = PluginImpl()._cont_plan(off, "то же самое, но спиной")
    assert plan_none is None


def test_plugin_record_generation_writes_history():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        h = GenerationHistory(tmp / "h2.jsonl")
        app = FakeApp(fresh_plugin_settings())
        app._gen_history = h
        dest = tmp / "gen_99.png"
        dest.write_bytes(b"\x89PNG")
        app.state["imggen_request"] = "gen request"
        app.state["imggen_kind"] = "cont"
        p = PluginImpl()
        p._record_generation(app, prompt=PROMPT_A, workflow="qwen21_i2i.json",
                             refs=["x.png"], seed=5, dest=dest)
        r = h.last()
        assert r is not None
        assert r.id == "gen_99"
        assert r.workflow == "qwen21_i2i"
        assert r.seed == 5
        assert r.kind == "cont"
        assert r.request == "gen request"
        assert r.refs == ["x.png"]
        assert Path(r.output_path).exists()


if __name__ == "__main__":
    import traceback

    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    total = len([n for n in globals() if n.startswith("test_")])
    print(f"\n{'-' * 40}\n{total - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)