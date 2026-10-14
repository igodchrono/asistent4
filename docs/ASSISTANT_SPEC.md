# Лисичка (asistent4) — техническое описание для другой модели

Репозиторий: https://github.com/igodchrono/asistent4
Снимок: коммит `9bff41a` (ветка `main`).
Язык интерфейса и команд пользователя: русский.
Платформа: только Windows 10/11. Linux/macOS не поддерживаются.

Это не веб-приложение. Это локальный десктопный чат на Python.
Окно — PyQt5. Мозг — локальная LLM по OpenAI-совместимому API (обычно LM Studio на `http://127.0.0.1:1234/v1`) либо внешний API, либо EXE-провайдер.
Картинки рисует ComfyUI на `http://127.0.0.1:8188`, не облачный генератор.

Код не фильтрует 18+ контент. Границы задаёт карточка персонажа и сама модель.
См. `data/CONTENT_POLICY.md`.

---

## 1. Как запускается

Корень репозитория — скрипты. Код приложения — папка `data/`. `start.bat` делает `cd data` и запускает `python\pythonw.exe data\main.py` (портативный Python лежит рядом со `start.bat`, не внутри `data`).

Порядок в `data/main.py`:

1. `settings_manager.apply_to_config` читает `data/settings.json` (если нет — копирует `data/settings.example.json`).
2. `character_catalog` выбирает активного персонажа. По умолчанию `лисичка`.
3. PyQt5, тёмная тема, splash.
4. `PluginLoader` поднимает плагины из `data/plugins/<id>/`.
5. `LLMManager` + `ChatEngine`.
6. `gui.ChatWindow`.

Рабочая директория процесса — `data/`. Импорты вида `import config`, `from core...`, `from plugins...` считаются от `data/`.

---

## 2. Дерево папок (исходники, не каждый PNG)

```
asistent4/
  start.bat              запуск окна
  start_debug.bat        запуск с консолью
  stop_lisichka.bat      остановка
  install.bat            pip install
  train.bat              обучение (не ядро чата)
  requirements.txt       корень; рабочие зависимости — data/requirements.txt
  README.md
  LICENSE                MIT

  data/
    main.py              точка входа
    gui.py               окно чата
    config.py            константы: API, модель, режим, активный персонаж
    character_catalog.py список персонажей и карточки
    plugin_catalog.py
    settings_manager.py  load/save settings.json, белый список ключей
    settings_dialog.py   окно настроек
    settings.example.json
    settings.json        локальный файл пользователя (может быть в git у этой копии)
    requirements.txt     PyQt5, qasync, aiohttp, Pillow, mss
    REQUIREMENTS_VOICE.txt  Vosk + Silero, ставится отдельно
    CONTENT_POLICY.md
    logger.py
    splash.py / splash.jpg

    core/                ядро, без GUI-плагинов
    plugins/             каждый плагин — папка с plugin.py и def register()
    personas/characters/<id>/   карточка, кадры, память
    browser/             встроенный Qt WebEngine (вкладки, DOM, JS)
    settings_ui/         вкладки настроек: основное, плагины, персонаж
    ui/theme.py
    prompts/             библиотека стилей и якорей для промптов картинок
    models/README.md     голосовые/эмбеддинг модели сюда не коммитятся

    sessions/            JSON сессий диалога
    generations/history.jsonl   история генераций картинок
    preferences/         вкусы пользователя для выбора с экрана
    assistant_picks/     вырезанные с экрана кропы
    generated/<персонаж>/  png от ComfyUI
    attachments/ГГГГ-ММ-ДД/     файлы и скачанные картинки в чат
    cache/search/        кэш поиска (создаётся при работе)
    logs/                создаётся при работе
```

Не путать с исходниками: `data/generated/`, `data/assistant_picks/`, `data/personas/characters/*/images/*.png`, `data/backup_*`. Это данные, не логика.

---

## 3. Поток одного сообщения

`ChatEngine.handle_user` в `data/core/chat_engine.py`.

