# -*- coding: utf-8 -*-
"""Тесты парсера промта imggen (extract_prompt_from_llm).

Запуск:
    python/python.exe -m pytest tests/test_imggen_prompt_parsing.py -v
или без pytest:
    python/python.exe tests/test_imggen_prompt_parsing.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# пути: data (модуль core) и папка плагина
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PLUGIN_DIR = DATA_DIR / "plugins" / "phone_media"
sys.path.insert(0, str(DATA_DIR))
sys.path.insert(0, str(PLUGIN_DIR))

from plugin import extract_prompt_from_llm  # noqa: E402


def api_response(content="", reasoning="", finish="stop"):
    """Полный ответ OpenAI-совместимого API."""
    return {
        "choices": [{
            "message": {"role": "assistant", "content": content, "reasoning_content": reasoning},
            "finish_reason": finish,
        }]
    }


# ──────────────────────────────────────────────────────────────────────

def test_plain_json_content():
    """TC-IMG-001: content = '{"prompt": "cat"}' → 'cat'."""
    r = api_response(content='{"prompt": "cat"}')
    assert extract_prompt_from_llm(r) == "cat"


def test_json_in_markdown_block():
    """TC-IMG-002: content = '```json\\n{"prompt": "cat"}\\n```' → 'cat'."""
    r = api_response(content='```json\n{"prompt": "cat"}\n```')
    assert extract_prompt_from_llm(r) == "cat"


def test_json_in_plain_markdown_block():
    """TC-IMG-002b: content = '```\\n{"prompt": "cat"}\\n```' → 'cat'."""
    r = api_response(content='```\n{"prompt": "cat"}\n```')
    assert extract_prompt_from_llm(r) == "cat"


def test_prefix_text():
    """TC-IMG-003: 'Конечно! Вот промт: cat on window' → 'cat on window'."""
    r = api_response(content="Конечно! Вот промт: cat on window")
    assert extract_prompt_from_llm(r) == "cat on window"


def test_reasoning_content_fallback():
    """TC-IMG-004: content пуст, reasoning_content = 'cat' → 'cat'."""
    r = api_response(content="", reasoning="cat")
    assert extract_prompt_from_llm(r) == "cat"


def test_reasoning_content_full_api():
    """TC-IMG-004b: полный API-ответ с reasoning_content вместо content."""
    r = api_response(content="", reasoning="The user wants a cat. Prompt: a cat on a windowsill")
    out = extract_prompt_from_llm(r)
    assert "cat" in out


def test_refusal_returns_empty():
    """TC-IMG-005: 'Извините, не могу' → '' (дальше сработает fallback)."""
    r = api_response(content="Извините, не могу")
    assert extract_prompt_from_llm(r) == ""


def test_truncated_json_returns_empty():
    """TC-IMG-006: обрезанный JSON → '' (fallback)."""
    r = api_response(content='{"prompt": "cat on a windo')
    assert extract_prompt_from_llm(r) == ""


def test_empty_response_returns_empty():
    """TC-IMG-007: пустой ответ → ''."""
    r = api_response(content="")
    assert extract_prompt_from_llm(r) == ""


# ── доп. кейсы ────────────────────────────────────────────────────────

def test_content_preferred_over_reasoning():
    """Если content непустой — берём его, reasoning игнорируем."""
    r = api_response(content="The image is a cat.", reasoning="long reasoning here")
    assert extract_prompt_from_llm(r) == "The image is a cat."


def test_rewritten_prompt_key():
    """JSON с ключом rewritten_prompt тоже подхватывается."""
    r = api_response(content='{"rewritten_prompt": "cat"}')
    assert extract_prompt_from_llm(r) == "cat"


def test_t2i_key():
    """JSON с ключом t2i (карточка промптов)."""
    r = api_response(content='{"t2i": "cat", "edit": "edit cat"}')
    assert extract_prompt_from_llm(r) == "cat"


def test_plain_text_no_json():
    """Простой текст без JSON возвращается как есть (ТЗ п.7)."""
    r = api_response(content="The image is a cat on a window, soft light")
    assert extract_prompt_from_llm(r) == "The image is a cat on a window, soft light"


def test_regex_prompt_in_text():
    """Regex: "prompt": "..." внутри текста с мусором."""
    r = api_response(content='prefix {"prompt": "cat"} suffix')
    assert extract_prompt_from_llm(r) == "cat"


def test_dict_without_choices():
    """Словарь вида {"content": "..."} без choices."""
    assert extract_prompt_from_llm({"content": '{"prompt": "cat"}'}) == "cat"
    assert extract_prompt_from_llm({"content": "", "reasoning_content": "cat"}) == "cat"


def test_string_input_directly():
    """Прямая строка вместо dict."""
    assert extract_prompt_from_llm('{"prompt": "cat"}') == "cat"
    assert extract_prompt_from_llm("cat on window") == "cat on window"
    assert extract_prompt_from_llm("") == ""


def test_short_garbage_returns_empty():
    """Короткий мусор (меньше 3 символов) → ''."""
    assert extract_prompt_from_llm(api_response(content="?")) == ""


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
