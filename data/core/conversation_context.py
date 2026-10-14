# -*- coding: utf-8 -*-
"""Контекст диалога: история, тема, сущности, разрешение отсылок.

ConversationContext хранит последние N ходов диалога, извлекает сущности,
определяет текущую тему и разрешает местоимения / отсылки («это», «то же»,
«его», «как раньше») перед отправкой запроса в LLM.

Использование:
    ctx = ConversationContext()
    ctx.add(Turn("user", "нарисуй лису"))
    ctx.add(Turn("assistant", "🐺", image_refs=["generated/fox.png"]))
    resolved = ctx.resolve_reference("а теперь в стиле аниме")
    # → "а теперь нарисуй лису в стиле аниме"
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Logger ──────────────────────────────────────────────────────────────

_log = logging.getLogger("conversation_context")


# ── Датакласс хода ─────────────────────────────────────────────────────

@dataclass
class Turn:
    """Один ход диалога: сообщение пользователя или ответ ассистента."""
    role: str                            # user | assistant | system
    text: str
    timestamp: float = field(default_factory=time.time)
    entities: Dict[str, Any] = field(default_factory=dict)
    image_refs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Turn:
        return cls(**d)
# ── Контекст диалога ───────────────────────────────────────────────────

class ConversationContext:
    """Цепочка ходов с извлечением темы, сущностей и разрешением отсылок.

    Args:
        max_turns: максимум ходов в памяти (старые отбрасываются).
        max_tokens: максимум токенов (приблизительно по 4 символа на токен).
        session_dir: куда сохранять/грузить сессии.
    """

    # Паттерны для отсылок
    REFERENCE_PATTERNS = re.compile(
        r"\b("
        r"это|этого|этому|этим|этом|"
        r"то|того|тому|тем|том|"
        r"его|её|ее|их|"
        r"там|тогда|"
        r"то же|то же самое|такой же|такую же|такого же|"
        r"повтори|ещё раз|еще раз|"
        r"как раньше|как в прошлый раз|как тогда|"
        r"как на той|как на том|как на прошлой|"
        r"эту картинк|это изображен|этот файл|"
        r"загруженн|прикреплённ|прикрепленн|"
        r"используй как основ|возьми за основ"
        r")\b",
        re.I,
    )

    def __init__(
        self,
        max_turns: int = 20,
        max_tokens: int = 4000,
        session_dir: Optional[str] = None,
    ):
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self._turns: deque[Turn] = deque(maxlen=max_turns)
        self._session_id: Optional[str] = None
        self._session_dir = session_dir or "data/sessions"
        self._entity_store: Dict[str, Any] = {}   # накопленные сущности
        self._topic: str = ""

    # ── Основные операции ──────────────────────────────────────────────

    def add(self, turn: Turn) -> None:
        """Добавить ход и обновить тему/сущности."""
        self._turns.append(turn)
        self._extract_entities(turn)
        self._update_topic()
        self._trim_by_tokens()
        _log.debug("add turn: %s | topic=%s | turns=%d", turn.role[:4], self._topic, len(self._turns))

    def get_recent(self, n: int = 10) -> List[Turn]:
        """Последние n ходов."""
        return list(self._turns)[-n:]

    def get_all(self) -> List[Turn]:
        """Все ходы."""
        return list(self._turns)

    def clear(self) -> None:
        """Полный сброс контекста."""
        self._turns.clear()
        self._entity_store.clear()
        self._topic = ""
# ── Тема диалога ──────────────────────────────────────────────────

    def get_topic(self) -> str:
        """Текущая тема (сумма subject-ов последних 5 ходов с весом)."""
        return self._topic

    def get_entities(self) -> Dict[str, Any]:
        """Накопленные сущности за диалог."""
        return dict(self._entity_store)

    # ── Разрешение отсылок ────────────────────────────────────────────

    def resolve_reference(self, text: str) -> str:
        """Разрешает местоимения и отсылки в тексте пользователя.

        Ищет паттерны вида «это», «то же самое», «его» и подставляет
        последнюю подходящую сущность или последний subject.

        Returns:
            Текст с разрешёнными отсылками.
        """
        if not text or not self._turns:
            return text

        original = text
        low = text.lower().strip()

        # Если нет отсылок — возвращаем как есть
        if not self.REFERENCE_PATTERNS.search(low):
            return text

        # Собираем последние значимые сущности
        entities = self.get_entities()
        last_subject = entities.get("subject", "")
        last_action = entities.get("action", "")
        last_image = entities.get("last_image_ref", "")

        # Ищем последний ход пользователя с изображением
        for turn in reversed(self._turns):
            if turn.role == "user" and turn.image_refs:
                if not last_image:
                    last_image = turn.image_refs[0]
                break

        # Разрешаем конкретные отсылки
        replacements: List[tuple] = []

        # «это изображение», «эта картинка», «этот файл», «загруженное»
        if re.search(r"(эту? картинк|это изображен|этот файл|загруженн|прикреплённ|прикрепленн)", low):
            if last_image:
                replacements.append((
                    r"(эту? картинк\w*|это изображен\w*|этот файл|загруженн\w*|прикреплённ\w*|прикрепленн\w*)",
                    f"изображение ({last_image})"
                ))

        # «то же самое», «такой же»
        if re.search(r"\b(то же самое|то же|такой же|такую же|такого же)\b", low):
            target = last_subject or last_action or "это"
            replacements.append((r"\b(то же самое|то же|такой же|такую же|такого же)\b", target))

        # «повтори», «ещё раз»
        if re.search(r"\b(повтори|ещё раз|еще раз)\b", low):
            last_user_text = ""
            for turn in reversed(self._turns):
                if turn.role == "user":
                    last_user_text = turn.text
                    break
            if last_user_text:
                replacements.append((
                    r"\b(повтори|ещё раз|еще раз)\b",
                    f"повтори: {last_user_text}"
                ))
# «как раньше», «как в прошлый раз»
        if re.search(r"\b(как раньше|как в прошлый раз|как тогда)\b", low):
            last_user_text = ""
            for turn in reversed(self._turns):
                if turn.role == "user":
                    last_user_text = turn.text
                    break
            if last_user_text:
                replacements.append((
                    r"\b(как раньше|как в прошлый раз|как тогда)\b",
                    f"(как в предыдущем запросе: {last_user_text[:50]}...)"
                ))

        # «его», «её», «их» — подставляем последний subject
        if re.search(r"\b(его|её|ее|их)\b", low) and last_subject:
            replacements.append((r"\b(его|её|ее|их)\b", last_subject))

        # «это» в начале или孤立ное — подставляем subject
        if re.search(r"(?:^|\s)(это|этого|этому|этим|этом)(?:\s|$)", low) and last_subject:
            replacements.append((
                r"(?:^|\s)(это|этого|этому|этим|этом)(?:\s|$)",
                f" {last_subject} "
            ))

        # Применяем замены
        resolved = text
        for pattern, replacement in replacements:
            resolved = re.sub(pattern, replacement, resolved, flags=re.I)

        _log.debug("resolve: %r → %r", original, resolved)
        return resolved

    # ── Сохранение / загрузка сессии ──────────────────────────────────

    def save_session(self, session_id: str) -> None:
        """Сохранить контекст в JSON-файл сессии."""
        self._session_id = session_id
        sess_dir = Path(self._session_dir)
        sess_dir.mkdir(parents=True, exist_ok=True)
        path = sess_dir / f"{session_id}.json"
        data = {
            "turns": [t.to_dict() for t in self._turns],
            "entities": self._entity_store,
            "topic": self._topic,
        }
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            _log.debug("session saved: %s (%d turns)", path, len(self._turns))
        except OSError as e:
            _log.error("save session %s: %s", session_id, e)

    def load_session(self, session_id: str) -> bool:
        """Загрузить контекст из файла сессии. Вернёт True при успехе."""
        path = Path(self._session_dir) / f"{session_id}.json"
        if not path.is_file():
            _log.debug("session not found: %s", path)
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._turns.clear()
            for t in data.get("turns", []):
                self._turns.append(Turn.from_dict(t))
            self._entity_store = data.get("entities", {})
            self._topic = data.get("topic", "")
            self._session_id = session_id
            _log.debug("session loaded: %s (%d turns)", path, len(self._turns))
            return True
        except (OSError, json.JSONDecodeError, KeyError) as e:
            _log.error("load session %s: %s", session_id, e)
            return False
# ── Внутренние методы ─────────────────────────────────────────────

    def _extract_entities(self, turn: Turn) -> None:
        """Извлечь сущности из текста хода и накопить."""
        if turn.role != "user":
            return
        text = turn.text.lower()

        # subject — первое существительное после глагола
        subject = ""
        for pattern in [
            r"(?:нарисуй|сгенерируй|создай|изобрази|найди|покажи|сделай)\s+([а-яёa-z\s\-]{2,40}?)(?:\s+в\s+|\s+на\s+|\s+с\s+|$|\.)",
            r"(?:картинку|изображение|фото|арт)\s+([а-яёa-z\s\-]{2,40}?)(?:\s+в\s+|\s+на\s+|\s+с\s+|$|\.)",
            r"(?:про|о|об|обо)\s+([а-яёa-z\s\-]{2,40}?)(?:\s|$|\.)",
        ]:
            m = re.search(pattern, text)
            if m:
                subject = m.group(1).strip()
                break

        if subject:
            # Отбрасываем местоимения и служебные слова
            _skip_words = {
                "это", "этого", "этому", "этим", "этом",
                "его", "её", "ее", "их", "то", "того",
                "тому", "тем", "том", "ту", "там", "эту",
                "такой", "такую", "такого", "такая", "такие",
            }
            if subject.lower() in _skip_words:
                subject = ""  # не обновляем subject
            else:
                if subject in self._entity_store:
                    self._entity_store[subject] = self._entity_store.get(subject, 0) + 0.5
                else:
                    self._entity_store[subject] = 1.0
                self._entity_store["subject"] = subject

        # action
        for pattern in [
            r"(нарисуй|сгенерируй|создай|изобрази|найди|покажи|сделай|переделай|дорисуй|измени|добавь)",
        ]:
            m = re.search(pattern, text)
            if m:
                self._entity_store["action"] = m.group(1)
                break

        # image_refs
        if turn.image_refs:
            self._entity_store["last_image_ref"] = turn.image_refs[-1]

    _SKIP_TOPIC_WORDS = {
        "это", "этого", "этому", "этим", "этом",
        "его", "её", "ее", "их", "то", "того",
        "тому", "тем", "том", "ту", "там", "эту",
        "такой", "такую", "такого", "такая", "такие",
    }

    def _update_topic(self) -> None:
        """Определить тему как subject из последних 5 ходов с макс. весом."""
        recent = list(self._turns)[-5:]
        subjects: Dict[str, float] = defaultdict(float)
        for i, turn in enumerate(recent):
            weight = 1.0 + i * 0.2  # свежесть
            if turn.role == "user":
                for pattern in [
                    r"(?:нарисуй|сгенерируй|создай|изобрази|найди|покажи|сделай)\s+([а-яёa-z\s\-]{2,40}?)(?:\s+в\s+|\s+на\s+|\s+с\s+|$|\.)",
                ]:
                    m = re.search(pattern, turn.text.lower())
                    if m:
                        subj = m.group(1).strip()
                        if subj.lower() not in self._SKIP_TOPIC_WORDS:
                            subjects[subj] += weight
                        break
        if subjects:
            self._topic = max(subjects, key=subjects.get)
        else:
            self._topic = ""

    def _trim_by_tokens(self) -> None:
        """Отбросить старые ходы, если превышен лимит токенов."""
        total = sum(len(t.text) // 4 + len(t.image_refs) * 10 for t in self._turns)
        while total > self.max_tokens and len(self._turns) > 1:
            old = self._turns.popleft()
            total -= len(old.text) // 4 + len(old.image_refs) * 10

    def __len__(self) -> int:
        return len(self._turns)

    def __bool__(self) -> bool:
        return len(self._turns) > 0

    def __repr__(self) -> str:
        return (
            f"ConversationContext(turns={len(self._turns)}, "
            f"topic={self._topic!r}, entities={len(self._entity_store)})"
        )
# ── Вспомогательная сборка контекстного блока для промпта ─────────────

def build_context_block(ctx: ConversationContext) -> str:
    """Собрать блок [Контекст диалога] для вставки в system prompt.

    Args:
        ctx: контекст диалога.

    Returns:
        Строка вида:
        [Контекст диалога]
        Тема: <topic>
        Последние упомянутые объекты: <entities>
        Предыдущий запрос: <last_user_text>
    """
    if not ctx:
        return ""

    lines = ["[Контекст диалога]"]
    topic = ctx.get_topic()
    if topic:
        lines.append(f"Тема: {topic}")

    entities = ctx.get_entities()
    if entities:
        display = {k: v for k, v in entities.items()
                   if k not in ("subject", "action", "last_image_ref")}
        if display:
            lines.append(f"Объекты: {', '.join(f'{k}={v}' for k, v in display.items())}")

    # Последние 3 хода пользователя (кратко)
    user_turns = [t for t in ctx.get_recent(6) if t.role == "user"]
    if user_turns:
        last_text = user_turns[-1].text[:200]
        lines.append(f"Предыдущий запрос: {last_text}")

    # Есть ли изображения в контексте
    has_images = any(t.image_refs for t in ctx.get_recent(10))
    if has_images:
        lines.append("Изображения: есть в истории")

    return "\n".join(lines)
# ── Fast test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    ctx = ConversationContext(max_turns=10)

    # Симуляция диалога
    ctx.add(Turn("user", "нарисуй лису"))
    ctx.add(Turn("assistant", "🐺", image_refs=["generated/fox.png"]))
    ctx.add(Turn("user", "а теперь сделай её в стиле аниме"))

    resolved = ctx.resolve_reference("а теперь сделай её в стиле аниме")
    print(f"Исходно:  а теперь сделай её в стиле аниме")
    print(f"Разрешено: {resolved}")
    print(f"Тема: {ctx.get_topic()}")
    print(f"Сущности: {ctx.get_entities()}")
    print()

    # Тест 2: отсылка к изображению
    ctx2 = ConversationContext()
    ctx2.add(Turn("user", "вот фото заката", image_refs=["uploads/sunset.jpg"]))
    resolved2 = ctx2.resolve_reference("используй это изображение как основу")
    print(f"Исходно:  используй это изображение как основу")
    print(f"Разрешено: {resolved2}")