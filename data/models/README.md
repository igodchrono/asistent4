# Модели не хранятся в git

Чекпоинты HuggingFace Trainer (`checkpoint-*`, `optimizer.pt`, `*.safetensors`) сюда не кладём — репозиторий иначе весит сотни мегабайт.

## Голос (STT)

Скачайте [vosk-model-small-ru-0.22](https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip)
и распакуйте в `data/vosk-model-small-ru-0.22/`.

Silero TTS подтягивается через `torch.hub` при первом запуске озвучки.

## Эмоции / intent-классификатор

Рантайм использует правила `data/core/intents.py`, не нейросеть.
Если тренируете свои веса — держите их локально или на Hugging Face, не в этом репо.
