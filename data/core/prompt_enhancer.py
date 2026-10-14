# -*- coding: utf-8 -*-
"""PromptEnhancer — автоусиление промта через LLM.

Системный промт для enhancer:
    Ты — эксперт по промт-инжинирингу для Stable Diffusion / Midjourney.
    Улучши промт пользователя, сохранив его смысл.
    Добавь: освещение, композицию, детали, стиль.
    Не добавляй: новых объектов, которых не было в запросе.
    Формат вывода: одна строка, через запятую.
    Язык: английский.
"""
from __future__ import annotations

import logging
from typing import Any, Optional


_log = logging.getLogger("prompt_enhancer")

_SYSTEM_PROMPT = (
    "Ты — эксперт по промт-инжинирингу для Stable Diffusion / Midjourney.\n"
    "Улучши промт пользователя, сохранив его смысл.\n"
    "Добавь: освещение, композицию, детали, стиль.\n"
    "Не добавляй: новых объектов, которых не было в запросе.\n"
    "Формат вывода: одна строка, через запятую.\n"
    "Язык: английский."
)


class PromptEnhancer:
    """Автоусиление промта через LLM.

    Args:
        llm_manager: менеджер LLM (опционально, если None — вызов заглушен).
        enabled: если False — passthrough.
        model: модель для вызова.
        max_tokens: макс. токенов для ответа.
        language: предпочтительный язык (en / ru).
    """

    def __init__(
        self,
        llm_manager: Optional[Any] = None,
        enabled: bool = True,
        model: str = "local",
        max_tokens: int = 200,
        language: str = "en",
    ):
        self._llm = llm_manager
        self.enabled = enabled
        self.model = model
        self.max_tokens = max_tokens
        self.language = language

    def enhance(
        self,
        raw_prompt: str,
        persona: Optional[str] = None,
        context: Optional[Any] = None,
    ) -> str:
        """Усилить промт через LLM.

        Args:
            raw_prompt: исходный пользовательский промт.
            persona: имя персонажа (опционально).
            context: ConversationContext (опционально).

        Returns:
            Усиленный промт (или исходный, если enhancer выключен / ошибка).
        """
        if not self.enabled or not self._llm or not raw_prompt.strip():
            _log.debug("enhancer skipped (enabled=%s, has_llm=%s)", self.enabled, bool(self._llm))
            return raw_prompt

        system = _SYSTEM_PROMPT
        if self.language == "ru":
            system = system.replace("Язык: английский.", "Язык: русский.")

        # Добавляем контекст персонажа
        user_msg = raw_prompt
        if persona:
            try:
                from character_catalog import read_character_card  # type: ignore
                card = (read_character_card(str(persona)) or "")[:500]
                if card:
                    user_msg = f"Персонаж: {card}\nЗапрос: {raw_prompt}"
            except Exception:
                pass

        _log.debug("enhancing… len=%d", len(raw_prompt))

        try:
            if hasattr(self._llm, "chat_once"):
                result = self._llm.chat_once(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.3,
                    max_tokens=self.max_tokens,
                )
            elif hasattr(self._llm, "complete"):
                result = self._llm.complete(
                    f"{system}\n\nЗапрос: {user_msg}\n\nУлучшенный промт:",
                    temperature=0.3,
                    max_tokens=self.max_tokens,
                )
            else:
                _log.warning("LLM object has neither chat_once nor complete")
                return raw_prompt

            enhanced = (result or "").strip()
            if not enhanced:
                return raw_prompt

            # Убираем возможные кавычки-обёртки
            enhanced = enhanced.strip("\"'`«»")
            _log.debug("enhanced: %r → %r", raw_prompt[:60], enhanced[:60])
            return enhanced

        except Exception as e:
            _log.warning("enhancer error: %s", e)
            return raw_prompt


# ── Fast test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    enh = PromptEnhancer(enabled=False)  # без LLM — passthrough
    result = enh.enhance("a cat sitting on a windowsill")
    print(f"Input:  a cat sitting on a windowsill")
    print(f"Output: {result}")
    assert result == "a cat sitting on a windowsill", "Should be passthrough when disabled"
    print("OK")