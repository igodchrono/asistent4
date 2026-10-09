# -*- coding: utf-8 -*-
"""Персонажи на диске: personas/characters/<id>/."""
from __future__ import annotations
import json
import shutil
from datetime import datetime
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


# ── Управление файлами персонажа ──────────────────────────────────────

def reset_character(character_id: str) -> str:
    """Полный сброс персонажа: очистка памяти, скриншотов, сгенерированных
    картинок, заметок, сохранённых состояний. Карточка и аватар-спрайты НЕ трогаются."""
    d = character_dir(character_id)
    if not d.is_dir():
        return f"Папка персонажа {character_id} не найдена"
    errors = []
    deleted = []

    # 1. memory.db
    mem_db = d / "memory" / "memory.db"
    if mem_db.is_file():
        try:
            mem_db.unlink()
            deleted.append("memory.db (история, факты, дневник)")
        except Exception as e:
            errors.append(f"memory.db: {e}")

    # 2. generated/character_id (скриншоты + сгенерированные картинки)
    data_dir = Path(getattr(config, "DATA_DIR", Path(__file__).resolve().parent))
    generated_dir = data_dir / "generated" / character_id
    if generated_dir.is_dir():
        try:
            shutil.rmtree(str(generated_dir))
            deleted.append(f"generated/{character_id}/ (скриншоты, генерации)")
        except Exception as e:
            errors.append(f"generated: {e}")

    # 3. ref.png — стереть, чтобы персонаж не помнил себя
    ref_path = d / "images" / "ref.png"
    if ref_path.is_file():
        try:
            ref_path.unlink()
            deleted.append("images/ref.png (референс внешности)")
        except Exception as e:
            errors.append(f"ref.png: {e}")
    # ref.* других форматов
    for stem in ("ref", "reference"):
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            p = d / "images" / f"{stem}{ext}"
            if p.is_file():
                try:
                    p.unlink()
                    deleted.append(f"images/{stem}{ext}")
                except Exception as e:
                    errors.append(f"{stem}{ext}: {e}")

    # 4. notes.md — очистить
    notes = d / "notes.md"
    if notes.is_file():
        try:
            notes.write_text("", encoding="utf-8")
            deleted.append("notes.md (заметки)")
        except Exception as e:
            errors.append(f"notes.md: {e}")

    # 5. user_patterns.json — очистить
    patterns = d / "user_patterns.json"
    if patterns.is_file():
        try:
            patterns.write_text("{}", encoding="utf-8")
            deleted.append("user_patterns.json")
        except Exception as e:
            errors.append(f"user_patterns.json: {e}")

    # 6. emotional_memory.json — очистить
    em = d / "emotional_memory.json"
    if em.is_file():
        try:
            em.write_text("[]", encoding="utf-8")
            deleted.append("emotional_memory.json")
        except Exception as e:
            errors.append(f"emotional_memory.json: {e}")

    # 7. plugin_data/ — удалить
    plugin_data = d / "plugin_data"
    if plugin_data.is_dir():
        try:
            shutil.rmtree(str(plugin_data))
            deleted.append("plugin_data/ (данные плагинов)")
        except Exception as e:
            errors.append(f"plugin_data: {e}")

    # 8. память о референсе в app.state сбрасывается при рестарте
    invalidate_card_cache()

    # сбросить также _card_cache для этого персонажа
    if character_id in _card_cache:
        del _card_cache[character_id]

    result = "✅ Персонаж сброшен к заводским настройкам.\n"
    if deleted:
        result += "Удалено/очищено:\n  " + "\n  ".join(deleted)
    if errors:
        result += "\n\n⚠️ Ошибки:\n  " + "\n  ".join(errors)
    return result


def backup_character(character_id: str) -> str:
    """Сохранить резервную копию всей папки персонажа."""
    src = character_dir(character_id)
    if not src.is_dir():
        return f"Папка персонажа {character_id} не найдена"
    data_dir = Path(getattr(config, "DATA_DIR", Path(__file__).resolve().parent))
    backup_root = data_dir / "personas" / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dst = backup_root / f"{character_id}_{stamp}"
    try:
        shutil.copytree(str(src), str(dst))
        return f"✅ Резервная копия сохранена:\n{dst}"
    except Exception as e:
        return f"❌ Ошибка при создании копии: {e}"


def set_character_reference(character_id: str, image_path: str) -> str:
    """Скопировать изображение как ref.png персонажа."""
    src = Path(image_path)
    if not src.is_file():
        return "Файл не найден"
    d = character_dir(character_id)
    img_dir = d / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    dest = img_dir / "ref.png"
    try:
        dest.write_bytes(src.read_bytes())
        invalidate_card_cache()
        return f"✅ Референс сохранён: {dest}"
    except Exception as e:
        return f"❌ Ошибка сохранения: {e}"


def has_character_reference(character_id: str) -> Optional[Path]:
    """Проверить, есть ли у персонажа ref.png."""
    d = character_dir(character_id)
    img_dir = d / "images"
    if not img_dir.is_dir():
        return None
    for stem in ("ref", "reference"):
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            p = img_dir / f"{stem}{ext}"
            if p.is_file():
                return p
    return None
