# -*- coding: utf-8 -*-
"""GenerationHistory — история генераций изображений.

Сохраняет КАЖДУЮ генерацию (включая промежуточные: fallback, retry,
variations) в data/generations/history.jsonl — одна запись на строку.
В памяти сессии держится тот же список для быстрого доступа
(используется модулем «то же самое» в генераторе картинок).

Файлы:
  data/generations/history.jsonl   — все генерации (JSONL)
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


_log = logging.getLogger("core.generation_history")

_ROOT = Path(__file__).resolve().parents[1]
_HISTORY_PATH = _ROOT / "generations" / "history.jsonl"


@dataclass
class GenerationRecord:
    """Одна запись истории генерации.

    Поля refs/output_path хранятся строками (так они уходят в JSONL);
    для прикладного кода есть helpers: ref_paths() и output_file.
    """
    id: str
    timestamp: float
    prompt: str                       # финальный промт, ушедший в генератор
    negative_prompt: str = ""
    workflow: str = "t2i"             # t2i | edit | i2i | ipadapter | ...
    refs: List[str] = field(default_factory=list)      # пути-строки
    output_path: str = ""             # куда сохранили картинку
    seed: Optional[int] = None
    params: Dict[str, Any] = field(default_factory=dict)  # steps, cfg, resolution
    request: str = ""                 # исходный запрос пользователя
    kind: str = ""                    # plain | self | cont | edit | ...
    source: str = ""                  # откуда пришла генерация

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GenerationRecord":
        d = d or {}
        return cls(
            id=str(d.get("id") or f"gen_{int(d.get('timestamp') or time.time())}"),
            timestamp=float(d.get("timestamp") or time.time()),
            prompt=str(d.get("prompt") or ""),
            negative_prompt=str(d.get("negative_prompt") or d.get("negative") or ""),
            workflow=str(d.get("workflow") or "t2i"),
            refs=[str(r) for r in (d.get("refs") or [])],
            output_path=str(d.get("output_path") or d.get("image_path") or ""),
            seed=d.get("seed"),
            params=dict(d.get("params") or {}),
            request=str(d.get("request") or ""),
            kind=str(d.get("kind") or ""),
            source=str(d.get("source") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "workflow": self.workflow,
            "refs": list(self.refs or []),
            "output_path": self.output_path,
            "seed": self.seed,
            "params": dict(self.params or {}),
            "request": self.request,
            "kind": self.kind,
            "source": self.source,
        }

    def ref_paths(self) -> List[Path]:
        return [Path(r) for r in (self.refs or [])]

    @property
    def output_file(self) -> Optional[Path]:
        return Path(self.output_path) if self.output_path else None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<GenerationRecord {self.id} wf={self.workflow} seed={self.seed} out={self.output_path or '-'}>"


class GenerationHistory:
    """Журнал генераций: JSONL-файл + копия в памяти.

    Потокобезопасен (RLock): imggen пишет из фоновых потоков.
    """

    def __init__(self, storage: Optional[Path] = None,
                 history_path: Optional[Path] = None):
        # history_path — обратная совместимость со старым именем параметра
        self._path = Path(storage) if storage else (Path(history_path) if history_path else _HISTORY_PATH)
        self._lock = threading.RLock()
        self._entries: List[GenerationRecord] = self._load()

    # ── Запись ────────────────────────────────────────────────────────

    def add(self, record: GenerationRecord) -> GenerationRecord:
        """Добавить запись о генерации (в память и в JSONL)."""
        with self._lock:
            if not record.prompt and not record.output_path:
                return record
            self._entries.append(record)
            self._save(record)
            _log.debug("gen #%d: %s seed=%s %r",
                       len(self._entries), record.id, record.seed, record.prompt[:40])
        return record

    def record(self,
               prompt: str = "",
               negative: str = "",
               seed: Optional[int] = None,
               bundle: Any = None,
               image_path: Optional[str] = None,
               metrics: Optional[Dict[str, Any]] = None,
               feedback: str = "",
               style: str = "",
               quality: str = "",
               extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Обратносовместимая запись (используется prompt_pipeline).

        Возвращает dict (как раньше).
        """
        entry: Dict[str, Any] = {}
        if metrics:
            entry["metrics"] = metrics
        if feedback:
            entry["feedback"] = feedback
        if style:
            entry["style"] = style
        if quality:
            entry["quality"] = quality
        if extra:
            entry.update(extra)
        if bundle is not None:
            if not seed:
                seed = getattr(bundle, "seed", None)
            if not style:
                style = getattr(bundle, "style_preset", "") or ""
            if not quality:
                quality = getattr(bundle, "quality_preset", "") or ""
            if not negative and getattr(bundle, "negative", None):
                negative = bundle.negative
        now = time.time()
        record = GenerationRecord(
            id=f"gen_{int(now)}",
            timestamp=now,
            prompt=prompt,
            negative_prompt=negative,
            workflow=str((extra or {}).get("workflow") or "t2i"),
            refs=[str(r) for r in ((extra or {}).get("refs") or [])],
            output_path=str(image_path or ""),
            seed=int(seed) if seed else None,
            params=dict(entry.pop("params", {}) or {}),
            request=str((extra or {}).get("request") or ""),
            kind=str((extra or {}).get("kind") or ""),
        )
        self.add(record)
        d = record.to_dict()
        d.update(entry)
        return d

    # ── Чтение ────────────────────────────────────────────────────────

    def last(self) -> Optional[GenerationRecord]:
        with self._lock:
            if not self._entries:
                return None
            return self._entries[-1]

    def last_n(self, n: int = 5) -> List[GenerationRecord]:
        with self._lock:
            return list(self._entries[-max(int(n), 0):]) if self._entries else []

    def find_by_query(self, query: str) -> List[GenerationRecord]:
        """Поиск по подстроке в промте/запросе/id/kind/workflow."""
        q = (query or "").lower()
        if not q:
            return []
        out: List[GenerationRecord] = []
        with self._lock:
            for r in reversed(self._entries):
                hay = f"{r.prompt} {r.request} {r.id} {r.kind} {r.workflow}".lower()
                if q in hay:
                    out.append(r)
        return out

    def last_image(self) -> Optional[GenerationRecord]:
        """Последняя запись, у которой есть реальный файл картинки."""
        with self._lock:
            for r in reversed(self._entries):
                if r.output_path and Path(r.output_path).is_file():
                    return r
        return None

    def get_by_seed(self, seed: int) -> Optional[GenerationRecord]:
        with self._lock:
            for r in reversed(self._entries):
                if r.seed == seed:
                    return r
        return None

    def all(self) -> List[GenerationRecord]:
        with self._lock:
            return list(self._entries)

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    # ── Внутреннее ────────────────────────────────────────────────────

    def _load(self) -> List[GenerationRecord]:
        if not self._path.is_file():
            return []
        out: List[GenerationRecord] = []
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(GenerationRecord.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue
        except Exception as e:
            _log.warning("load history: %s", e)
        return out

    def _save(self, record: GenerationRecord) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(self._path), "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            _log.warning("save history: %s", e)


# Singleton для application-wide использования
_shared: Optional[GenerationHistory] = None


def get_history() -> GenerationHistory:
    global _shared
    if _shared is None:
        _shared = GenerationHistory()
    return _shared


def reset_history() -> None:
    """Сброс синглтона (для тестов)."""
    global _shared
    _shared = None


if __name__ == "__main__":
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "h.jsonl"
    h = GenerationHistory(tmp)
    h.add(GenerationRecord("gen_1", time.time(), "a cat on a chair", workflow="t2i",
                           output_path="/tmp/1.png", seed=12345))
    h.add(GenerationRecord("gen_2", time.time(), "fox girl in black lace lingerie", workflow="i2i",
                           output_path="/tmp/2.png", refs=["/tmp/1.png"], seed=12346))
    print("count:", h.count())
    print("last:", h.last())
    print("last_n(1):", h.last_n(1)[0].id)
    print("find 'lace':", [r.id for r in h.find_by_query("lace")])
    legacy = h.record("legacy prompt", negative="lowres", seed=7, image_path="/tmp/3.png")
    print("legacy record dict keys:", sorted(legacy.keys()))
    tmp.unlink(missing_ok=True)
    print("OK")
