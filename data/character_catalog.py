# -*- coding: utf-8 -*-
"""Персонажи на диске: personas/characters/<id>/."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Optional
import config

_card_cache: Dict[str, str] = {}


def invalidate_card_cache() -> None:
    _card_cache.clear()


def characters_root() -> Path:
    base = Path(getattr(config, "DATA_DIR", Path(__file__).resolve().parent))
    return base / "personas" / "characters"


def list_character_ids() -> List[str]:
    root = characters_root()
    if not root.is_dir():
        return []
    ids = []
    for p in sorted(root.iterdir()):
        if not p.is_dir() or p.name.startswith("_"):
            continue
        # есть хотя бы md или любая файлы
        ids.append(p.name)
    return ids


def character_dir(character_id: str) -> Path:
    return characters_root() / character_id


def character_card_path(character_id: str) -> Optional[Path]:
    d = character_dir(character_id)
    if not d.is_dir():
        return None
    # <id>.md или любой .md
    direct = d / f"{character_id}.md"
    if direct.is_file():
        return direct
    mds = sorted(d.glob("*.md"))
    return mds[0] if mds else None


def read_character_card(character_id: str, max_chars: int = 12000) -> str:
    cid = str(character_id or "")
    if cid in _card_cache:
        return _card_cache[cid][:max_chars]
    path = character_card_path(cid)
    if not path:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"character card {cid}: {e}", flush=True)
        return ""
    _card_cache[cid] = text
    return text[:max_chars]


def character_meta(character_id: str) -> Dict[str, Any]:
    d = character_dir(character_id)
    card = character_card_path(character_id)
    title = character_id
    if card and card.is_file():
        try:
            first = card.read_text(encoding="utf-8", errors="replace").splitlines()[:5]
            for line in first:
                line = line.strip()
                if line.startswith("#"):
                    title = line.lstrip("#").strip() or title
                    break
        except Exception:
            pass
    return {
        "id": character_id,
        "title": title,
        "path": str(d),
        "card": str(card) if card else None,
        "has_avatar": (d / "avatar").is_dir() or (d / "frames").is_dir(),
    }


def list_characters_meta() -> List[Dict[str, Any]]:
    return [character_meta(i) for i in list_character_ids()]


def ensure_default_character() -> str:
    """Если папок нет — создать заготовку default."""
    root = characters_root()
    root.mkdir(parents=True, exist_ok=True)
    ids = list_character_ids()
    if ids:
        return ids[0]
    d = root / "default"
    d.mkdir(parents=True, exist_ok=True)
    (d / "default.md").write_text(
        "# Default\n\nБазовый персонаж ядра. Замените карточку или добавьте папку personas/characters/<имя>/\n",
        encoding="utf-8",
    )
    (d / "memory").mkdir(exist_ok=True)
    (d / "plugin_data").mkdir(exist_ok=True)
    return "default"