1. `ConversationContext.resolve_reference` раскрывает «это», «то же самое», «ещё раз», «как раньше».
2. Ход пишется в контекст диалога и в короткую историю `self.history`.
3. Фраза может переключить режим `companion` ↔ `work` (`core/mode.py`).
4. Плагины по очереди получают `on_user_message`. Первые в очереди: `persona`, `companion`, `character_log`, `attachments`, `screen`. Если плагин вернул `HookResult(handled=True)`, классификатор не вызывается. Так генерация картинок часто перехватывается `phone_media` целиком.
5. Иначе намерение: сначала правила `core/intents.py`. Если правило молчит — LLM-классификатор по тексту `INTENT_SCHEMA` в `chat_engine.py`. Ответ модели режется `sanitize_intent` и `core/tool_args.py` (белый список аргументов, нельзя подсунуть `confirmed`/`force`).
6. Намерение → инструмент `app.tools[имя]`. Инструменты регистрируют плагины.
7. Если намерение `chat` — обычный ответ LLM. В полный режим в системный промпт идут карточка персонажа, память, настроение. Режим `LLM_CONTEXT_MODE=lean` шлёт в модель только фразу пользователя.
8. Ответ попадает в историю, в SQLite-память персонажа и в `ConversationContext`.

Маркер картинки в тексте ответа: `[фото: D:\полный\путь.png]`.
Маркер файла: `[файл: путь]`.
`gui.py` вырезает эти маркеры и рисует превью. Клик по картинке — полный экран. Ссылки `http(s)` кликабельны.

---

## 4. Ядро `data/core/`

| Файл | Зачем |
|---|---|
| `plugin_api.py` | Базовый `Plugin`, `HookResult`, `AppContext` (`state`, `tools`, `plugins`, `get_active_character`) |
| `plugin_loader.py` | `import plugins.<id>.plugin`, вызов `register()` |
| `chat_engine.py` | Диалог, классификация, вызов инструментов, стрим ответа |
| `intents.py` | Быстрые правила намерений. `None` = пусть решит LLM |
| `intent_schema.py` | Схема для модели |
| `routing.py` | Дочистка «открой её» после поиска |
| `tool_args.py` | Белый список аргументов инструментов |
| `tool_runner.py` | Запуск инструмента |
| `llm_client.py` | HTTP к OpenAI-compatible API, стрим |
| `llm_manager.py` | `APIProvider` или `EXEProvider` |
| `prompt_builder.py` | Сборка системного промпта |
| `mode.py` | `companion` (личный) и `work` (по делу) |
| `policy.py` | Политика; фильтров контента в коде нет |
| `net_guard.py` | HTTP только в публичный интернет. localhost/частные IP режутся, кроме явно разрешённого ComfyUI |
| `reply_format.py` | Снятие служебных `[ANIM:...]` |
| `conversation_context.py` | 20 ходов, тема, сущности, «это/то же самое», сессия в `data/sessions/` |
| `query_parser.py` | Разбор поисковой фразы: тема, картинка/ссылка, one/few/many, «в чат» |
| `search_query.py` | Короткая чистка поискового запроса |
| `search_settings.py` | Веса ранжирования поиска |
| `continuation_detector.py` | «то же самое, но …» для картинки |
| `generation_history.py` | `data/generations/history.jsonl` |
| `prompt_anchors.py` | Якоря прошлого промпта, чтобы правка не переписывала всё |
| `prompt_pipeline.py` / `prompt_enhancer.py` / `prompt_ab.py` | Усиление и сравнение промптов |
| `refine_loop.py` | Цикл «слишком темно / добавь деталей» |
| `feedback_parser.py` | 👍/👎 и «нравится» / «не то» |
| `preference_memory.py` | Вкусы для выбора картинки с экрана, `data/preferences/` |
| `_guard.py` | Служебный сторож |

Хуки плагина: `on_load`, `on_shutdown`, `on_user_message`, `on_before_llm`, `on_after_llm`, `on_character_changed`, `register_tools`, `setup_settings_tab`.

