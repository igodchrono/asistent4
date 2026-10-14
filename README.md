# Лисичка — локальный ассистент

Десктопный чат с локальной LLM (LM Studio / любой OpenAI-compatible API), плагинами и карточками персонажей.

Личный 18+ компаньон **и** рабочий ассистент: кнопка «🦊 Компаньон / 💼 Работа» или фразы «давай по делу» / «режим лисы».

**Только Windows 10/11.** `pc_control` использует `taskkill` / `os.startfile` / PowerShell. Скрипты запуска — `.bat`. Linux/macOS не поддерживаются.

## Быстрый старт

1. Установите [Python 3.10+](https://www.python.org/downloads/) **или** положите портативный Python в папку `python\` рядом с `start.bat`.
2. Поставьте [LM Studio](https://lmstudio.ai/), загрузите модель, Start Server (`http://127.0.0.1:1234`).
3. Зависимости:

```bat
install.bat
```

или:

```bat
pip install -r requirements.txt
```

4. Запуск: `start.bat`

Голос (Vosk + Silero) — отдельно:

```bat
pip install -r data\REQUIREMENTS_VOICE.txt
```

Модель Vosk (русский, ~50 МБ) скачайте сами и распакуйте в `data\vosk-model-small-ru-0.22\`:

https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip

Чекпоинты обучения и `optimizer.pt` в git не входят. Голосовые/эмбеддинг-модели — Hugging Face / релизы, не репозиторий. См. [data/models/README.md](data/models/README.md).

## Что внутри

| | |
|---|---|
| Чат | пузыри, markdown, список дней, стриминг токенов |
| Режим | компаньон 18+ / работа — кнопка или фраза |
| Intent | `data/core/intents.py` — правила, LLM только если правило молчит |
| ПК | песочница по умолчанию: блокнот и калькулятор |
| Поиск | один лучший результат: заголовок, описание, ссылка; картинка в чат только если просят |
| Память | SQLite: `personas/characters/<id>/memory/memory.db`, ~40 реплик в контексте |
| Персонажи | карточки в `data/personas/characters/` |

Настройки: `data/settings.json` (копируется из `settings.example.json` при первом запуске). В git не коммитится.

## Структура

```
start.bat / install.bat
data/main.py          — точка входа
data/core/            — LLM, chat engine, intents, plugin loader
data/plugins/         — плагины
data/personas/        — персонажи
data/gui.py           — окно чата
```

## Лицензия

MIT — см. [LICENSE](LICENSE).

## Контент-политика

Ассистент не применяет фильтры контента на уровне кода.
Ответственность за контент-политику полностью лежит на LLM
и системном промте персонажа.

Если нужно ограничить контент:
1. Используйте LLM с соответствующим alignment.
2. Настройте системный промт персонажа.
3. Не добавляйте фильтры в код — это нарушает архитектуру.

Все настройки фильтрации в settings.json — информационные.
См. [data/CONTENT_POLICY.md](data/CONTENT_POLICY.md).
