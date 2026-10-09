# -*- coding: utf-8 -*-
"""LLMManager — выбор источника LLM: API или EXE.

Использование:
    manager = LLMManager.from_config(config, app)
    reply = await manager.chat_once(messages)
    async for chunk in manager.chat_stream(messages): ...
"""
from __future__ import annotations
import os
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────
#  Абстрактный провайдер
# ──────────────────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """Базовый класс для всех LLM-провайдеров."""

    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        """Синхронная генерация по строковому промпту."""

    @abstractmethod
    async def chat_once(self, messages: List[Dict], **opts) -> str:
        """Асинхронный однократный запрос (messages → ответ)."""

    @abstractmethod
    def chat_stream(self, messages: List[Dict], **opts) -> AsyncIterator[str]:
        """Асинхронная генерация потоком (yield чанков)."""

    @property
    def model(self) -> str:
        """Имя текущей модели."""
        return "unknown"


# ──────────────────────────────────────────────────────────────────────
#  APIProvider  —  обёртка над существующим LLMClient
# ──────────────────────────────────────────────────────────────────────

class APIProvider(LLMProvider):
    """Провайдер для API-доступа (LM Studio, proxyapi.ru и др.)."""

    def __init__(self, llm_client):
        self._client = llm_client

    def generate(self, prompt: str, **kwargs) -> str:
        raise NotImplementedError(
            "APIProvider не поддерживает sync generate. Используйте chat_once / chat_stream."
        )

    async def chat_once(self, messages: List[Dict], **opts) -> str:
        return await self._client.chat_once(messages, **opts)

    async def chat_stream(self, messages: List[Dict], **opts) -> AsyncIterator[str]:
        async for chunk in self._client.chat_stream(messages, **opts):
            yield chunk

    @property
    def model(self) -> str:
        return self._client.model

    @property
    def _client_ref(self):
        """Доступ к внутреннему LLMClient для ping / list_models."""
        return self._client


# ──────────────────────────────────────────────────────────────────────
#  EXEProvider  —  запуск внешнего .exe для инференса
# ──────────────────────────────────────────────────────────────────────

class EXEProvider(LLMProvider):
    """Провайдер, запускающий .exe с промптом в аргументе."""

    def __init__(self, exe_path: str):
        self.exe_path = exe_path

    def generate(self, prompt: str, **kwargs) -> str:
        import subprocess
        try:
            result = subprocess.run(
                [self.exe_path, prompt],
                capture_output=True,
                text=True,
                timeout=kwargs.get("timeout", 120),
            )
            if result.returncode != 0:
                raise RuntimeError(f"EXE error (code {result.returncode}): {result.stderr[:500]}")
            return result.stdout.strip()
        except FileNotFoundError:
            raise FileNotFoundError(f"EXE не найден: {self.exe_path}")
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"EXE таймаут: {self.exe_path}")

    async def chat_once(self, messages: List[Dict], **opts) -> str:
        prompt = "\n".join(m.get("content", "") for m in messages)
        import asyncio
        return await asyncio.to_thread(self.generate, prompt, **opts)

    async def chat_stream(self, messages: List[Dict], **opts) -> AsyncIterator[str]:
        result = await self.chat_once(messages, **opts)
        yield result


# ──────────────────────────────────────────────────────────────────────
#  LLMManager  —  оркестратор, единая точка входа
# ──────────────────────────────────────────────────────────────────────