`AppContext.state` — общий словарь на сессию. Важные ключи: `imggen_stage`, `imggen_refs`, `imggen_ref_kind` (`self`/`edit`), `character_self_ref`, `phone_media_last`, `pending_attachments`, `last_attachments`, `last_search_query`, `last_search_results`, `last_search_mode`, `screen_capture_geo`, `last_user_text`, `chat_id`.

---

## 5. Намерения

Правила и LLM могут вернуть только имя из `KNOWN_INTENTS` (`data/core/intents.py`):

- Разговор: `chat`, `deep_think`
- Экран: `describe_screen`, `pick_from_screen`
- Интернет: `web_search`, `search_similar`, `download_image`, `fetch_page`, `fetch_url`, `open_last_search`
- Картинка ComfyUI: `imggen`, `imggen_edit` (инструмент один: `generate_image`)
- Файлы: `text_edit`, `file_list`, `file_get`, `read_uploaded`, `save_file`, `send_file`
- Память: `memory_add`, `memory_list`, `memory_forget`
- Заметки: `note_add`, `note_list`, `note_find`
- Напоминания: `reminder_add`, `reminder_list`
- ПК: `pc_open`, `pc_close`, `pc_volume`, `pc_type`, `pc_search_files`, `pc_search_folders`, `pc_open_found`, `pc_close_last`, `pc_create_text`, `pc_recycle`, `pc_empty_recycle`

Как пользователь это произносит:

| Фраза | Куда идёт |
|---|---|
| «нарисуй кота», «просто картинку» | Новая картинка по тексту, без чужого фото |
| «нарисуй по референсу», «по этому фото», «переделай картинку» | Правка прикреплённого или последнего кадра (workflow edit) |
| «нарисуй лисичку», «нарисуй себя», «хочу тебя увидеть» без сцены | Свой образ. Короткое «покажи себя» шлёт сохранённое `ref.png`. «покажи себя на кровати» рисует её по референсу |
| «запомни, это ты» + вложение | Файл копируется в `personas/characters/<id>/images/ref.png`. В текстовую память фактов не пишется |
| «покажи, как ты меня запомнила» | Отдаёт `ref.png` в чат |
| «то же самое, но в аниме» | Продолжение последней генерации: старый кадр как референс, якоря промпта, меняется только просьба |
| «найди картинки кошек» | Поиск, в чат список из одного лучшего, файл не качается |
| «скинь картинку акулы в чат» | Поиск и скачивание файла в чат |
| «посмотри на экран и выбери» | `image_picker`: регионы на скрине, кроп лучшего |
| «что на экране» | Снимок монитора и описание |
| «открой блокнот», «тише», «напечатай …» | `pc_control` |
| «найди файл на диске D» | Поиск по диску, не интернет |
| «запомни …» / «забудь» | Факты персонажа |
| «запиши: …» | Заметка |
| «напомни через час» | Напоминание. «напомни как тебя зовут» — обычный чат |
| «давай по делу» / «режим лисы» | work / companion |

---

## 6. Плагины `data/plugins/`

У каждого каталога есть `plugin.py` с `class PluginImpl(Plugin)` и `def register()`.

### phone_media — генератор картинок
`workflows/`:
- `qwen21_t2i.json` — текст → картинка
- `qwen21_edit.json` — правка по референсу (и «нарисуй себя»)
- `qwen21_i2i.json`, `qwen_image.json`, `qwen_edit.json` — соседние графы

ComfyUI только на localhost. Промпт собирается один раз и сразу в очередь. Старое вложение из прошлого сообщения не становится референсом само. Референс нужен, если фото приложено к этой фразе, если просят правку, если сцена про самого персонажа, или если это продолжение «то же самое». Пока `imggen_stage` не `idle` (`drafting`, `busy`, `confirm`, `await_prompt`, `await_model`), чужое настроение окна не должно перебивать позу.

