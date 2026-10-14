# -*- coding: utf-8 -*-
"""FeedbackParser — распознавание реакции пользователя на выбор.

Определяет сигнал обратной связи из текста и/или эмодзи:
    👍 «нравится»  → +1.0
    👎 «не то»     → -1.0
    «давай другое» → -0.5 + повторный pick
    «покажи ещё»   → +0.5 + top-N похожих
    молчание       → +0.1 (слабое положительное)

Используется PreferenceMemory.record_choice(explicit_feedback=...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

_POS_WORDS = (
    "нравит", "супер", "отлично", "идеально", "замечательно",
    "классно", "красиво", "прекрасно", "люблю", "огонь", "топ",
)
_NEG_WORDS = (
    "не нрав", "не то", "ненавиж", "ужас", "отврат", "плохо",
    "не подходит", "фу", "гадость", "некрасиво",
)
_AGAIN_WORDS = (
    "ещё", "еще", "другое", "другую", "другой", "покажи ещё",
    "давай другое", "смени", "замени",
)
_SIMILAR_WORDS = (
    "похоже", "подобное", "такое же", "такую же", "в том же",
    "ещё такое", "ещё похожее", "аналогичн",
)
_RESET_WORDS = (
    "забудь", "сброс", "сбрось", "очисти", "заново", "начни с чистого листа",
)

# Эмодзи-сигналы
_EMOJI = {
    "👍": 1.0,
    "❤": 1.2,
    "🔥": 1.2,
    "😍": 1.2,
    "🙂": 0.4,
    "😐": 0.0,
    "👎": -1.0,
    "😠": -1.2,
    "🤮": -1.2,
    "💔": -1.0,
}


@dataclass
class Feedback:
    """Разобранная обратная связь."""
    signal: float = 0.0                     # сила: -1..+1 (0 = не распознано)
    kind: str = ""                           # positive | negative | again | similar | reset
    want_another: bool = False               # «давай другое»
    want_similar: bool = False               # «покажи ещё похожее»
    want_reset: bool = False                 # «забудь предпочтения»
    text: str = ""                           # исходный текст


class FeedbackParser:
    """Парсер реакции пользователя."""

    # Русские эмодзи могут прийти как буквы, поэтому словарь выше.

    def parse(self, text: str, silent_seconds: Optional[float] = None) -> Feedback:
        """Разобрать текст/эмодзи в Feedback.

        Args:
            text: текст реакции («нравится», «👍», ...).
            silent_seconds: если > 30 и нет текста — слабое «+».

        Returns:
            Feedback с сигналом.
        """
        fb = Feedback(text=(text or "").strip())
        low = (text or "").strip().lower()
        if not low:
            if silent_seconds is not None and silent_seconds >= 30:
                return Feedback(signal=0.1, kind="positive", text="(молчание)")
            return fb

        # 1. Эмодзи
        for emoji, val in _EMOJI.items():
            if emoji in low:
                fb.signal = val
                fb.kind = "positive" if val > 0 else "negative"
                self._apply_meta(fb, low)
                return fb

        # 2. Сброс
        for w in _RESET_WORDS:
            if w in low:
                fb.want_reset = True
                fb.kind = "reset"
                return fb

        # 3. Негатив
        for w in _NEG_WORDS:
            if w in low:
                fb.signal = -1.0
                fb.kind = "negative"
                self._apply_meta(fb, low)
                return fb

        # 4. Позитив
        for w in _POS_WORDS:
            if w in low:
                fb.signal = 1.0
                fb.kind = "positive"
                self._apply_meta(fb, low)
                return fb

        # 5. «Ещё похожее» (похожее важнее «другого», проверяем первым)
        for w in _SIMILAR_WORDS:
            if w in low:
                fb.signal = 0.5
                fb.kind = "similar"
                fb.want_similar = True
                return fb

        # 6. «Другое» — слабый минус + повторный pick
        for w in _AGAIN_WORDS:
            if w in low:
                fb.signal = -0.5
                fb.kind = "again"
                fb.want_another = True
                return fb

        return fb

    @staticmethod
    def _apply_meta(fb: Feedback, low: str) -> None:
        """Добавить мета-флаги из текста (ещё/другое) к знаку."""
        for w in _SIMILAR_WORDS:
            if w in low:
                fb.want_similar = True
                fb.kind = "similar"
                return
        for w in _AGAIN_WORDS:
            if w in low:
                fb.want_another = True
                fb.kind = "again"
                return

    def parse_and_apply(
        self,
        text: str,
        memory: Any,
        silent_seconds: Optional[float] = None,
    ) -> Feedback:
        """Разобрать реакцию и сразу применить к PreferenceMemory.

        Возвращает Feedback для дальнейшего действия (повторный pick и т.д.).
        """
        fb = self.parse(text, silent_seconds)
        if fb.kind == "reset":
            memory.reset()
        elif fb.signal != 0.0:
            memory.record_choice(
                chosen=memory.last_choice if hasattr(memory, "last_choice") else None,
                explicit_feedback=text,
            )
        return fb