class LLMManager:
    """Оркестратор LLM-провайдеров.

    Провайдеры:
      - "api"     — LLMClient (LM Studio / proxyapi.ru)
      - "exe"     — EXEProvider (внешний .exe)
    """

    PROVIDER_API = "api"
    PROVIDER_EXE = "exe"

    def __init__(self, config):
        self.config = config
        self._provider: Optional[LLMProvider] = None
        self._provider_type: Optional[str] = None

        # для совместимости с кодом, читающим self.llm.temperature / max_tokens / timeout
        self.temperature = float(getattr(config, "TEMPERATURE", 0.75) or 0.75)
        self.max_tokens = int(getattr(config, "MAX_TOKENS", 1000) or 1000)
        self.timeout = float(getattr(config, "LLM_TIMEOUT", 300) or 300)

    # ── фабрика ──────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, config, app=None) -> "LLMManager":
        """Создаёт LLMManager, читая настройки из config + plugin llm_provider."""
        manager = cls(config)

        provider_type = cls.PROVIDER_API
        if app is not None:
            try:
                provider_type = app.get_plugin_setting(
                    "llm_provider", "provider", cls.PROVIDER_API
                )
            except Exception:
                pass

        if provider_type == cls.PROVIDER_EXE:
            exe_path = ""
            if app is not None:
                try:
                    exe_path = app.get_plugin_setting("llm_provider", "exe_path", "")
                except Exception:
                    pass
            manager.set_provider(cls.PROVIDER_EXE, exe_path=exe_path)
        else:
            # API — local (LM Studio) или external (proxyapi)
            from .llm_client import LLMClient
            client = LLMClient.from_config(config, app)
            manager.set_provider(cls.PROVIDER_API, client=client)

        return manager

    # ── управление провайдером ──────────────────────────────────────

    def set_provider(self, provider_type: str, **kwargs):
        """Установить провайдера по типу."""
        self._provider_type = provider_type

        if provider_type == self.PROVIDER_API:
            client = kwargs.get("client")
            if client is None:
                from .llm_client import LLMClient
                client = LLMClient.from_config(self.config)
            self._provider = APIProvider(client)
            # Sync temperature/max_tokens from the client
            self.temperature = client.temperature
            self.max_tokens = client.max_tokens
            self.timeout = client.timeout

        elif provider_type == self.PROVIDER_EXE:
            self._provider = EXEProvider(
                exe_path=kwargs.get("exe_path", ""),
            )

        else:
            raise ValueError(f"Неизвестный тип провайдера: {provider_type}")

    @property
    def model(self) -> str:
        if self._provider is None:
            return "?"
        return self._provider.model

    @property
    def provider_type(self) -> Optional[str]:
        return self._provider_type

    # ── основные методы ──────────────────────────────────────────────

    async def chat_once(self, messages: List[Dict], **opts) -> str:
        if self._provider is None:
            raise RuntimeError("Провайдер не выбран. Укажите источник LLM в настройках.")
        return await self._provider.chat_once(messages, **opts)

    async def chat_stream(self, messages: List[Dict], **opts) -> AsyncIterator[str]:
        if self._provider is None:
            raise RuntimeError("Провайдер не выбран. Укажите источник LLM в настройках.")
        async for chunk in self._provider.chat_stream(messages, **opts):
            yield chunk

    def generate(self, prompt: str, **kwargs) -> str:
        """Синхронная генерация (полезно для CLI/тестов)."""
        if self._provider is None:
            raise RuntimeError("Провайдер не выбран.")
        return self._provider.generate(prompt, **kwargs)

    # ── вспомогательные ──────────────────────────────────────────────

    async def ping(self) -> bool:
        """Проверить доступность провайдера."""
        if self._provider is None:
            return False
        if isinstance(self._provider, APIProvider):
            cl = getattr(self._provider, "_client", None)
            if cl is not None and hasattr(cl, "ping"):
                return await cl.ping()
            return False
        # EXE — считаем доступным, если провайдер создан
        return True

    async def list_models(self) -> Tuple[List[str], Optional[str]]:
        """Список доступных моделей у текущего провайдера."""
        if isinstance(self._provider, APIProvider):
            cl = getattr(self._provider, "_client", None)
            if cl is not None and hasattr(cl, "list_models"):
                return await cl.list_models()
            return [], "API провайдер не активен"
        return [], "Список моделей недоступен для этого провайдера"
