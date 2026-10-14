# Отчёт об удалении цензуры из кода

## Дата
2026-10-14

## Что было найдено

| # | Файл | Тип | Описание | Критичность |
|---|------|-----|----------|-------------|
| 1 | data/core/_guard.py:7-8 | hardcode | _MODE_LOCK, _MODE_NSFW — принудительная контент-политика | 🔴 |
| 2 | data/core/_guard.py:11-39 | moderation | parse_character_policy() — full_censor, NSFW-mode | 🔴 |
| 3 | data/core/_guard.py:42-53 | moderation | _active_cid() | 🟡 |
| 4 | data/core/_guard.py:56-70 | moderation | character_policy() — запись в state | 🔴 |
| 5 | data/core/_guard.py:73-74 | moderation | character_is_nsfw() — проверка | 🔴 |
| 6 | data/core/policy.py:1-3 | moderation | Re-export модуля цензуры | 🟡 |
| 7 | data/core/mode.py:97-106 | system_prompt | Инструкции LLM: "без флирта, эротики", "Не предлагай 18+" | 🟡 |
| 8 | data/core/chat_engine.py:192 | moderation | Поле nsfw в контекстной карте | 🟡 |
| 9 | data/plugins/persona/plugin.py:57-59 | blacklist | _NSFW_FAMILIES — чёрный список анимаций | 🔴 |
| 10 | data/plugins/persona/plugin.py:162 | blacklist | self._forbidden — чёрный список анимаций | 🔴 |
| 11 | data/plugins/persona/plugin.py:164 | moderation | self._nsfw — NSFW-гейт | 🔴 |
| 12 | data/plugins/persona/plugin.py:170-172 | moderation | _nsfw_ok() — NSFW-проверка | 🔴 |
| 13 | data/plugins/persona/plugin.py:392-393 | moderation | Блокировка NSFW-анимаций в _family_pool | 🔴 |
| 14 | data/plugins/persona/plugin.py:414-416 | blacklist | Блокировка по _forbidden в _resolve | 🔴 |
| 15 | data/plugins/persona/plugin.py:438-444 | moderation | Блокировка NSFW-анимаций в match_prose | 🔴 |
| 16 | data/plugins/persona/plugin.py:577-630 | config | Чтеые nsfw: и запрещённых анимаций из карточки | 🔴 |
| 17 | data/plugins/screen/plugin.py:740-745 | modertion | characteris_nsfw гейтинг эмоций | 🟡 |
| 18 | data/plugis/creen/pluginpy:760-761 | moderation | amen flir onsfw" >" | | 
| 19 | data/plugis/ompanion/plugin.py:1 | modertin | hracter_nsfw-гейтинг флирт-мода | |
| -3| data/plugs/compion/plugin.y:17-18 | moderation | charactensfw"}"plaufl" ہ "clm" | |
|  [-. | daa/plugins/companion/plugin.py:318-326 | moderation | NSFW-gte for scene="nsfw" | 🟡 |
| 22 | data/plugins/companion/plugin.py:551 | moderation | characternsfw for mood selection | 🟡 |
| 23 | data/core/generation_metrics.py:65,210-212 | moderation | nsfw_score e nsfw штраф в overall (не активен) | 🟢 |
| 24 | data/plugins/character_log/plugin.py:25-30 | moderation | state['characterns' = character_is_nsfw() | 🟡 |

## Что удалено

| № | Изменение | Файлы |
|---|----------|-------|
| 1 | Удалена принудительная контент-политика (_MODE_LOCK, _MODE_NSFW, full_censor) | data/core/_guard.py |
| 2 | Функции _guard оставлены как информационные заглушки (не влияют на код) | data/core/_guard.py |
| 3 | Обновлён docstring в policy.py | data/core/policy.py |
| 4 | Удалены инструкции LLM из system_addendum (work mode) | data/core/mode.py |
| 5 | Убрано поле 'nsfw' из контекстной карты | data/core/chat_engine.py |
| 6 | Удалены _NSFW_FAMILIES, _forbidden, _nsfw, _nsfw_ok() | data/plugins/persona/plugin.py |
| 7 | Убран NSFW-гейтинг в _family_pool, _resolve, _match_pose | data/plugins/persona/plugin.py |
| 8 | Убрано чтение nsfw: и запрещённых анимаций из карточки | data/plugins/persona/plugin.py |
| 9 | Убран NSFW-гейт emotions в screen/plugin.py | data/plugins/screen/plugin.py |
| 10 | Убраны NSFW-проверки в companion/plugin.py (4 места) | data/plugins/companion/plugin.py |
| 11 | Убрана установка state['character_nsfw'] | data/plugins/character_log/plugin.py |
| 12 | Убран nsfw_score и штраф в generation_metrics.py | data/core/generation_metrics.py |
| 13 | Добавлена секция content в settings.json | data/settings.json |

## Что оставлено

1. **net_guard.py** — SSRF-защита (безопасность, не цензура)
2. **tool_args.py** — _FORBIDDEN (безопасность выполнения, confirmed/force/internal)
3. **prompt_builder.py:**43-46 — _NEGATIVE_BASE (технические негативные промты для SD)
4. **prompt_builder.py:**48-55 — _STYLE_NEGATIVE_MAP (технические негативные промты по стилю)
5. **prompt_builder.py:**412-416 — forbidden в negative prompt (легитимные no X для image gen)
6. **selftest_personas_live.py:**38,76 — тесты ожидающие отказ от LLM (тестируется LLM, не код)
7. **selftest_human_live.py** — character_nsfw_flag (читает информационное поле)

## Архитектурное решение

**Ответственность за контент полностью перенесена на LLM.**

Код ассистента:
- Не содержит чёрных списков слов
- Не содержит модерационных функций
- Не фильтрует ввод/вывод LLM
- Не принимает решений "можно/нельзя"
- Не NSFW-классификаторов
- Не даёт LLM инструкций "отказывайся от", "не обсуждай", "будь безопасной"

## Как настроить контент

1. Выбрать LLM с нужным alignment
2. Настроить системный промт персонажа (data/personas/characters/<name>/<name>.md)
3. Не добавлять фильтры в код

## Проверка

| Проверка | Результат |
|----------|-----------|
| Компиляция всех изменённых файлов | ✅ OK (0 ошибок) |
| Поиск "не могу помочь/не имею права/против правил" | ✅ Только в тесте (ожидание от LLM) |
| Поиск BBANNED_WORDS/BLACKLIST/PROHIBITED | ✅ Пусто |
| Поиск ccheck_content/modere/ftr_espnse/snitie_output | ✅ Пусто |
| Поиск acractr_nsfw в проектном коде | ✅ Только в тестах |
| Бэкап изменённых файлов | ✅ data/backup_censorship_removal_20261014_005829/ |

## Лог изменений

data/logs/censorship_removal.log — 14 записей об изменениях.
