# -*- coding: utf-8 -*-
"""Тесты fallback-логики imggen: пустой ответ LLM, refusal, content_filter,
RU→EN перевод и защита от UnboundLocalError (refs).

Запуск (из корня asistent3):
    python/python.exe tests/test_imggen_llm_fallback.py
или
    python/python.exe -m pytest tests/test_imggen_llm_fallback.py -v
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PLUGIN_DIR = DATA_DIR / "plugins" / "phone_media"
sys.path.insert(0, str(DATA_DIR))
sys.path.insert(0, str(PLUGIN_DIR))

from plugin import (
    PluginImpl,
    extract_prompt_from_llm,
    _translate_scene,
    _RU_EN,
)


class FakeLLM:
    """Фейк LLM: возвращает заранее заданный message-словарь."""

    def __init__(self, result: dict):
        self.result = result
        self.calls = 0

    async def chat_once_message(self, messages, **opts):
        self.calls += 1
        return dict(self.result)


class FakeApp:
    """Минимальный app для unit-тестов (без PyQt)."""

    def __init__(self, plugin_settings=None, llm_result: dict = None):
        self._settings = dict(plugin_settings or {})
        self.state = {}
        self.llm = FakeLLM(llm_result) if llm_result is not None else None

    def get_plugin_setting(self, plugin_id, key, default=None):
        block = self._settings.get(plugin_id) or {}
        if not isinstance(block, dict):
            return default
        return block.get(key, default)

    def get_active_character(self):
        return "Лисичка"


def fresh_settings():
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


def make_tmp_ref(tmp: Path, name: str = "ref.png") -> Path:
    img = tmp / name
    img.write_bytes(b"\x89PNG\r\n\x1a\n-fake-")
    return img


# ── _translate_scene ──────────────────────────────────────────────────────


def test_translate_empty():
    assert _translate_scene("") == ""
    assert not _translate_scene(None)


def test_translate_basic():
    result = _translate_scene("девушка сидит на стуле")
    assert "girl" in result.lower()
    assert "sitting" in result.lower()
    assert "chair" in result.lower()


# ── extract_prompt_from_llm ────────────────────────────────────────────────


def _api(content="", reasoning="", finish="stop", refusal=None):
    msg = {"role": "assistant", "content": content, "reasoning_content": reasoning}
    if refusal is not None:
        msg["refusal"] = refusal
    return {
        "choices": [{"message": msg, "finish_reason": finish}],
        "usage": {"completion_tokens": 10, "total_tokens": 50},
    }


def test_llm_005_content_filter():
    """TC-LLM-005: finish_reason=content_filter → пустой промт."""
    r = _api(content="cat", finish="content_filter")
    assert extract_prompt_from_llm(r) == ""


def test_llm_006_refusal_field():
    """TC-LLM-006: refusal в message → пустой промт."""
    r = _api(content="cat", refusal="I refuse")
    assert extract_prompt_from_llm(r) == ""


def test_llm_refusal_content():
    """"Извините, не могу..." в content → пустой промт."""
    r = _api(content="Извините, не могу выполнить этот запрос.")
    assert extract_prompt_from_llm(r) == ""


def test_llm_reasoning_fallback():
    """Пустой content + reasoning_content=JSON → промт из reasoning."""
    r = _api(reasoning='{"prompt": "a cat on windowsill"}')
    assert "cat" in extract_prompt_from_llm(r)


def test_llm_no_choices_but_message():
    """"Нет choices, есть message — не падаем."""
    r = {"message": {"content": "a dog"}}
    assert extract_prompt_from_llm(r) == "a dog"


def test_llm_plain_empty():
    """Совсем пустой ответ → ""."""
    assert extract_prompt_from_llm({}) == ""
    assert extract_prompt_from_llm("") == ""
def run_one_prompt(app, request, refs, wf="t2i"):
    """Запустить PluginImpl._one_prompt в новом цикле событий."""
    p = PluginImpl()
    return asyncio.run(p._one_prompt(app, request, refs, wf))
# ── _one_prompt: защита от UnboundLocalError + fallback ────────────────────


def test_bug_001_generate_sebya_no_ref():
    """TC-BUG-001: «сгенерируй себя» без refs → не падает, refs=[] (defensive init)."""
    app = FakeApp(fresh_settings(), llm_result={"content": "a fox girl in the forest"})
    app.state["imggen_kind"] = "plain"
    app.state["imggen_stage"] = "drafting"
    prompt = run_one_prompt(app, "сгенерируй себя", [])
    assert prompt, f"prompt должен быть непустым, получено {prompt!r}"
    assert not app.state.get("imggen_llm_fallback")