### screen — зрение
Снимок монитора через `mss`. Несколько мониторов, геометрия в `state["screen_capture_geo"]`. Реакция на заголовок окна. Выбор картинки с экрана. Сохранение оригинала: правый клик мыши → «сохранить как» / адрес картинки, а не кривой кроп, когда пользователь просит сохранить. `vision.py` регистрирует тот же инструмент `describe_screen` (второй плагин `screen_vision`).

### image_picker — умный кроп с экрана
`screen_analyzer.py` ищет прямоугольники (OpenCV, если есть, иначе PIL). `smart_picker.py` ранжирует. После кропа ждёт «нравится» / «не то» и пишет `PreferenceMemory`. Это не смешивание двух картинок в одну. Это вырезание области со скриншота в `data/assistant_picks/`.

### browser_search — интернет
Открывает вкладку Google/Яндекс/Bing/DuckDuckGo. Параллельно читает DuckDuckGo HTML, Bing Images, Wikimedia, Wikipedia. Ранжирует (`smart_search.py`). В чат — один лучший результат: заголовок, описание, URL. Картинку качает только если просят «в чат» / «скинь» / «скачай». Описания страниц — `description.py` (meta или первый абзац, без выдумок). Кэш `data/cache/search`, лог `data/logs/search_perf.log`.

Отдельно есть пакет `data/browser/`: своё окно на Qt WebEngine (`window.py`, `tab.py`, `profile.py`, `dom_api.py`, `js_bridge.py`). Это не то же самое, что открытие системного Chrome из `browser_search`.

### memory — долгая память
SQLite на персонажа: `personas/characters/<id>/memory/memory.db`. Факты, дневник, история реплик. Переживает перезапуск. «Запомни, это ты» плагин пропускает, этим занимается `phone_media`. Очистка чата в GUI не стирает эту базу.

### persona — лицо
Окно аватара `persona/window.py`. Кадр = PNG в `images/`, имя файла = поза. Тег в тексте `[ANIM:имя]` показывает кадр и из ответа вырезается. Настроение копится из реплики, сцены и того, что она только что сделала, а не из крутилки по таймеру. Во время генерации картинки поза генерации важнее заголовка чужого окна.

### companion — сама пишет в чат
Один комментарий, если экран реально сменился и пользователь молчит. Кулдаун. Своё окно и пустой заголовок пропускает. Настроение одно на сессию, не случайный перебор.

### Остальные
- `attachments` — текст из вложенных файлов подмешивается в LLM.
- `files` — список загрузок, правка txt/md, отдать файл в чат.
- `notes` — заметки.
- `reminders` — напоминания.
- `pc_control` — открыть/закрыть программу, громкость, печать в активное окно, поиск файлов и папок, корзина. Песочница: по умолчанию безопасные цели (блокнот, калькулятор). Опасное закрытие процессов спрашивает.
- `voice` — Vosk (речь в текст) и Silero/pyttsx3 (текст в речь). Модель Vosk кладётся в `data/vosk-model-small-ru-0.22/`, в git её нет.
- `auto_messages` — сама пишет, если долго тишина и LLM жива; иначе заготовленные фразы.
- `rag` — локальный поиск по своим текстам.
- `deep_think` — более длинный ответ на «разбери подробно».
- `llm_provider` — переключение API / EXE.
- `character_log` — лог смены персонажа.

---

## 7. Персонаж

Папка: `data/personas/characters/<id>/`.

- `<id>.md` — личность. Её читает LLM. Команды она не исполняет.
- `poses.json`, `emotions_map.json`, `reactions.json` — кадры и реакции.
- `images/*.png` — кадры. `images/ref.png` — лицо для генерации «себя».
- `notes.md`, `user_patterns.json`, `emotional_memory.json` — заметки поведения.
- `memory/memory.db` — создаётся плагином памяти.

Сейчас в дереве есть `лисичка` (много кадров, включая 18+) и `мила` (меньший набор). Шаблон: `characters/_template/` и `personas/_template_reactions/`.

Новый кадр: положить PNG, имя = значение `[ANIM:...]`, и строку в `poses.json`.

