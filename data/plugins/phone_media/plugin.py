# -*- coding: utf-8 -*-
"""Генератор картинок: Qwen-Image 2.1. Текст → t2i, референс → edit.

Промпт пишется один раз под выбранный граф и сразу уходит в ComfyUI.
"""
from __future__ import annotations

import copy
import json
import mimetypes
import random
import re
import asyncio
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from core.plugin_api import AppContext, HookResult, Plugin, SettingField

_ASK = (
    "сгенерируй", "сгенерировать", "нарисуй", "нарисовать",
    "сделай картин", "сделай изображ", "сделай арт",
    "создай картин", "изобрази", "представь в виде",
    "сгенери изображение", "сгенерируй изображение", "сгенерируй картин",
    "нарисуй мне", "draw ", "generate an image", "generate a picture", "create an image",
)
_ALIASES = {
    "t2i": "workflows/qwen21_t2i.json",
    "текст": "workflows/qwen21_t2i.json",
    "qwen": "workflows/qwen21_t2i.json",
    "qwen21": "workflows/qwen21_t2i.json",
    "qwen_image": "workflows/qwen21_t2i.json",
    "edit": "workflows/qwen21_edit.json",
    "правка": "workflows/qwen21_edit.json",
    "qwen_edit": "workflows/qwen21_edit.json",
    "референс": "workflows/qwen21_edit.json",
}
_EDIT_WORDS = (
    "правк", "измени", "переделай", "отредактир", "поправь", "замени",
    "дорисуй", "перекрас", "референс", "по фото", "по этому фото",
    "это фото", "это изображ", "<image",
)