def test_bug_001b_no_refs_keyword():
    """Подстраховка: `refs=None` → list(refs or []) не даёт UnboundLocalError."""
    app = FakeApp(fresh_settings(), llm_result={"content": "a fox girl in the forest"})
    app.state["imggen_kind"] = "plain"
    app.state["imggen_stage"] = "drafting"
    prompt = run_one_prompt(app, "сгенерируй себя", None)
    assert prompt


def test_bug_002_generate_sebya_v_belve():
    """TC-BUG-002: «сгенерируй себя в белье» + ref.png → edit/self, refs=[ref]."""
    p = PluginImpl()
    with tempfile.TemporaryDirectory() as tmp:
        ref = make_tmp_ref(Path(tmp))
        app = FakeApp(fresh_settings(), llm_result={"content": "<image1> change clothes"})
        app.state["imggen_kind"] = "self"
        app.state["imggen_stage"] = "drafting"
        app.state["character_self_ref"] = str(ref)
        prompt = run_one_prompt(app, "сгенерируй себя в белье", [str(ref)], wf="edit")
        assert prompt, f"prompt должен быть непустым, получено {prompt!r}"
        assert not app.state.get("imggen_llm_fallback")


def test_llm_001_empty_response_fallback():
    """TC-LLM-001: LLM вернула пусто → fallback, prompt непустой, флаг True."""
    app = FakeApp(fresh_settings(), llm_result={"content": "", "finish_reason": "stop"})
    app.state["imggen_kind"] = "plain"
    app.state["imggen_stage"] = "drafting"
    prompt = run_one_prompt(app, "девушка в черном платье", [])
    assert prompt, f"fallback должен дать промт, получено {prompt!r}"
    assert app.state.get("imggen_llm_fallback") is True


def test_llm_003_refusal_fallback():
    """TC-LLM-003: refusal → fallback, флаг True."""
    app = FakeApp(fresh_settings(), llm_result={"content": "", "refusal": "no"})
    app.state["imggen_kind"] = "plain"
    app.state["imggen_stage"] = "drafting"
    prompt = run_one_prompt(app, "котик", [])
    assert prompt
    assert app.state.get("imggen_llm_fallback") is True


def test_llm_004_llm_offline():
    """TC-LLM-004: app.llm отсутствует → fallback, prompt непустой."""
    app = FakeApp(fresh_settings(), llm_result=None)
    app.state["imggen_kind"] = "plain"
    app.state["imggen_stage"] = "drafting"
    prompt = run_one_prompt(app, "котик на подоконнике", [])
    assert prompt
    assert app.state.get("imggen_llm_fallback") is True
# ── _log_llm_raw: диагностика usage/refusal/finish_reason ──────────────────


def test_diag_001_log_llm_raw():
    """TC-DIAG-001: _log_llm_raw пишет usage, refusal, finish_reason в лог."""
    p = PluginImpl()
    app = FakeApp(fresh_settings(), llm_result={"content": "x"})
    app.state["imggen_stage"] = "drafting"
    log_file = DATA_DIR / "logs" / "imggen_prompt_raw.log"
    before = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    msg = {
        "content": "test",
        "reasoning_content": "",
        "finish_reason": "stop",
        "refusal": None,
        "usage": {"completion_tokens": 5, "total_tokens": 15},
    }
    p._log_llm_raw(app, [{"role": "user", "content": "hi"}], msg)
    after = log_file.read_text(encoding="utf-8")
    lines = [l for l in after.splitlines() if l.strip()]
    assert len(lines) >= 1
    data = json.loads(lines[-1])
    assert data.get("usage") == {"completion_tokens": 5, "total_tokens": 15}
    assert data["message"]["finish_reason"] == "stop"
    assert "refusal" in data["message"]


# ── _RU_EN словарь ─────────────────────────────────────────────────────────


def test_ru_en_has_common_words():
    common = {r"\bдевушка\b", r"\bсидит\b", r"\bбель[её]\b", r"\bспиной\b", r"\bна стул\w*\b"}
    for pat in common:
        assert any(pat in k for k in _RU_EN), f"паттерн {pat!r} отсутствует в _RU_EN"


# ── Runner без pytest ──────────────────────────────────────────────────────


def _main():
    import traceback
    failed = 0
    total = 0
    for name in sorted(globals()):
        if name.startswith("test_"):
            total += 1
            try:
                globals()[name]()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{'-' * 40}\n{total - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())