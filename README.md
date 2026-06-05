# Missed Call Bot

Telegram-бот для уведомлений о пропущенных звонках от Utel.uz.

Принимает webhook от IP-АТС → фильтрует пропущенные звонки → отправляет
уведомление в Telegram-группу операторов.

---

## Быстрый старт (локально)

```bash
# 1. Создать и активировать виртуальное окружение
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 2. Установить зависимости
pip install -r requirements.txt

# 3. Скопировать шаблон конфига
copy .env.example .env   # Windows
# cp .env.example .env   # macOS/Linux

# 4. Заполнить .env (токен бота, chat_id, webhook_secret)

# 5. Запустить
uvicorn app.main:app --reload --port 8000
```

---

## Получить chat_id группы операторов

1. Добавьте бота в группу.
2. Напишите любое сообщение в группе.
3. Откройте в браузере:
   ```
   https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
   ```
4. Найдите `"chat": {"id": -1001234567890}` — это и есть `TELEGRAM_CHAT_ID`.
5. Для тем (topics) — найдите `"message_thread_id"` и запишите в `TELEGRAM_THREAD_ID`.

---

## Конфигурация (`.env`)

| Переменная | Обязательная | Описание |
|---|---|---|
| `BOT_TOKEN` | ✅ | Токен от @BotFather |
| `TELEGRAM_CHAT_ID` | ✅ | ID группы операторов (отрицательный) |
| `TELEGRAM_THREAD_ID` | — | ID темы в супергруппе (если нужна тема) |
| `WEBHOOK_SECRET` | ✅ | Секрет для проверки запросов от Utel |
| `WEBHOOK_SECRET_HEADER` | — | Имя заголовка с секретом (по умолч. `X-Webhook-Secret`) |
| `TIMEZONE` | — | Таймзона для времени звонков (по умолч. `Asia/Tashkent`) |
| `LOG_LEVEL` | — | `DEBUG` / `INFO` / `WARNING` (по умолч. `INFO`) |

---

## Эндпоинты

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/` | Проверка — бот жив |
| `GET` | `/health` | Healthcheck для Railway |
| `POST` | `/webhook/utel` | Приём webhook от Utel (требует секрет в заголовке) |

---

## Тестирование через curl

### Пропущенный звонок (ожидаем уведомление в Telegram)
```bash
curl -X POST http://localhost:8000/webhook/utel \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: <ваш_секрет>" \
  -d '{
    "caller": "998901234567",
    "timestamp": 1749041520,
    "wait": 45,
    "status": "no answer",
    "direction": "inbound",
    "call_id": "test-001"
  }'
# Ожидаем: {"ok": true, "sent": true}
```

### Неверный секрет → 403
```bash
curl -X POST http://localhost:8000/webhook/utel \
  -H "X-Webhook-Secret: wrong" \
  -d '{}'
```

### Отвеченный звонок → тихо пропускаем
```bash
curl -X POST http://localhost:8000/webhook/utel \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: <ваш_секрет>" \
  -d '{"caller": "998901234567", "status": "answered", "call_id": "skip-001"}'
# Ожидаем: {"skipped": true, "reason": "not_missed"}
```

### Отсутствуют поля → устойчивая обработка
```bash
curl -X POST http://localhost:8000/webhook/utel \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: <ваш_секрет>" \
  -d '{"status": "missed"}'
# Ожидаем: уведомление с "Номер: неизвестен" и "Ожидание: —"
```

### Healthcheck
```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

> **PowerShell:** замените `\` на `` ` `` для переноса строк и используйте `curl.exe` вместо `curl`.

---

## Запуск тестов

```bash
pytest tests/ -v
```

---

## Деплой на Railway

1. Создайте репозиторий на GitHub и сделайте `git push`.
2. В Railway: **New Project → Deploy from GitHub repo**.
3. Railway сам определит Python через `requirements.txt` (Nixpacks).
4. В разделе **Variables** добавьте все переменные из таблицы выше.
5. **Settings → Networking → Generate Domain** → скопируйте URL.
6. Передайте Utel:
   - Webhook URL: `https://<app>.up.railway.app/webhook/utel`
   - Имя заголовка: значение `WEBHOOK_SECRET_HEADER`
   - Значение секрета: значение `WEBHOOK_SECRET`

---

## Адаптация под реальный payload Utel

После первого реального звонка (с настроенным `LOG_LEVEL=DEBUG`) в логах
Railway появится сырой payload. Сверьтесь с таблицей в `app/schemas.py`
(секция `extract_from_raw`) и при необходимости добавьте нужные имена полей
в списки `pick(...)`. Словарь статусов пропущенных — `MISSED_STATUSES`
в том же файле.