class PluginImpl(Plugin):
    id = "phone_media"
    name = "Генератор картинок"
    version = "5.0.0"
    description = "Qwen-Image 2.1: текст или правка референса"
    settings_tab = "own"
    settings_tab_title = "Генератор"
    settings_schema = [
        SettingField("enabled", "Включить", "bool", True),
        SettingField("comfy_url", "ComfyUI", "str", "http://127.0.0.1:8188"),
        SettingField("timeout_sec", "Таймаут сек", "int", 900, min_value=60, max_value=900),
        SettingField("steps", "Steps (если есть KSampler)", "int", 8, min_value=4, max_value=60),
        SettingField("width", "Ширина latent", "int", 1088, min_value=512, max_value=2048),
        SettingField("height", "Высота latent", "int", 1808, min_value=512, max_value=2048),
        SettingField("positive_prefix", "Префикс промпта", "str", "masterpiece, best quality, anime"),
        SettingField("default_workflow", "Workflow по умолчанию", "str", "qwen"),
        SettingField("ask_workflow", "Спрашивать движок каждый раз", "bool", True),
    ]

    def __init__(self):
        self.app = None
        self._ui: Dict[str, Any] = {}
        self._busy = False

    def on_load(self, app: AppContext) -> None:
        self.app = app
        app.state.setdefault("imggen_stage", "idle")
        print("🖼 imggen 6.0: qwen-image 2.1 t2i / edit", flush=True)

    def register_tools(self, app: AppContext) -> None:
        app.tools["generate_image"] = self.tool_generate

    def setup_settings_tab(self, tab, app: AppContext) -> bool:
        from PyQt5 import QtWidgets
        lay = QtWidgets.QVBoxLayout(tab)
        en = QtWidgets.QCheckBox("Включить генератор")
        en.setChecked(bool(app.get_plugin_setting(self.id, "enabled", True)))
        ask = QtWidgets.QCheckBox("Если приложено фото и неясно — спросить: текст или правка")
        ask.setChecked(bool(app.get_plugin_setting(self.id, "ask_workflow", True)))
        lay.addWidget(en)
        lay.addWidget(ask)
        url = QtWidgets.QLineEdit(str(app.get_plugin_setting(self.id, "comfy_url", "http://127.0.0.1:8188")))
        pref = QtWidgets.QLineEdit(str(app.get_plugin_setting(self.id, "positive_prefix", "")))
        dflt = QtWidgets.QComboBox()
        dflt.setEditable(True)
        for n in ("t2i", "edit"):
            dflt.addItem(n)
        dflt.setEditText(str(app.get_plugin_setting(self.id, "default_workflow", "t2i")))
        tout = QtWidgets.QSpinBox(); tout.setRange(60, 900)
        tout.setValue(int(app.get_plugin_setting(self.id, "timeout_sec", 900) or 900))
        form = QtWidgets.QFormLayout()
        form.addRow("ComfyUI", url)
        form.addRow("По умолчанию", dflt)
        form.addRow("Ждать сек", tout)
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QLabel(
            "Только два графа:\n"
            "qwen21_t2i.json — картинка по тексту\n"
            "qwen21_edit.json — правка прикреплённого фото"
        ))
        self._ui = dict(enabled=en, ask_workflow=ask, comfy_url=url, positive_prefix=pref,
                        default_workflow=dflt, timeout_sec=tout)
        lay.addStretch(1)
        return True

    def collect_settings_tab(self) -> Dict[str, Any]:
        u = self._ui
        return {
            "enabled": u["enabled"].isChecked(),
            "ask_workflow": u["ask_workflow"].isChecked(),
            "comfy_url": u["comfy_url"].text().strip(),
            "positive_prefix": u["positive_prefix"].text().strip(),
            "default_workflow": u["default_workflow"].currentText().strip() or "t2i",
            "timeout_sec": int(u["timeout_sec"].value()),
        }

    def on_user_message(self, text, app):
        if not app.get_plugin_setting(self.id, "enabled", True):
            return None
        low = (text or "").strip().lower().replace("ё", "е")
        low = low.replace("этоты", "это ты").replace("это-ты", "это ты")
        if not low:
            return None
        stage = str(app.state.get("imggen_stage") or "idle")
        age = time.time() - float(app.state.get("imggen_at") or 0)
        if stage != "idle" and age > 600:
            app.state["imggen_stage"] = "idle"
            stage = "idle"

        if self._wants_show_ref(low):
            return HookResult(True, self._show_character_ref(app))
        if self._is_self_remember(low):
            return HookResult(True, self._save_character_ref(app))

        if stage == "drafting" and age < 25:
            if self._has_word(low, "отмена", "стоп") or "не надо" in low:
                app.state["imggen_stage"] = "idle"
                app.state["imggen_job"] = 0
                return HookResult(True, "ок, остановила сборку промпта.")
            return HookResult(True, "уже собираю один промпт, не пересобираю.")
        if stage == "await_model":
            if self._has_word(low, "отмена", "стоп") or "не надо" in low:
                app.state["imggen_stage"] = "idle"
                return HookResult(True, "ок, не рисую.")
            choice = low.strip(" .!")
            fam = ""
            if choice in ("1", "текст", "t2i", "новая") or choice.startswith("1 ") or choice.startswith("1."):
                fam = "t2i"
            elif choice in ("2", "правка", "edit", "референс") or choice.startswith("2 ") or choice.startswith("2.") or "правк" in choice:
                fam = "edit"
            else:
                picked = self._match_choice(low, self._choices())
                if picked is not None and picked.exists():
                    fam = self._family(picked.stem)
            if not fam:
                return HookResult(True, "напиши 1 (только текст) или 2 (правка фото).")
            req = str(app.state.get("imggen_request") or text)
            refs_now = self._refs(app) or list(app.state.get("imggen_refs") or [])
            if fam == "edit" and not refs_now:
                return HookResult(True, "для правки сначала прикрепи фото.")
            self._begin_one(app, req, wf=fam)
            label = "правка" if fam == "edit" else "текст"
            return HookResult(True, f"{label}. один промпт и сразу в генерацию.")
        if stage == "await_prompt":
            if self._has_word(low, "отмена", "стоп") or "не надо" in low:
                app.state["imggen_stage"] = "idle"
                return HookResult(True, "ок, не рисую.")
            return HookResult(True, self._after_scene(app, text))
        if stage == "confirm" and not (self._is_prompt_revise(low) and self._has_recent_gen(app)):
            return self._handle_confirm(app, text, low)
        if stage == "busy":
            if self._has_word(low, "отмена", "стоп") or "стоп генерац" in low or "не надо картин" in low:
                app.state["imggen_stage"] = "idle"
                app.state["imggen_job"] = 0
                return HookResult(True, "ок, не жду эту картинку. можно просить новую.")
            if not self._is_prompt_revise(low) and not any(k in low for k in _ASK):
                return None

        if self._is_prompt_revise(low) and self._has_recent_gen(app):
            return HookResult(True, self._revise(app, text))

        if any(k in low for k in _ASK):
            scene = self._user_scene(text)
            wf = self._pick_workflow_name(low)
            if wf:
                app.state["imggen_workflow_hint"] = wf
            if len(scene) < 8:
                app.state["imggen_stage"] = "await_prompt"
                app.state["imggen_request"] = text  # сохранить исходный запрос для контекста персонажа
                app.state["imggen_at"] = time.time()
                if wf:
                    app.state["imggen_workflow_hint"] = wf
                return HookResult(True, "что рисуем? опиши сцену. фото нужно только если это правка.")
            return HookResult(True, self._after_scene(app, text))
        return None

    @staticmethod
    def _has_word(text: str, *words: str) -> bool:
        t = text or ""
        for w in words:
            if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", t, flags=re.I):
                return True
        return False

    def _begin_one(self, app, raw: str, wf: str = "", refs: Optional[List[str]] = None, kind: str = "") -> None:
        """Один промпт от модели и сразу очередь ComfyUI. Без второй сборки."""
        low = (raw or "").lower()
        if not wf:
            wf = self._pick_workflow_name(low) or str(app.state.get("imggen_workflow_hint") or "")
        wf = "edit" if self._family(wf) == "edit" else "t2i"
        if "\nCHANGE:\n" not in (raw or ""):
            app.state["imggen_origin"] = raw
        if refs is None:
            if wf == "edit":
                refs = self._refs(app) or list(app.state.get("imggen_refs") or [])
            else:
                refs = self._attached(app)
        name, _look = self._char_look(app)
        about = self._about_character(f"{raw}\n{app.state.get('imggen_request') or ''}", name)
        if about and not self._wants_fresh(low) and kind != "edit":
            char_ref = self._character_ref_img(app)
            if char_ref and not (self._wants_edit(low) and self._attached(app) and kind != "self"):
                # Не затирать screen-ref (пришёл из _after_scene с source=last)
                has_screen_ref = bool(refs) and any(
                    Path(str(r)).stem.startswith(("pick_", "gen_")) for r in refs
                )
                if not has_screen_ref:
                    wf = "edit"
                    refs = [char_ref]
                    kind = "self"
        print(f"imggen: route wf={wf} kind={kind or '-'} about={about} refs={list(refs or [])[:1]}", flush=True)
        job = time.time()
        app.state["imggen_request"] = raw
        app.state["imggen_refs"] = list(refs or [])
        app.state["imggen_ref_kind"] = kind or ("edit" if wf == "edit" else "")
        app.state["imggen_stage"] = "drafting"
        app.state["imggen_at"] = time.time()
        app.state["imggen_workflow_hint"] = ""
        app.state["imggen_last_wf"] = wf
        app.state["imggen_job"] = job
        self._schedule_one(app, raw, refs, wf, job)

    def _choices(self) -> List[Path]:
        folder = Path(__file__).resolve().parent / "workflows"
        out: List[Path] = []
        if not folder.is_dir():
            return out
        for p in sorted(folder.glob("*.json")):
            try:
                if self._is_stub_wf(p):
                    continue
            except Exception:
                continue
            out.append(p)
        return out

    def _default_path(self, paths: List[Path]) -> Optional[Path]:
        if not paths:
            return None
        want = str(self.app.get_plugin_setting(self.id, "default_workflow", "") or "").strip().lower() if self.app else ""
        for p in paths:
            if want and want in (p.stem.lower(), self._family(p.stem)):
                return p
        return paths[0]

    def _model_menu(self, paths: List[Path]) -> str:
        if not paths:
            return "в папке workflows/ нет живых графов."
        lines = ["Каким графом рисовать? Напиши номер или имя файла:"]
        for i, p in enumerate(paths, 1):
            lines.append(f"{i}. {p.stem}")
        return "\n".join(lines)

    def _match_choice(self, low: str, paths: List[Path]) -> Optional[Path]:
        text = (low or "").strip().lower()
        if not text or not paths:
            return None
        m = re.match(r"^(\d+)\b", text)
        if m:
            i = int(m.group(1))
            if 1 <= i <= len(paths):
                return paths[i - 1]
        named = self._pick_workflow_name(text)
        if named:
            resolved = self._resolve_wf(named)
            if resolved.exists() and not self._is_stub_wf(resolved):
                return resolved
        for p in paths:
            stem = p.stem.lower()
            if stem == text or stem in text or stem.replace("_", " ") in text:
                return p
        return None

    def _wants_edit(self, low: str) -> bool:
        low = low or ""
        if any(k in low for k in ("промпт", "промт")):
            return False
        return any(k in low for k in _EDIT_WORDS)

    def _is_refer_to_last(self, low: str, app) -> bool:
        """Пользователь ссылается на последнее изображение (screen pick / gen)."""
        if not low:
            return False
        # явные отсылки к «этому» как к объекту, а не части речи
        refer = any(w in low for w in (
            "нарисуй это", "нарисуй то же", "нарисуй такое",
            "сделай такое", "сделай такую", 
            "похож", "подобн",
            "на основе этого", "из этого",
            "как на экран", "с экрана", "с монитора",
            "то же", "такое же", "такой же",
        ))
        if not refer:
            return False
        # проверяем свежесть screen pick или генерации
        screen_at = float(app.state.get("screen_last_at") or 0)
        gen_at = float(app.state.get("imggen_done_at") or 0)
        last_img = app.state.get("phone_media_last")
        if not last_img or not Path(str(last_img)).is_file():
            return False
        now = time.time()
        recent_screen = screen_at and (now - screen_at) < 120       # 2 минуты
        recent_gen = gen_at and (now - gen_at) < 300                  # 5 минут
        return bool(recent_screen or recent_gen)

    def _is_prompt_revise(self, low: str) -> bool:
        if any(w in low for w in ("напоминан", "заметк", "календар", "задач", "список", "файл", "окно", "вкладк", "громч", "тише")):
            return False
        if any(k in low for k in ("промпт", "промт")) and any(
            w in low for w in ("измени", "поправь", "перепиши", "добавь", "убери", "другой", "переделай")
        ):
            return True
        return any(w in low for w in (
            "добавь", "убери", "заново", "ещё раз", "еще раз", "перегенерир",
            "другой вариант", "тот же", "потемнее", "посветлее", "крупнее",
        ))

    def _has_recent_gen(self, app) -> bool:
        if not (app.state.get("imggen_last_prompt") or app.state.get("imggen_request")):
            return False
        last = float(app.state.get("imggen_done_at") or app.state.get("imggen_at") or 0)
        return bool(last) and time.time() - last < 1800

    def _revise(self, app, text: str) -> str:
        prev = str(app.state.get("imggen_last_prompt") or "")
        base = str(app.state.get("imggen_request") or "")
        low = (text or "").lower()
        refs = self._refs(app) or list(app.state.get("imggen_refs") or [])
        kind = ""
        if any(k in low for k in ("промпт", "промт")) or not self._wants_edit(low):
            wf = "t2i"
        else:
            wf = "edit"
        if wf == "edit" and not refs:
            name, _ = self._char_look(app)
            char_ref = ""
            if self._about_character(text, name):
                char_ref = self._character_ref_img(app)
            if char_ref:
                refs = [char_ref]
                kind = "self"
            else:
                app.state["imggen_stage"] = "await_prompt"
                app.state["imggen_workflow_hint"] = "edit"
                app.state["imggen_request"] = (text or "").strip()
                app.state["imggen_at"] = time.time()
                return "для правки фото нужен кадр. прикрепи его и повтори, что изменить."
        origin = str(app.state.get("imggen_origin") or base)
        merged = (
            "REVISION\n"
            f"ORIGIN:\n{origin[:800]}\n"
            f"PREVIOUS:\n{prev[:1800]}\n"
            "CHANGE:\n"
            f"{text}"
        )
        self._begin_one(app, merged, wf=wf, refs=refs if wf == "edit" else None, kind=kind)
        app.state["imggen_request"] = (text or "").strip()
        return "обновляю промпт и сразу отправляю в ComfyUI."

    def _wants_fresh(self, low: str) -> bool:
        return any(k in (low or "") for k in ("с нуля", "новую картин", "по тексту", "без референс", "без фото"))

    def _after_scene(self, app, raw: str) -> str:
        low = (raw or "").lower()
        edit = self._wants_edit(low) or str(app.state.get("imggen_workflow_hint") or "") in ("edit", "правка", "qwen_edit")
        fresh = self._wants_fresh(low)
        named = self._pick_workflow_name(low)
        if named:
            edit = self._family(named) == "edit"
            fresh = not edit
        attached = self._attached(app)
        refs = self._refs(app) if edit else attached
        name, _ = self._char_look(app)
        stored = str(app.state.get("imggen_request") or "")
        is_about_char = self._about_character(raw, name) or (stored and self._about_character(stored, name))
        kind = ""
        explicit_edit = self._wants_edit(low) and bool(attached)
        if is_about_char and not fresh and not named and not explicit_edit:
            char_candidate = self._character_ref_img(app)
            if char_candidate:
                # Не затирать screen-ref, если это явная правка с source=last
                hint = str(app.state.get("imggen_workflow_hint") or "")
                has_screen_ref = bool(refs) and hint in ("edit", "правка", "qwen_edit")
                if not has_screen_ref:
                    attached = []
                    refs = [char_candidate]
                    edit = True
                    kind = "self"
                    app.state["imggen_workflow_hint"] = "edit"
        # Если запрос ссылается на последнее изображение (screen pick / генерация)
        if not edit and not fresh and not named and self._is_refer_to_last(low, app):
            last_img = str(app.state.get("phone_media_last") or "")
            if last_img and Path(last_img).is_file():
                edit = True
                attached = [last_img]
                refs = [last_img]
                app.state["imggen_workflow_hint"] = "edit"
        ask = bool(app.get_plugin_setting(self.id, "ask_workflow", True))
        if attached and ask and not edit and not fresh and not named:
            app.state["imggen_stage"] = "await_model"
            app.state["imggen_request"] = raw
            app.state["imggen_refs"] = attached
            app.state["imggen_at"] = time.time()
            return (
                "фото есть. что делаем?\n"
                "1. новая картинка только по тексту\n"
                "2. правка этого фото"
            )
        char_ref = str(refs[0]) if refs and edit else ""
        if edit and not refs:
            if not char_ref:
                app.state["imggen_stage"] = "await_prompt"
                app.state["imggen_workflow_hint"] = "edit"
                app.state["imggen_request"] = raw
                app.state["imggen_at"] = time.time()
                return "для правки прикрепи фото и напиши, что изменить."
        wf = "edit" if edit else "t2i"
        if edit:
            app.state["imggen_refs"] = refs
        self._begin_one(app, raw, wf=wf, refs=refs if edit else None, kind=kind)
        if edit:
            if kind == "self":
                return "рисую себя по сохранённому фото. один промпт и сразу в генерацию."
            return "правка референса. один промпт-инструкция и сразу в Qwen-Image 2.1."
        return "картинка по тексту. один промпт под Qwen-Image 2.1 и сразу рисую."

    def _schedule_one(self, app, text, refs, wf: str, job: float = 0):
        async def run():
            try:
                prompt = await self._one_prompt(app, text, refs, wf)
            except Exception as e:
                print(f"imggen one prompt: {e}", flush=True)
                prompt = ""
            if job and app.state.get("imggen_job") != job:
                return
            if app.state.get("imggen_stage") not in ("drafting", "confirm"):
                return
            if not prompt:
                app.state["imggen_stage"] = "idle"
                self._notify(app, "не получила промпт от модели. проверь LM Studio и повтори сцену.", None)
                return
            path = self._resolve_wf(wf)
            fam = self._family(wf or path.stem)
            app.state["imggen_prompt"] = prompt
            app.state["imggen_last_prompt"] = prompt
            app.state["imggen_prompt_" + fam] = prompt
            app.state["imggen_last_wf"] = fam
            if not path.exists() or self._is_stub_wf(path):
                app.state["imggen_stage"] = "confirm"
                self._notify(app, f"промпт есть, но нет графа {path.name}.", None)
                return
            if fam == "edit" and not refs:
                fam = "t2i"
                path = self._resolve_wf("t2i")
                app.state["imggen_last_wf"] = fam
            app.state["imggen_stage"] = "busy"
            title = "рисую себя по сохранённому фото" if app.state.get("imggen_ref_kind") == "self" else (
                "правка референса" if fam == "edit" else "картинка по тексту"
            )
            self._notify(app, f"{title}.\n«{prompt[:700]}»\n\nотправляю в ComfyUI.", None)
            self._start(app, prompt, path, list(refs or []), job)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(run())
                return
        except Exception:
            pass

        def work():
            asyncio.run(run())

        threading.Thread(target=work, name="imggen-one", daemon=True).start()

    async def _one_prompt(self, app, request: str, refs: list, wf: str) -> str:
        fam = self._family(wf)
        name, look = self._char_look(app)
        origin, previous, change, fresh = self._split_revision(request)
        about = self._about_character(f"{origin}\n{change or fresh}", name)
        if fam == "edit" and str(app.state.get("imggen_ref_kind") or "") == "self":
            system = (
                "Ты пишешь финальную инструкцию для Qwen-Image-2.1 Edit. "
                "Граф её больше не переписывает. <image1> — фото внешности персонажа, не готовая сцена. "
                "Сохрани лицо, волосы, уши и узнаваемость человека с <image1>. "
                "Сцену, позу, одежду и фон собери заново по запросу пользователя. "
                "На картинке должна быть девушка, не мужчина. "
                "Английский язык, короткие указания. Обязательно начни с <image1>. "
                "Не пиши masterpiece, 8k, best quality, highly detailed. "
                "Не копируй подписи ORIGIN, PREVIOUS, CHANGE. "
                "Верни только инструкцию, без кавычек вокруг всего текста и без пояснений."
            )
        elif fam == "edit":
            system = (
                "Ты пишешь финальную инструкцию для Qwen-Image-2.1 Edit. "
                "Граф её больше не переписывает. Это не описание новой картинки. "
                "Английский язык, короткие указания. Исходное фото называй <image1>. "
                "Напиши, что изменить, и что оставить: pose, face, expression, clothes, "
                "background, composition. Оставляй их, если пользователь прямо не просит менять. "
                "Не пиши masterpiece, 8k, best quality, highly detailed. "
                "Не подменяй человека и не описывай кадр с нуля. "
                "Если это правка старой инструкции, сохрани прежние keep и внеси только CHANGE. "
                "Не копируй подписи ORIGIN, PREVIOUS, CHANGE. "
                "Верни только инструкцию, без кавычек вокруг всего текста и без пояснений."
            )
        else:
            system = (
                "Ты пишешь финальный промпт для Qwen-Image-2.1 text-to-image. "
                "Граф его больше не переписывает. Один английский абзац, как будто кадр уже перед тобой. "
                "Настоящее время. Нельзя: make sure, create, ensure, you, masterpiece, 8k, "
                "highly detailed, award-winning, best quality, разрешение, соотношение сторон, пиксели. "
                "Первая фраза строго такого вида: "
                "The image is a <vertical|wide|square> <style> <photograph|illustration|poster|scene> of <subject>, <background>. "
                "Дальше где что стоит: left, centre, right, upper third, lower third. "
                "Одежда и материалы отдельно. Отдельное предложение The lighting is.... "
                "Последнее предложение The overall composition.... "
                "Читаемый текст на картинке оставляй в исходной письменности внутри прямых двойных кавычек. "
                "Если даны PREVIOUS и CHANGE, сохрани всё, что не просили менять, и впиши только правку. "
                "Не копируй подписи ORIGIN, PREVIOUS, CHANGE и не отвечай по-русски. "
                "Только абзац."
            )
        if about:
            subject = (
                f"Персонаж «{name}» — девушка/female. На картинке должна быть девушка, не мужчина.\n"
                f"В кадре персонаж «{name}». Внешность переведи на английский, карточку в ответ не копируй:\n"
                f"{look[:500]}"
            )
        else:
            subject = "Рисуй только запрошенное. Не добавляй персонажа чата и черты, которых нет в запросе."
        if change or previous:
            user = (
                f"{subject}\n"
                f"ORIGIN:\n{origin}\n"
                f"PREVIOUS:\n{previous}\n"
                f"CHANGE:\n{change}"
            )
        else:
            user = f"{subject}\nЗапрос:\n{fresh}"
        raw = self._unwrap_prompt(await self._llm_complete(app, system, user) or "")
        if raw.startswith("{") and raw.endswith("}"):
            data = self._parse_prompt_json(raw)
            raw = str(data.get("rewritten_prompt") or data.get(fam) or data.get("prompt") or "").strip()
            raw = self._unwrap_prompt(raw)
        limit = 900 if fam == "edit" else 3200
        return raw[:limit]

    @staticmethod
    def _split_revision(request: str):
        text = request or ""
        if not text.startswith("REVISION\n"):
            return text, "", "", text
        origin = text.split("ORIGIN:\n", 1)[-1].split("\nPREVIOUS:\n", 1)[0].strip()
        previous = ""
        change = ""
        if "\nPREVIOUS:\n" in text:
            previous = text.split("\nPREVIOUS:\n", 1)[1].split("\nCHANGE:\n", 1)[0].strip()
        if "\nCHANGE:\n" in text:
            change = text.split("\nCHANGE:\n", 1)[1].strip()
        return origin, previous, change, change or origin

    @staticmethod
    def _unwrap_prompt(raw: str) -> str:
        s = (raw or "").strip()
        if s.startswith("```"):
            parts = s.split("```")
            s = parts[1] if len(parts) > 1 else s.strip("`")
            if s.lower().startswith("json"):
                s = s[4:]
            s = s.strip()
        if s.lower().startswith("json"):
            s = s[4:].strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
            s = s[1:-1].strip()
        return s

    def _schedule_card(self, app, text, refs):
        async def run():
            try:
                card = await self._llm_prompts_then_card(app, text, refs)
            except Exception as e:
                print(f"imggen llm task: {e}", flush=True)
                card = f"не собрала промпт: {e}"
                app.state["imggen_stage"] = "idle"
            else:
                if app.state.get("imggen_stage") == "drafting":
                    app.state["imggen_stage"] = "confirm"
                    app.state["imggen_at"] = time.time()
            self._notify(app, card, None)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(run())
                return
        except Exception:
            pass

        def work():
            asyncio.run(run())

        threading.Thread(target=work, name="imggen-llm", daemon=True).start()

    def _handle_confirm(self, app, text, low):
        if self._has_word(low, "нет") or self._has_word(low, "не то", "отмена"):
            app.state["imggen_stage"] = "idle"
            return HookResult(True, "ок, без картинки. скажи заново, что нарисовать.")
        if any(w in low for w in ("другой промпт", "переделай промпт", "не подойд")):
            prev = str(app.state.get("imggen_last_wf") or "")
            self._begin_one(app, text, wf=prev)
            return HookResult(True, f"тот же граф ({prev or 'по умолчанию'}), промпт один раз.")
        wf = self._pick_workflow_name(low)
        yes = self._has_word(low, "да", "давай", "ок", "го", "пойдёт", "пойдет", "норм", "утвержд", "сгенерируй")
        if wf or yes:
            if not wf:
                wf = str(app.get_plugin_setting(self.id, "default_workflow", "qwen") or "qwen")
            path = self._resolve_wf(wf)
            if not path.exists():
                return HookResult(True, f"нет файла {path.name}. Нужны qwen21_t2i.json или qwen21_edit.json.")
            if self._is_stub_wf(path):
                return HookResult(True,
                    f"{path.name} — не граф ComfyUI. Нужны qwen21_t2i.json и qwen21_edit.json.")
            app.state["imggen_stage"] = "busy"
            fam = self._family(wf or path.stem)
            refs = list(app.state.get("imggen_refs") or [])
            if fam == "edit" and not refs:
                app.state["imggen_stage"] = "confirm"
                return HookResult(True, "для правки нужен референс. прикрепи фото и снова напиши, что изменить.")
            prompt = str(app.state.get("imggen_prompt_" + fam) or app.state.get("imggen_prompt") or "")
            if not prompt:
                self._begin_one(app, str(app.state.get("imggen_request") or text))
                return HookResult(True, "промпта ещё нет. собираю один раз и сразу рисую.")
            self._start(app, prompt, path, refs)
            extra = f", референс: {Path(refs[0]).name}" if refs else ""
            return HookResult(True, f"запустила {path.stem}{extra}. промпт уже готов, второй раз не собираю.")
        scene = self._user_scene(text)
        if len(scene) >= 8:
            return HookResult(True, self._after_scene(app, text))
        paths = self._choices()
        return HookResult(True, self._model_menu(paths) if paths else "напиши имя графа из workflows/.")

    def _ask_card(self, app, prompt: str, refs: List[str]) -> str:
        if app.state.get("imggen_prompt_t2i") or app.state.get("imggen_prompt"):
            return self._ask_card_ready(app, refs)
        req = str(app.state.get("imggen_request") or "")
        t2i = self._draft_prompt(app, req, "t2i")
        edit = self._draft_prompt(app, req, "edit") if refs else ""
        app.state["imggen_prompt_t2i"] = t2i
        app.state["imggen_prompt_edit"] = edit
        app.state["imggen_prompt"] = t2i
        return self._ask_card_ready(app, refs)

    def _pick_workflow_name(self, low: str) -> str:
        # longer keys first
        for k in sorted(_ALIASES, key=len, reverse=True):
            if k in low.split() or f" {k} " in f" {low} " or low.endswith(k) or low.startswith(k):
                return k
        return ""

    def _list_wf(self) -> List[Path]:
        base = Path(__file__).resolve().parent
        out = []
        for folder in (base / "workflows", base):
            if folder.is_dir():
                out.extend(sorted(folder.glob("*.json")))
        return out

    def _resolve_wf(self, name: str) -> Path:
        base = Path(__file__).resolve().parent
        key = (name or "").strip().lower()
        rel = _ALIASES.get(key, key)
        p = Path(rel)
        if not p.is_absolute():
            p = base / rel
        if p.exists():
            return p
        for f in self._list_wf():
            if f.stem.lower() in (key, key.replace(" ", "_")):
                return f
        return p

    def _family(self, name: str) -> str:
        n = (name or "").lower()
        if any(k in n for k in ("edit", "правк", "референс")):
            return "edit"
        return "t2i"


    def _about_character(self, request: str, name: str = "") -> bool:
        """Сцена с персонажем чата: имя, «себя», «ассистент», «персонаж»."""
        low = (request or "").lower().replace("ё", "е")
        tokens = []
        cid = (name or "").strip().lower()
        if cid:
            tokens.extend({cid, cid.rstrip("аяуюи")})
        tokens.extend(("лисичка", "лисичку", "лисичке", "лисички", "мила", "милу", "миле", "милы"))
        if any(t and len(t) >= 3 and t in low for t in tokens):
            return True
        if any(w in low for w in (
            "ассистент", "персонаж", "себя", "тебя", "свой образ", "своем образе",
            "моя внешность", "твоя внешность", "этот персонаж", "эту героин",
        )):
            return True
        # Имя / род: если персонаж женский — «девушка» и «она» тоже про него
        if any(w in low for w in ("девушк", "девочк", "героин")):
            return True
        if re.search(r"(нарисуй|сгенерируй|сделай картин|сделай изображ|изобрази|высгенерируй)\s+(меня|мне)\b", low):
            return True
        if re.search(r"\b(где я|я сижу|я лежу|со мной|меня на|меня в)\b", low):
            return True
        # «как ты <действие>» — явно про персонажа
        if re.search(r"\bкак ты\s+\w+[её]шь\b", low):
            return True
        return False

    def _char_look(self, app) -> str:
        name = ""
        try:
            if hasattr(app, "get_active_character"):
                name = str(app.get_active_character() or "")
            if not name:
                name = str(getattr(app.config, "ACTIVE_CHARACTER", "") or "")
        except Exception:
            name = ""
        look = ""
        try:
            from character_catalog import read_character_card
            look = (read_character_card(name) or "")[:800]
        except Exception:
            look = ""
        return name or "персонаж", look or self._look_qwen()

    async def _llm_complete(self, app, system: str, user: str) -> str:
        msgs = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        llm = getattr(app, "llm", None)
        if llm is not None and hasattr(llm, "chat_once"):
            try:
                out = await llm.chat_once(msgs, temperature=0.4, max_tokens=1800)
                if out:
                    return str(out)
            except Exception as e:
                print(f"imggen llm.chat_once: {e}", flush=True)
        return ""

    def _parse_prompt_json(self, raw: str) -> dict:
        s = (raw or "").strip()
        if "```" in s:
            s = s.split("```", 2)[1]
            if s.lower().startswith("json"):
                s = s[4:]
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            s = s[i:j+1]
        try:
            data = json.loads(s)
            if isinstance(data, dict):
                return {k: str(v).strip() for k, v in data.items() if v}
        except Exception:
            pass
        return {}

    async def _llm_prompts_then_card(self, app, request: str, refs: list) -> str:
        name, look = self._char_look(app)
        about = self._about_character(request, name)
        if about:
            subject = (
                f"Персонаж «{name}» — девушка/female. На картинке должна быть девушка, не мужчина.\n"
                f"Запрос про персонажа чата «{name}». Внешность только из карточки:\n{look}\n"
                "Её и рисуй, если пользователь не назвал другого героя."
            )
        else:
            subject = (
                f"Запрос НЕ про персонажа чата («{name}»). "
                "НЕ вставляй её имя, уши, хвост, рыжие волосы и внешность. "
                "Рисуй только то, что в запросе пользователя."
            )
        system = (
            "Ты редактор промптов только для Qwen-Image-2.1. "
            "Верни ТОЛЬКО JSON без markdown:\n"
            '{"t2i":"...","edit":"..."}\n'
            "t2i — один английский абзац наблюдателя: The image is a ..., где что стоит, "
            "The lighting is..., The overall composition.... Без masterpiece и 8k.\n"
            "edit — английская инструкция правки с <image1>, что изменить и что оставить. "
            "Если референса нет, edit оставь пустым.\n"
            "Не вставляй персонажа чата, если запрос не про него."
        )
        user = (
            f"{subject}\n"
            f"Референс приложен: {'да' if refs else 'нет'}\n"
            f"Запрос пользователя: {request}\n"
            "Собери промпт t2i и, если есть референс, инструкцию edit."
        )
        raw = await self._llm_complete(app, system, user)
        data = self._parse_prompt_json(raw)
        print(f"imggen llm prompts keys={list(data.keys())} raw={raw[:180]!r}", flush=True)
        if not data.get("t2i") and not data.get("qwen"):
            # LLM не ответила — лучше сказать, чем совать сырую фразу
            if raw:
                data["t2i"] = raw.strip()[:600]
            else:
                return (
                    "не смогла получить промпт от LLM. проверь LM Studio "
                    "(http://127.0.0.1:1234) и повтори запрос."
                )
        t2i = data.get("t2i") or data.get("qwen") or ""
        edit = data.get("edit") or data.get("qwen_edit") or ""
        app.state["imggen_prompt_t2i"] = t2i
        app.state["imggen_prompt_edit"] = edit
        app.state["imggen_prompt"] = t2i
        app.state["imggen_last_prompt"] = t2i
        return self._ask_card_ready(app, refs)

    def _ask_card_ready(self, app, refs: list) -> str:
        t2i = str(app.state.get("imggen_prompt_t2i") or app.state.get("imggen_prompt") or "")
        edit = str(app.state.get("imggen_prompt_edit") or "")
        lines = [
            "Промпт для Qwen-Image 2.1:",
            f"Текст:\n«{t2i}»",
        ]
        if edit:
            lines.append(f"Правка:\n«{edit}»")
        lines.append("Если ок — «давай». Если нет — напиши, что изменить.")
        return "\n\n".join(lines)

    def _draft_prompt(self, app, raw: str, family: str = "") -> str:
        fam = self._family(family or "t2i")
        scene = self._user_scene(raw) or "a clear subject in even light"
        name, look = self._char_look(app)
        about = self._about_character(raw, name)
        if fam == "edit":
            keep = "Keep the pose, face, clothes, and composition from <image1>."
            if about:
                keep = f"Keep {name} as shown in <image1>: pose, face, and clothes, unless asked otherwise."
            return f"Edit <image1>. {keep} Change: {scene}."
        if about:
            return (
                f"The image is a vertical illustration of a cute anime girl with fox features — {name}. "
                f"She has long flowing red hair, fox ears, a fluffy tail, and bright blue eyes. "
                f"Scene: {scene}. The lighting is soft and even. "
                "The overall composition is a clear view of only this scene."
            )
        return (
            f"The image is an illustration of {scene}. "
            "The lighting is clear. The overall composition shows only what was requested."
        )

    def _user_scene(self, raw: str) -> str:

        extra = (raw or "").strip()
        for w in (
            "сгенерируй изображение", "сгенерируй картинку", "сгенерируй",
            "нарисуй мне", "нарисуй", "сделай картинку", "сделай изображение",
            "пришли фотку", "пришли фото", "скинь фотку", "скинь фото",
            "отредактируй", "поправь фото", "измени фото", "пожалуйста",
        ):
            extra = extra.replace(w, " ")
            extra = extra.replace(w.capitalize(), " ")
        return " ".join(extra.split())

    def _look_qwen(self) -> str:
        return (
            "аниме-девушка лиса: рыжие длинные волосы, лисьи уши, пушистый хвост, "
            "голубые глаза, узнаваемый персонаж Лисичка"
        )

    def _character_ref_img(self, app) -> str:
        """Сохранённое фото внешности: state, затем images/ref.*"""
        current = str(app.state.get("character_self_ref") or "")
        if current and Path(current).is_file():
            return current
        try:
            name = ""
            if hasattr(app, "get_active_character"):
                name = str(app.get_active_character() or "")
            if not name:
                name = str(getattr(app.config, "ACTIVE_CHARACTER", "") or "")
            if not name:
                return ""
            from character_catalog import character_dir
            img_dir = character_dir(name) / "images"
            if not img_dir.is_dir():
                return ""
            for stem in ("ref", "reference"):
                for ext in (".png", ".jpg", ".jpeg", ".webp"):
                    p = img_dir / f"{stem}{ext}"
                    if p.is_file():
                        app.state["character_self_ref"] = str(p)
                        return str(p)
        except Exception:
            pass
        return ""

    def _save_character_ref(self, app) -> str:
        """Сохранить прикреплённое/последнее изображение как ref.png персонажа."""
        try:
            name = ""
            if hasattr(app, "get_active_character"):
                name = str(app.get_active_character() or "")
            if not name:
                name = str(getattr(app.config, "ACTIVE_CHARACTER", "") or "")
            if not name:
                return "нет активного персонажа."
            from character_catalog import character_dir
            img_dir = character_dir(name) / "images"
            img_dir.mkdir(parents=True, exist_ok=True)
            refs = self._attached(app)
            if not refs:
                last = app.state.get("phone_media_last")
                if last and Path(str(last)).is_file():
                    refs = [str(last)]
            if not refs:
                # fallback: последняя сгенерированная картинка персонажа
                generated = Path(__file__).resolve().parents[2] / "generated" / name
                if generated.is_dir():
                    gens = sorted(generated.glob("gen_*.png")) + sorted(generated.glob("gen_*.jpg"))
                    if gens:
                        refs = [str(gens[-1])]
            if not refs:
                return "прикрепи фото или сначала сгенерируй себя."
            src = Path(refs[0])
            dest = img_dir / "ref.png"
            dest.write_bytes(src.read_bytes())
            from character_catalog import invalidate_card_cache
            invalidate_card_cache()
            app.state["character_self_ref"] = str(dest)
            print(f"imggen self ref saved: {dest}", flush=True)
            return f"запомнила. это я. когда рисую себя, беру это фото.\n[фото: {dest}]"
        except Exception as ex:
            return f"не смогла сохранить: {ex}"

    @staticmethod
    def _is_self_remember(text: str) -> bool:
        low = (text or "").lower().replace("ё", "е")
        low = re.sub(r"\[вложения:.*?\]", " ", low, flags=re.I | re.S)
        low = low.replace("этоты", "это ты").replace("это-ты", "это ты")
        low = " ".join(low.split())
        if any(k in low for k in (
            "запомни это", "запомни внешность", "запомни себя", "запомни образ",
            "это ты", "это я", "это твоя внешность", "это теперь ты", "это теперь я",
        )):
            return True
        return low.startswith("запомни") and any(k in low for k in (
            "внешность", "образ", "себя", "свой вид", "кадр", "картинк",
            "фото", "изображени",
        ))

    @staticmethod
    def _wants_show_ref(low: str) -> bool:
        text = (low or "").lower().replace("ё", "е")
        return any(k in text for k in (
            "как ты меня запомнила",
            "как ты меня помнишь",
            "покажи референс",
            "твой референс",
            "какое фото ты запомнила",
            "покажи как ты меня",
        ))

    def _show_character_ref(self, app) -> str:
        path = self._character_ref_img(app)
        if not path:
            return "я ещё не запомнила своё фото. прикрепи картинку и скажи «запомни, это ты»."
        return f"вот как я себя запомнила.\n[фото: {path}]"

    def _attached(self, app) -> List[str]:
        files = list(app.state.get("pending_attachments") or []) + list(app.state.get("last_attachments") or [])
        return self._image_files(files)

    def _refs(self, app) -> List[str]:
        files = list(app.state.get("pending_attachments") or []) + list(app.state.get("last_attachments") or [])
        last = app.state.get("phone_media_last")
        if last:
            files.append(last)
        return self._image_files(files)

    @staticmethod
    def _image_files(files) -> List[str]:
        out, seen = [], set()
        for f in files:
            p = Path(str(f))
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} and p.exists() and str(p) not in seen:
                seen.add(str(p))
                out.append(str(p))
        return out

    def _base(self, app) -> str:
        raw = str(app.get_plugin_setting(self.id, "comfy_url", "http://127.0.0.1:8188") or "").rstrip("/")
        try:
            from urllib.parse import urlparse
            u = urlparse(raw)
            host = (u.hostname or "").lower()
            if u.scheme != "http" or host not in ("127.0.0.1", "localhost", "::1"):
                print(f"imggen: comfy_url не loopback ({raw!r})", flush=True)
                return ""
        except Exception:
            return ""
        return raw

    def _start(self, app, prompt, wf_path: Path, refs: List[str], job: float = 0):
        def work():
            try:
                msg = self.tool_generate(app, prompt=prompt, workflow=str(wf_path), refs=refs)
            except Exception as e:
                msg = f"генерация сломалась: {e}"
            if not job or app.state.get("imggen_job") == job:
                app.state["imggen_stage"] = "idle"
                app.state["imggen_done_at"] = time.time()
            path = app.state.get("phone_media_last")
            req = str(app.state.get("imggen_request") or "")
            ok = path and Path(str(path)).exists() and "ошиб" not in (msg or "").lower() and "не " not in (msg or "")[:18]
            if ok:
                msg = f"помнишь, ты просил «{req[:80]}»? вот кадр."
            self._notify(app, msg, path if ok else None)
        threading.Thread(target=work, name="imggen", daemon=True).start()

    def _notify(self, app, msg, path):
        def ui():
            gui = getattr(app, "window", None) or app.state.get("gui")
            text = msg or ""
            if path and Path(str(path)).exists():
                text = text + f"\n[фото: {path}]"
            if gui and hasattr(gui, "publish_assistant_message"):
                gui.publish_assistant_message(text)
                return
            if gui and hasattr(gui, "_append"):
                gui._append("Ассистент", text)
        win = getattr(app, "window", None) or app.state.get("gui")
        if win is not None and hasattr(win, "post"):
            win.post(ui)
            return
        try:
            from PyQt5.QtWidgets import QApplication
            from PyQt5.QtCore import QThread
            appq = QApplication.instance()
            if appq is None or QThread.currentThread() is appq.thread():
                ui()
                return
        except Exception:
            pass
        ui()

    def _inbox(self) -> Path:
        return self._out_dir(self.app)

    def _out_dir(self, app) -> Path:
        name = "default"
        try:
            if hasattr(app, "get_active_character"):
                name = str(app.get_active_character() or "default")
            if not name:
                name = str(getattr(app.config, "ACTIVE_CHARACTER", "default") or "default")
        except Exception:
            pass
        # data/generated/<character>/
        root = Path(__file__).resolve().parents[2] / "generated" / name
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _is_stub_wf(self, path: Path) -> bool:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception:
            return True
        if "prompt" in data and isinstance(data["prompt"], dict):
            data = data["prompt"]
        if not isinstance(data, dict) or not data:
            return True
        # настоящий API-граф: узлы с class_type
        nodes = 0
        for k, v in data.items():
            if str(k).startswith("_"):
                continue
            if isinstance(v, dict) and v.get("class_type"):
                nodes += 1
        return nodes < 2

    def _load_graph(self, path: Path) -> dict:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "prompt" in data and isinstance(data["prompt"], dict):
            data = data["prompt"]
        return copy.deepcopy(data)

    def _fill(self, app, graph: dict, prompt: str, refs: List[str]) -> dict:
        seed = random.randint(1, 2**31 - 1)
        w = int(app.get_plugin_setting(self.id, "width", 1088) or 1088)
        h = int(app.get_plugin_setting(self.id, "height", 1808) or 1808)
        uploaded = None
        if refs:
            uploaded = self._upload(app, Path(refs[0]))
        for nid, node in graph.items():
            if not isinstance(node, dict):
                continue
            ct = node.get("class_type") or ""
            inp = node.setdefault("inputs", {})
            title = ((node.get("_meta") or {}).get("title") or "").lower()
            if ct == "PrimitiveStringMultiline" and self._is_prompt_slot(title):
                inp["value"] = prompt
            if ct in ("CLIPTextEncode", "TextEncodeQwenImageEdit", "TextEncodeQwenImageEditPlus"):
                field = "prompt" if "prompt" in inp else "text"
                cur = str(inp.get(field) or "")
                is_neg = "negative" in title
                is_pos = (not is_neg) and ("positive" in title or cur.strip() == "PROMPT")
                if is_pos and not isinstance(inp.get(field), list):
                    inp[field] = prompt
            if ct in ("EmptyLatentImage", "EmptySD3LatentImage"):
                if not isinstance(inp.get("width"), list):
                    inp["width"] = w
                if not isinstance(inp.get("height"), list):
                    inp["height"] = h
            if ct == "KSampler":
                inp["seed"] = seed
            if ct == "RandomNoise":
                inp["noise_seed"] = seed
            if ct == "LoadImage" and uploaded:
                inp["image"] = uploaded
        return graph

    @staticmethod
    def _is_prompt_slot(title: str) -> bool:
        if any(k in title for k in ("system", "rewrit", "эксперт", "pe ")):
            return False
        return any(k in title for k in ("prompt", "промпт", "изменить"))

    def _upload(self, app, path: Path) -> Optional[str]:
        try:
            import uuid
            boundary = uuid.uuid4().hex
            data = path.read_bytes()
            ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n"
            ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
            req = urllib.request.Request(
                self._base(app) + "/upload/image",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
            name = resp.get("name") or path.name
            print(f"imggen: uploaded ref {name}", flush=True)
            return name
        except Exception as e:
            print(f"imggen: upload fail {e}", flush=True)
            return None

    def _post(self, url, payload, timeout=30):
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))

    def _get(self, url, timeout=30):
        return urllib.request.urlopen(url, timeout=timeout).read()

    def tool_generate(self, app, prompt="", workflow="", refs=None, source="", negative="", size="", **kw):
        prompt = (prompt or kw.get("text") or kw.get("query") or "").strip()
        source = str(source or kw.get("source") or "").strip().lower()
        refs = list(refs or [])
        edit = source in ("last", "uploaded", "generated") or "edit" in str(workflow or "").lower()
        wf = str(workflow or "").strip()
        run_now = bool(wf) and Path(wf).suffix.lower() == ".json" or (
            wf in _ALIASES or (wf and (self._resolve_wf(wf).exists()) and wf not in ("", "auto"))
        )
        # intent imggen / imggen_edit — карточка промптов, не сразу в очередь
        if not run_now:
            if not prompt:
                app.state["imggen_stage"] = "await_prompt"
                app.state["imggen_at"] = time.time()
                return "что рисуем? опиши сцену."
            scene = self._user_scene(prompt)
            if len(scene) < 8:
                app.state["imggen_stage"] = "await_prompt"
                app.state["imggen_request"] = prompt
                app.state["imggen_at"] = time.time()
                return "что рисуем? опиши сцену — промпт соберу один раз."
            if edit:
                app.state["imggen_workflow_hint"] = "правка"
            return self._after_scene(app, prompt)
        refs = refs or []
        base = self._base(app)
        if not base:
            return "ComfyUI только на localhost (127.0.0.1 / ::1). Проверь URL в настройках."
        path = Path(workflow) if workflow else self._resolve_wf(str(app.get_plugin_setting(self.id, "default_workflow", "qwen")))
        print(f"imggen: wf={path} prompt={prompt[:140]!r} refs={refs}", flush=True)
        try:
            graph = self._fill(app, self._load_graph(path), prompt, refs)
        except Exception as e:
            return f"не прочитан workflow: {e}"
        try:
            queued = self._post(base + "/prompt", {"prompt": graph})
        except Exception as e:
            return f"ComfyUI /prompt: {e}"
        print(f"imggen: queue {queued}", flush=True)
        if queued.get("error") or queued.get("node_errors"):
            return f"граф отклонён: {queued.get('error') or queued.get('node_errors')}"
        pid = str(queued.get("prompt_id") or "")
        if not pid:
            return f"нет prompt_id: {queued}"
        limit = int(app.get_plugin_setting(self.id, "timeout_sec", 900) or 900)
        t0 = time.time()
        hist = {}
        n = 0
        while time.time() - t0 < limit:
            time.sleep(1.5)
            n += 1
            try:
                hist = json.loads(self._get(self._base(app) + "/history/" + pid).decode("utf-8"))
            except Exception:
                continue
            if pid in hist:
                break
            if n % 4 == 0:
                print(f"imggen: wait {int(time.time()-t0)}s", flush=True)
        else:
            return f"не успела за {limit} сек."
        outputs = (hist.get(pid) or {}).get("outputs") or {}
        info = None
        for node in outputs.values():
            if node.get("images"):
                info = node["images"][0]
                break
        if not info:
            return "в history нет картинки."
        q = "?filename=%s&subfolder=%s&type=%s" % (
            quote(str(info.get("filename") or ""), safe=""),
            quote(str(info.get("subfolder") or ""), safe=""),
            quote(str(info.get("type") or "output"), safe=""),
        )
        blob = self._get(self._base(app) + "/view" + q, timeout=120)
        dest = self._out_dir(app) / f"gen_{int(time.time())}.png"
        dest.write_bytes(blob)
        app.state["phone_media_last"] = str(dest)
        print(f"imggen: saved {dest}", flush=True)
        return f"[фото: {dest}]"


def register():
    return PluginImpl()







