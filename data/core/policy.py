# -*- coding: utf-8 -*-
"""Флаг nsfw с карточки — только для информации.

Ответственность за контент-политику полностью перенесена в LLM.
См. data/CONTENT_POLICY.md
"""

from core._guard import (  # noqa: F401
    character_is_nsfw,
    character_policy,
    parse_character_policy,
)
