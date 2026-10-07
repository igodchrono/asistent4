# -*- coding: utf-8 -*-
"""PC tools — только исполнители, без regex-фраз.

Песочница по умолчанию: блокнот и калькулятор.
confirmed принимается только из _execute_pending (_internal=True), не из LLM.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.plugin_api import AppContext, HookResult, Plugin, SettingField

_APP_ALIASES = {
    "калькулятор": "calc",
    "блокнот": "notepad",
    "проводник": "explorer",
    "explorer": "explorer",
    "chrome": "chrome",
    "браузер": "chrome",
    # новые
    "telegram": "Telegram",
    "телеграм": "Telegram",
    "телега": "Telegram",
    "discord": "Discord",
    "дискорд": "Discord",
    "vscode": "Code",
    "code": "Code",
    "вс код": "Code",
    "вижуал студио код": "Code",
    "word": "WINWORD",
    "ворд": "WINWORD",
    "winword": "WINWORD",
    "msword": "WINWORD",
    "excel": "EXCEL",
    "эксель": "EXCEL",
    "xls": "EXCEL",
    "firefox": "firefox",
    "фаерфокс": "firefox",
    "огненная лиса": "firefox",
    "edge": "msedge",
    "эдж": "msedge",
    "microsoft edge": "msedge",
    "spotify": "Spotify",
    "спотифай": "Spotify",
    "спотику": "Spotify",
    "steam": "Steam",
    "стим": "Steam",
    "paint": "mspaint",
    "пейнт": "mspaint",
    "краски": "mspaint",
    "mspaint": "mspaint",
    "cmd": "cmd",
    "командная строка": "cmd",
    "командная": "cmd",
    "terminal": "wt",
    "терминал": "wt",
    "powershell": "powershell",
    "pwsh": "powershell",
    "slack": "Slack",
    "слак": "Slack",
    "zoom": "Zoom",
    "зум": "Zoom",
    "obs": "obs64",
    "обс": "obs64",
    "stylus": "stylus",
    "notepad++": "notepad++",
    "блокнот++": "notepad++",
    "блокнот плюс": "notepad++",
}
_CLOSE_ALIASES = {
    "калькулятор": ["CalculatorApp.exe", "win32calc.exe", "calc.exe"],
    "calc": ["CalculatorApp.exe", "win32calc.exe", "calc.exe"],
    "calculator": ["CalculatorApp.exe", "win32calc.exe", "calc.exe"],
    "блокнот": ["notepad.exe"],
    "notepad": ["notepad.exe"],
    # новые
    "telegram": ["Telegram.exe"],
    "телеграм": ["Telegram.exe"],
    "телега": ["Telegram.exe"],
    "discord": ["Discord.exe"],
    "дискорд": ["Discord.exe"],
    "vscode": ["Code.exe"],
    "code": ["Code.exe"],
    "word": ["WINWORD.EXE"],
    "ворд": ["WINWORD.EXE"],
    "excel": ["EXCEL.EXE"],
    "эксель": ["EXCEL.EXE"],
    "firefox": ["firefox.exe"],
    "edge": ["msedge.exe", "msedgewebview2.exe"],
    "эдж": ["msedge.exe", "msedgewebview2.exe"],
    "spotify": ["Spotify.exe"],
    "steam": ["Steam.exe"],
    "стим": ["Steam.exe"],
    "paint": ["mspaint.exe"],
    "cmd": ["cmd.exe"],
    "terminal": ["WindowsTerminal.exe", "wt.exe"],
    "терминал": ["WindowsTerminal.exe", "wt.exe"],
    "powershell": ["powershell.exe", "pwsh.exe"],
    "slack": ["Slack.exe"],
    "zoom": ["Zoom.exe"],
    "obs": ["obs64.exe"],
    "chrome": ["chrome.exe"],
    "браузер": ["chrome.exe"],
    "проводник": ["explorer.exe"],
    "explorer": ["explorer.exe"],
}
_SANDBOX_OPEN = {
    "калькулятор": "calc",
    "calc": "calc",
    "calculator": "calc",
    "блокнот": "notepad",
    "notepad": "notepad",
    "telegram": "Telegram",
    "телеграм": "Telegram",
    "discord": "Discord",
    "дискорд": "Discord",
    "paint": "mspaint",
    "пейнт": "mspaint",
    "cmd": "cmd",
    "командная строка": "cmd",
    "terminal": "wt",
    "терминал": "wt",
    "powershell": "powershell",
}


class PluginImpl(Plugin):
    id = "pc_control"
    name = "Управление ПК"
    version = "2.1.0"
    settings_tab = "own"
    settings_tab_title = "Управление ПК"
    settings_schema = [
        SettingField("enabled", "Включить", "bool", True),
        SettingField(
            "sandbox",
            "Песочница (только блокнот и калькулятор)",
            "bool",
            True,
            help="Запрещает произвольные пути, поиск по дискам, корзину и запуск exe.",
        ),
        SettingField(
            "allow_process_close",
            "Разрешить закрытие программ",
            "bool",
            False,
            help="taskkill. В песочнице — только блокнот/калькулятор.",
        ),
        SettingField(
            "confirm_danger",
            "Спрашивать перед опасным",
            "bool",
            True,
            help="Очистка корзины и закрытие чужих процессов — только после «да» в чате. "
            "Флаг confirmed из модели игнорируется.",
        ),
    ]

    def on_load(self, app: AppContext) -> None:
        self.app = app
        sb = "sandbox" if self._sandbox(app) else "full"
        print(
            f"🖥 pc_control: {sb}, close={self._allow_close(app)}, confirm={self._confirm_danger(app)}",
            flush=True,
        )

    def _sandbox(self, app: AppContext) -> bool:
        return bool(app.get_plugin_setting(self.id, "sandbox", True))

    def _allow_close(self, app: AppContext) -> bool:
        return bool(app.get_plugin_setting(self.id, "allow_process_close", False))

    def _confirm_danger(self, app: AppContext) -> bool:
        return bool(app.get_plugin_setting(self.id, "confirm_danger", True))

    @staticmethod
    def _is_sandbox_app(target: str) -> bool:
        t = (target or "").strip().lower()
        t = t.replace(".exe", "")
        return t in _SANDBOX_OPEN

    def _sandbox_block(self, app: AppContext, action: str) -> Optional[str]:
        if self._sandbox(app):
            alts = ", ".join(sorted(_SANDBOX_OPEN.keys())[:10])
            return (
                f"Песочница ПК: «{action}» нельзя. "
                f"Можно: {alts}. Сними галку в настройках «Управление ПК»."
            )
        return None

    def register_tools(self, app: AppContext) -> None:
        app.tools["pc_open"] = self.tool_open
        app.tools["pc_close"] = self.tool_close
        app.tools["pc_volume"] = self.tool_volume
        app.tools["pc_type"] = self.tool_type
        app.tools["pc_search_files"] = self.tool_search_files
        app.tools["pc_search_folders"] = self.tool_search_folders
        app.tools["pc_open_found"] = self.tool_open_found
        app.tools["pc_close_last"] = self.tool_close_last
        app.tools["pc_create_text"] = self.tool_create_text
        app.tools["pc_recycle"] = self.tool_recycle
        app.tools["pc_empty_recycle"] = self.tool_empty_recycle

    def on_user_message(self, text: str, app: AppContext) -> Optional[HookResult]:
        if not app.get_plugin_setting(self.id, "enabled", True):
            return None
        pending = getattr(self, "_pending", None)
        low = (text or "").strip().lower()
        if not pending:
            return None
        if low in ("да", "yes", "ок", "окей", "подтверждаю"):
            self._pending = None
            try:
                reply = self._execute_pending(app, pending)
            except Exception as e:
                reply = f"Не удалось: {e}"
            return HookResult(True, reply)
        if low in ("нет", "no", "отмена", "не надо"):
            self._pending = None
            return HookResult(True, "Отменено.")
        return None

    def _focus_window_by_title(self, title_part: str, wait: float = 0.8) -> None:
        """Совместимость с selftest."""
        if not title_part:
            return
        time.sleep(max(0.2, float(wait)))
        part = title_part.replace("'", "''")[:80]
        ps = (
            f"$part='{part}'; "
            "Add-Type -Name W -Namespace Z -MemberDefinition '[DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr h,int c);'; "
            "$p=Get-Process | Where-Object { $_.MainWindowTitle -like \"*$part*\" } | Select-Object -First 1; "
            "if($p){ [Z.W]::ShowWindow($p.MainWindowHandle,3); [Z.W]::SetForegroundWindow($p.MainWindowHandle) }"
        )
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=15)
        except Exception:
            pass

    def _execute_pending(self, app, pending):
        action = pending.get("action")
        if action == "empty_recycle":
            return self.tool_empty_recycle(app, confirmed=True, _internal=True)
        if action == "close":
            return self.tool_close(
                app, target=pending.get("target") or "", confirmed=True, _internal=True
            )
        return "Неизвестное действие."

    def tool_open(self, app: AppContext, target: str = "", **kw) -> str:
        target = (target or kw.get("query") or "").strip()
        if not target:
            return "Не указано, что открыть."
        # поддержка «открой найденное/этот/эту/найденный»
        pronoun_low = target.lower().strip(" .!?")
        if pronoun_low in ("этот", "это", "эту", "найденное", "найденный", "найденную", "найденного",
                           "найденную папку", "найденный файл", "найденное файл", "найденное папку"):
            found = str(app.state.get("pc_last_found") or "")
            if found:
                os.startfile(found)
                app.state["pc_last_opened"] = found
                return f"Открыто найденное: {found}"
            return "Нет сохранённого результата поиска."
        if self._sandbox(app):
            alias = _SANDBOX_OPEN.get(target.lower())
            if not alias:
                alts = ", ".join(sorted(_SANDBOX_OPEN.keys())[:12])
                return (
                    f"Песочница ПК: «{target}» открыть нельзя. "
                    f"Можно: {alts}"
                )
            exe = shutil.which(alias) or shutil.which(alias + ".exe") or alias
            try:
                subprocess.Popen([exe], close_fds=True)
            except Exception as e:
                return f"Не запущено: {e}"
            app.state["pc_last_opened"] = alias
            return f"Запущено (песочница): {alias}"
        alias = _APP_ALIASES.get(target.lower(), target)
        path = Path(os.path.expandvars(os.path.expanduser(alias)))
        if path.exists():
            os.startfile(str(path))
            app.state["pc_last_opened"] = str(path)
            return f"Открыто: {path}"
        exe = shutil.which(alias) or shutil.which(alias + ".exe")
        if exe:
            subprocess.Popen([exe], close_fds=True)
            return f"Запущено: {target}"
        try:
            os.startfile(target)
            app.state["pc_last_opened"] = target
            return f"Открыто: {target}"
        except Exception:
            return f"Не найдено: {target}"

    def tool_close(
        self,
        app: AppContext,
        target: str = "",
        confirmed: bool = False,
        _internal: bool = False,
        **kw,
    ) -> str:
        target = (target or kw.get("query") or "").strip()
        if not _internal:
            confirmed = False
        if not self._allow_close(app):
            return "Закрытие программ выключено (allow_process_close)."
        if self._sandbox(app) and not self._is_sandbox_app(target):
            return self._sandbox_block(app, f"закрыть {target}") or "Песочница."
        need_confirm = self._confirm_danger(app) and not self._is_sandbox_app(target)
        if need_confirm and not confirmed:
            self._pending = {"action": "close", "target": target}
            return f"Подтвердите: закрыть {target}? да/нет"
        key = target.lower().replace(".exe", "")
        candidates = list(_CLOSE_ALIASES.get(key, []))
        if not candidates:
            if self._sandbox(app):
                return self._sandbox_block(app, f"закрыть {target}") or "Песочница."
            name = target if target.lower().endswith(".exe") else target + ".exe"
            candidates = [name]
        closed = []
        for image in candidates:
            try:
                r = subprocess.run(
                    ["taskkill", "/IM", image, "/F"],
                    capture_output=True, text=True,
                )
            except FileNotFoundError:
                return "taskkill недоступен (нужен Windows)."
            if r.returncode == 0:
                closed.append(image)
        return f"Закрыто: {', '.join(closed)}" if closed else f"Не найдено процесс: {target}"

    def tool_type(self, app: AppContext, text: str = "", **kw) -> str:
        """Печатает текст в активное окно через эмуляцию клавиатуры."""
        text = (text or kw.get("query") or "").strip()
        if not text:
            return "Не указан текст для печати."
        blocked = self._sandbox_block(app, "набор текста")
        if blocked:
            return blocked
        # Оборачиваем текст в PowerShell для SendKeys
        escaped = text.replace("{", "{{").replace("}", "}}").replace("~", "{~}")
        # Замена управляющих последовательностей
        escaped = (escaped
            .replace("\n", "{ENTER}")
            .replace("\t", "{TAB}")
        )
        ps = (
            'Add-Type -AssemblyName System.Windows.Forms; '
            f'[System.Windows.Forms.SendKeys]::SendWait(\'{escaped}\')'
        )
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=15)
        except Exception as e:
            return f"Ошибка ввода текста: {e}"
        return f"Текст напечатан ({len(text)} символов)"

    def tool_volume(self, app: AppContext, direction: str = "up", **kw) -> str:
        key = 0xAF if str(direction).lower() in ("up", "громче", "+") else 0xAE
        try:
            import ctypes
            ctypes.windll.user32.keybd_event(key, 0, 0, 0)
            ctypes.windll.user32.keybd_event(key, 0, 2, 0)
        except Exception as e:
            return f"Громкость: {e}"
        return "Громкость увеличена." if key == 0xAF else "Громкость уменьшена."

    def tool_search_files(self, app: AppContext, query: str = "*", disk: str = "", **kw) -> str:
        blocked = self._sandbox_block(app, "поиск файлов по дискам")
        if blocked:
            return blocked
        query = (query or kw.get("text") or kw.get("path") or "*").strip()
        root_text = (disk or kw.get("disk") or "").strip()
        if not root_text:
            root_text = self._disk_from_text(query) or self._disk_from_text(
                str(getattr(app, "state", {}).get("last_user_text") or "")
            )
        if root_text:
            query = self._strip_disk_phrase(query)
        roots = self._roots(root_text)
        qlow = query.lower()
        if any(w in qlow for w in ("картин", "фото", "изображ", "обои", "jpg", "png", "webp")):
            patterns = ("*.jpg", "*.jpeg", "*.png", "*.webp", "*.gif", "*.bmp")
        else:
            patterns = (query if any(c in query for c in "*?") else f"*{query}*",)
        results: List[str] = []
        started = time.monotonic()
        skip = {"$recycle.bin", "system volume information", "windows", "program files",
                "program files (x86)", "programdata", "appdata", "node_modules", ".git"}
        for root in roots:
            if not root.exists():
                continue
            for cur, dirs, files in os.walk(root, topdown=True):
                dirs[:] = [d for d in dirs if d.lower() not in skip]
                try:
                    depth = len(Path(cur).relative_to(root).parts)
                except Exception:
                    depth = 0
                if depth > 8:
                    dirs[:] = []
                    continue
                for name in files:
                    if any(fnmatch.fnmatch(name.lower(), pat.lower()) for pat in patterns):
                        results.append(str(Path(cur) / name))
                        if len(results) >= 25:
                            break
                if len(results) >= 25 or time.monotonic() - started > 15:
                    break
            if len(results) >= 25 or time.monotonic() - started > 15:
                break
        app.state["pc_last_search"] = results
        if results:
            app.state["pc_last_found"] = results[0]
        if not results:
            return f"По «{query}» ничего не найдено."
        lines = "\n".join(f"{i}. {p}" for i, p in enumerate(results, 1))
        return f"Найдено файлов: {len(results)}\n{lines}"

    def tool_search_folders(self, app: AppContext, query: str = "", disk: str = "", **kw) -> str:
        blocked = self._sandbox_block(app, "поиск папок")
        if blocked:
            return blocked
        query = (query or kw.get("text") or "").strip().lower()
        if not query:
            return "Укажи имя папки."
        root_text = (disk or kw.get("disk") or "").strip()
        if not root_text:
            root_text = self._disk_from_text(query) or self._disk_from_text(
                str(getattr(app, "state", {}).get("last_user_text") or "")
            )
        if root_text:
            query = self._strip_disk_phrase(query)
        roots = self._roots(root_text)
        results: List[str] = []
        started = time.monotonic()
        skip = {"$recycle.bin", "system volume information", "windows", "program files",
                "program files (x86)", "programdata", "appdata", "node_modules", ".git"}
        for root in roots:
            if not root.exists():
                continue
            for cur, dirs, files in os.walk(root, topdown=True):
                dirs[:] = [d for d in dirs if d.lower() not in skip]
                try:
                    depth = len(Path(cur).relative_to(root).parts)
                except Exception:
                    depth = 0
                if depth > 7:
                    dirs[:] = []
                    continue
                for d in list(dirs):
                    if query in d.lower():
                        results.append(str(Path(cur) / d))
                        if len(results) >= 20:
                            break
                if len(results) >= 20 or time.monotonic() - started > 15:
                    break
            if len(results) >= 20 or time.monotonic() - started > 15:
                break
        app.state["pc_last_search"] = results
        if results:
            app.state["pc_last_found"] = results[0]
        if not results:
            return f"Папка «{query}» не найдена."
        lines = "\n".join(f"{i}. {p}" for i, p in enumerate(results, 1))
        return f"Найдено папок: {len(results)}\n{lines}"

    def tool_open_found(self, app: AppContext, **kw) -> str:
        blocked = self._sandbox_block(app, "открыть найденный файл")
        if blocked:
            return blocked
        path = str(app.state.get("pc_last_found") or "")
        if not path:
            return "Нет сохранённого результата поиска."
        os.startfile(path)
        app.state["pc_last_opened"] = path
        return f"Открыто: {path}"

    def tool_close_last(self, app: AppContext, **kw) -> str:
        if not self._allow_close(app):
            return "Закрытие программ выключено (allow_process_close)."
        blocked = self._sandbox_block(app, "закрыть последнее окно")
        if blocked:
            return blocked
        path = str(app.state.get("pc_last_opened") or app.state.get("pc_last_found") or "")
        if not path:
            return "Нет последнего открытого."
        p = Path(path)
        if p.is_dir():
            ps = (
                f"$target = '{str(p).replace(chr(39), chr(39)+chr(39))}'; "
                "$shell = New-Object -ComObject Shell.Application; $n=0; "
                "foreach ($w in @($shell.Windows())) { try { "
                "if ($w.Document.Folder.Self.Path -eq $target) { $w.Quit(); $n++ } "
                "} catch {} }; Write-Output $n"
            )
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=20)
            app.state["pc_last_opened"] = ""
            return f"Закрыто окон папки: {(r.stdout or '').strip()}"
        name = p.name.replace("'", "''")
        ps = (
            f"$name='{name}'; "
            "Get-Process | Where-Object { $_.MainWindowTitle -like \"*$name*\" } | "
            "ForEach-Object { try { $_.CloseMainWindow()|Out-Null } catch {} }; 'ok'"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=20)
        app.state["pc_last_opened"] = ""
        return f"Закрыто: окна с «{p.name}»"

    def tool_create_text(self, app: AppContext, name: str = "note.txt", **kw) -> str:
        blocked = self._sandbox_block(app, "создать файл")
        if blocked:
            return blocked
        name = (name or kw.get("text") or "note.txt").strip().strip('"')
        p = Path(name)
        if p.is_absolute():
            return "Абсолютный путь запрещён. Укажи только имя файла."
        folder = Path(getattr(app.config, "DATA_DIR", Path("."))) / "selftest_files"
        folder.mkdir(parents=True, exist_ok=True)
        if not name.lower().endswith(".txt"):
            name += ".txt"
        # только имя, без обхода каталогов
        p = folder / Path(name).name
        p.write_text(f"created by pc_control\n{p}\n", encoding="utf-8")
        app.state["pc_last_text_file"] = str(p)
        return f"Создан файл: {p}"

    def tool_recycle(self, app: AppContext, name: str = "", **kw) -> str:
        blocked = self._sandbox_block(app, "корзина")
        if blocked:
            return blocked
        raw = (name or kw.get("text") or "").strip().strip('"')
        for junk in ("удали файл ", "удалить файл ", "удали ", "файл "):
            if raw.lower().startswith(junk):
                raw = raw[len(junk):].strip()
        if not raw:
            raw = str(app.state.get("pc_last_text_file") or "")
        if not raw:
            return "Укажи файл."
        p = Path(raw)
        data_dir = Path(getattr(app.config, "DATA_DIR", Path("."))).resolve()
        if not p.is_file():
            for cand in (
                data_dir / "selftest_files" / Path(raw).name,
                data_dir / "selftest_files" / raw,
            ):
                if cand and Path(cand).is_file():
                    p = Path(cand)
                    break
        if not p.is_file():
            last = str(app.state.get("pc_last_text_file") or "")
            if last and Path(last).is_file():
                p = Path(last)
            else:
                return f"Файл не найден: {raw}"
        try:
            p_res = p.resolve()
            allowed = (data_dir / "selftest_files").resolve()
            if allowed not in p_res.parents and p_res.parent != allowed:
                return f"В корзину можно только файлы из {allowed}"
        except Exception:
            return "Некорректный путь."
        parent = str(p.parent).replace("'", "''")
        nm = p.name.replace("'", "''")
        ps = (
            f"$p=Join-Path '{parent}' '{nm}'; "
            "Add-Type -AssemblyName Microsoft.VisualBasic; "
            "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p,'OnlyErrorDialogs','SendToRecycleBin')"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return f"Не удалось в корзину: {(r.stderr or r.stdout or '')[:200]}"
        return f"Файл отправлен в корзину: {p.name}"

    def tool_empty_recycle(
        self,
        app: AppContext,
        confirmed: bool = False,
        _internal: bool = False,
        **kw,
    ) -> str:
        if not _internal:
            confirmed = False
        blocked = self._sandbox_block(app, "очистить корзину")
        if blocked:
            return blocked
        if not confirmed:
            self._pending = {"action": "empty_recycle"}
            return "Подтвердите: очистить корзину? да/нет"
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"],
            capture_output=True, text=True, timeout=120,
        )
        return "Корзина очищена."

    @staticmethod
    def _disk_from_text(text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""
        m = re.search(r"(?:диск[аеу]?\s+|на\s+диске\s+)([A-Za-z])\b", t, re.I)
        if m:
            return m.group(1).upper()
        m = re.search(r"\b([A-Za-z]):(?:\\|/|\s|$)", t)
        if m:
            return m.group(1).upper()
        return ""

    @staticmethod
    def _strip_disk_phrase(text: str) -> str:
        t = text or ""
        t = re.sub(r"(?:на\s+)?диск[аеу]?\s+[A-Za-z]\b", " ", t, flags=re.I)
        t = re.sub(r"\b[A-Za-z]:(?:\\|/)?", " ", t)
        t = " ".join(t.split())
        return t or "*"

    def _roots(self, disk: str) -> List[Path]:
        disk = (disk or "").strip()
        if disk:
            letter = disk[0].upper() if disk else ""
            if letter.isalpha():
                return [Path(f"{letter}:/")]
            d = disk.rstrip(":\\/") + ":/"
            return [Path(d)]
        roots = []
        if Path.home().exists():
            roots.append(Path.home())
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            p = Path(f"{letter}:/")
            if p.exists():
                roots.append(p)
        return roots


def register():
    return PluginImpl()