Промпты картинок, не личности: `data/prompts/library/*.yaml`, `quality.yaml`, `styles.yaml`, `successful.jsonl`, `weights.json`.

---

## 8. Окно `data/gui.py`

- Пузыри пользователя и ассистента, markdown, код в рамке.
- Список чатов слева: «＋ чат» создаёт новый, «удалить» удаляет чат. Это не стирает память фактов.
- Кнопки: режим компаньон/работа, вложение файла, отправить, голос, стоп речь, настройки.
- Drag-and-drop файла копирует его в `data/attachments/<дата>/`.
- Картинка в ответе: превью, полный экран, «сохранить как».
- Настройки (`settings_dialog.py` + `settings_ui/`): API, модель, режим контекста полный/слабый, персонаж, включение плагинов.

Режим контекста:
- `full` — карточка, история, факты.
- `lean` — в основную генерацию уходит только фраза пользователя, чтобы экономить токены. Инструменты по правилам при этом живут.

---

## 9. Настройки

`data/config.py` — значения по умолчанию.
`settings_manager.ALLOWED_KEYS` — что из JSON попадает в config: API, модель, температура, режим, активный персонаж, `LLM_CONTEXT_MODE`, `LLM_ECONOMY_MODE`, `PLUGINS`, `PLUGIN_SETTINGS`.
Ключи с префиксами `PLUGIN`, `VOICE_`, `SEARCH_`, `SCREEN_`, `CHAT_` тоже применяются.
Вложенные блоки `search`, `query_parser`, `response_format` читает `core/search_settings.py` сам, даже если `apply_to_config` их в модуль config не кладёт.

Секреты и локальный URL LLM — в `settings.json`, не в коде.

---

## 10. Зависимости

Обязательные (`data/requirements.txt`): PyQt5, qasync, aiohttp, Pillow, mss.
Голос — отдельный файл `REQUIREMENTS_VOICE.txt`.
OpenCV, pytesseract, BeautifulSoup — не обязательны. Поиск парсит HTML стандартным `html.parser`. Выбор областей на экране умеет работать через PIL, если cv2 нет.

Внешние процессы, без которых часть функций молчит:
- LM Studio или другой OpenAI-compatible сервер.
- ComfyUI на 127.0.0.1:8188 и модели графов Qwen, если нужны картинки.
- Браузер Windows, если поиск открывает вкладку.

---

## 11. Чего в коде нет

Чтобы другая модель не дописывала несуществующее:

- Нет облачной генерации картинок. Только ComfyUI.
- Нет смешивания двух картинок режимами blend/overlay/style-transfer как отдельного продукта. «Микс» со скрином в текущем коде — это кроп области (`image_picker`) либо генерация ComfyUI с референсом, не PIL-наложение.
- Нет spaCy/transformers в ядре.
- Нет мультипользовательского сервера и нет аккаунтов.
- Очистка чата не удаляет `memory.db` и не удаляет `ref.png`.
- Плагин, вернувший `handled=True`, обходит классификатор. Новый перехватчик нужно ставить осознанно, иначе он съест чужие фразы.

---

## 12. Куда смотреть, если меняешь поведение

| Задача | Файл |
|---|---|
| Новая фраза-команда | `data/core/intents.py`, затем инструмент плагина |
| Чтобы команда не ушла в болтовню | правило должно вернуть intent, не `None` |
| Генерация / референс / «покажи себя» | `data/plugins/phone_media/plugin.py` |
| Поиск и формат ссылки | `data/plugins/browser_search/plugin.py`, `smart_search.py` |
| «Это» и «то же самое» в диалоге | `data/core/conversation_context.py` |
| Продолжение картинки | `continuation_detector.py`, `prompt_anchors.py`, `generation_history.py` |
| Выбор с экрана | `data/plugins/image_picker/` |
| Настроение и кадр | `data/plugins/persona/plugin.py`, `companion/plugin.py` |
| Что видит модель | `chat_engine.py` (`_lean`, сбор messages), карточка `.md` |
| Окно | `data/gui.py` |
