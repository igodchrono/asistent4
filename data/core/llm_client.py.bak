# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from urllib.parse import urlparse
import aiohttp


def normalize_api_url(url: str) -> str:
    """Приводит URL к виду .../v1 (как у OpenAI / LM Studio)."""
    u = (url or "").strip()
    if not u:
        return "http://127.0.0.1:1234/v1"
    if not u.startswith(("http://", "https://")):
        u = "http://" + u
    u = u.rstrip("/")
    # уже есть /v1
    if u.endswith("/v1") or "/v1/" in u:
        # обрезать всё после /v1
        idx = u.find("/v1")
        return u[: idx + 3]
    parsed = urlparse(u)
    # host:port или host:port/something
    if parsed.path in ("", "/"):
        return u + "/v1"
    # если указали .../chat/completions — отрезать
    if u.endswith("/chat/completions"):
        return u[: -len("/chat/completions")].rstrip("/") or (u + "/v1")
    return u + "/v1"


class LLMClient:
    """Универсальный LLM-клиент: local (LM Studio) или external (proxyapi.ru и др.)."""

    PROVIDER_LOCAL = "local"
    PROVIDER_EXTERNAL = "external"

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        provider: str = PROVIDER_LOCAL,
        temperature: float = 0.4,
        max_tokens: int = 1000,
        timeout: float = 300,
    ):
        self.api_url = normalize_api_url(api_url)
        self.api_key = api_key or "lm-studio"
        self.model = model or "local-model"
        self.provider = provider or self.PROVIDER_LOCAL
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    @classmethod
    def from_config(cls, config, app=None) -> "LLMClient":
        """Создаёт клиента на основе конфига и настроек плагина llm_provider."""
        provider = cls.PROVIDER_LOCAL
        api_url = str(getattr(config, "API_URL", "http://127.0.0.1:1234/v1"))
        api_key = str(getattr(config, "API_KEY", "lm-studio"))
        model = str(getattr(config, "MODEL_NAME", "local-model"))

        # Если есть app с плагином llm_provider — читаем его настройки
        if app is not None:
            try:
                prov = app.get_plugin_setting("llm_provider", "provider", "local")
                if prov in (cls.PROVIDER_LOCAL, cls.PROVIDER_EXTERNAL):
                    provider = prov
                if provider == cls.PROVIDER_EXTERNAL:
                    ext_url = app.get_plugin_setting(
                        "llm_provider", "external_api_url",
                        "https://api.proxyapi.ru",
                    )
                    ext_key = app.get_plugin_setting(
                        "llm_provider", "external_api_key", "",
                    )
                    ext_model = app.get_plugin_setting(
                        "llm_provider", "external_model", "gpt-4o",
                    )
                    if ext_url:
                        api_url = str(ext_url)
                    if ext_key:
                        api_key = str(ext_key)
                    if ext_model:
                        model = str(ext_model)
            except Exception:
                pass

        return cls(
            api_url=api_url,
            api_key=api_key,
            model=model,
            provider=provider,
            temperature=float(getattr(config, "TEMPERATURE", 0.4) or 0.4),
            max_tokens=int(getattr(config, "MAX_TOKENS", 1000) or 1000),
            timeout=float(getattr(config, "LLM_TIMEOUT", 300) or 300),
        )

    def _headers(self) -> Dict[str, str]:
        if self.provider == self.PROVIDER_EXTERNAL:
            return {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @property
    def _provider_label(self) -> str:
        return "External API" if self.provider == self.PROVIDER_EXTERNAL else "LM Studio"

    async def list_models(self) -> Tuple[List[str], Optional[str]]:
        """GET /models. Возвращает (имена, ошибка|None)."""
        urls = [
            f"{self.api_url}/models",
            f"{self.api_url.rstrip('/v1')}/v1/models",
            f"{self.api_url.rstrip('/v1')}/models",
        ]
        # unique preserve order
        seen = set()
        try_urls = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                try_urls.append(u)
        last_err = None
        timeout = aiohttp.ClientTimeout(total=8, sock_connect=4)
        async with aiohttp.ClientSession() as session:
            for url in try_urls:
                try:
                    async with session.get(url, headers=self._headers(), timeout=timeout) as resp:
                        text = await resp.text()
                        if resp.status != 200:
                            last_err = f"{url} → HTTP {resp.status}: {text[:200]}"
                            continue
                        data = json.loads(text)
                        models = data.get("data") or data.get("models") or []
                        names = []
                        for m in models:
                            if isinstance(m, dict):
                                names.append(str(m.get("id") or m.get("name") or m))
                            else:
                                names.append(str(m))
                        if names:
                            return names, None
                        last_err = f"{url} → пустой список моделей"
                except Exception as e:
                    last_err = f"{url} → {type(e).__name__}: {e}"
        return [], last_err or "не удалось получить модели"

    async def ping(self) -> bool:
        names, err = await self.list_models()
        return bool(names)

    async def chat_stream(self, messages: List[Dict[str, Any]], **opts) -> AsyncIterator[str]:
        payload = {
            "model": opts.get("model") or self.model,
            "messages": messages,
            "temperature": opts.get("temperature", self.temperature),
            "max_tokens": opts.get("max_tokens", self.max_tokens),
            "stream": True,
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout, sock_connect=8)
        url = f"{self.api_url}/chat/completions"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=self._headers(), json=payload, timeout=timeout
                ) as resp:
                    if resp.status != 200:
                        err = await resp.text()
                        yield (
                            f"[Ошибка API {resp.status}] {url}\n"
                            f"model={payload['model']}\n{err[:500]}\n"
                            f"Проверьте соединение: {self._provider_label}, API URL = {self.api_url}, модель загружена."
                        )
                        return
                    async for raw in resp.content:
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                            chunk = (
                                obj.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content")
                                or ""
                            )
                            if chunk:
                                yield chunk
                        except Exception:
                            continue
        except Exception as e:
            # fallback без stream
            try:
                text = await self.chat_once(messages, **opts)
                yield text
            except Exception as e2:
                yield (
                    f"[Нет связи с LLM]\n"
                    f"URL: {url}\n"
                    f"stream: {type(e).__name__}: {e}\n"
                    f"fallback: {type(e2).__name__}: {e2}\n"
                    f"1) В LM Studio нажмите Start Server\n"
                    f"2) URL вида http://127.0.0.1:1234/v1 (local) или https://api.proxyapi.ru (external)\n"
                    f"3) Провайдер: {self._provider_label}, модель: {self.model}"
                )

    async def chat_once(self, messages: List[Dict[str, Any]], **opts) -> str:
        payload = {
            "model": opts.get("model") or self.model,
            "messages": messages,
            "temperature": opts.get("temperature", self.temperature),
            "max_tokens": opts.get("max_tokens", self.max_tokens),
            "stream": False,
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout, sock_connect=8)
        url = f"{self.api_url}/chat/completions"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=self._headers(), json=payload, timeout=timeout
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}: {text[:400]}")
                data = json.loads(text)
                return (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content")
                    or ""
                )

