# AGENTS.md — Контекст для AI-агентов (Codex, Claude Code, etc.)

Этот файл — точка входа для любого AI-агента, продолжающего работу над проектом.
Активный план задач и прогресс по этапам → **`ROADMAP.md`**.

---

## Проект

**missed-call-bot** — Telegram-бот для мебельной компании «Neoclassica» (Узбекистан).

Три основных модуля:
1. **Utel webhook** — отслеживает пропущенные звонки, уведомляет операторов.
2. **BILLZ аналитика** — еженедельный AI-дайджест продаж/остатков (по понедельникам 09:00).
3. **Hermes AI** — «AI руководитель отдела продаж (РОП)», встроен в Telegram-чат. Анализирует сделки AmoCRM, отвечает на вопросы команды, даёт скрипты и рекомендации.

---

## Стек

| Слой | Технология |
|---|---|
| Web / API | FastAPI (lifespan) |
| Telegram | aiogram 3.x, long-polling |
| База данных | SQLAlchemy 2.0 async (PostgreSQL на Railway, SQLite локально) |
| Планировщик | APScheduler (AsyncIOScheduler) |
| AI (Hermes) | DeepSeek Chat через OpenAI-compatible SDK (`openai.AsyncOpenAI`) |
| AmoCRM | REST API v4, **read-only** (long-lived token) |
| POS | BILLZ REST API |
| Телефония | Utel webhook |
| Деплой | Railway — автодеплой при push в `main` |

---

## Как запустить локально

```bash
cp .env.example .env         # заполнить переменные
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Entrypoint: `app/main.py` (`lifespan` → init_db → start_scheduler → start_polling).
Procfile: `web: uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

---

## Структура

```
app/
  config.py          — Pydantic Settings (все env vars)
  models.py          — SQLAlchemy ORM модели
  db.py              — AsyncSession factory
  repository.py      — CRUD-операции
  scheduler.py       — APScheduler jobs
  telegram.py        — send_to_user(), send_notification(), get_dispatcher()
  amocrm/
    client.py        — AmoCRM REST клиент (READ-ONLY: _get only)
  billz/
    client.py        — BILLZ REST клиент
    weekly_ai.py     — AI-анализ недельного дайджеста
  crm/
    client.py        — CRM auth сервис (login→access_token→M2M, не AmoCRM)
  bot/
    hermes_handlers.py — Telegram router для Hermes (регистрируется последним)
    admin_handlers.py
    menu.py          — Reply/inline keyboards

hermes/
  agent.py           — Основная точка входа: ask(tg_user_id, text, ...)
                       Системный промпт "Hermes — AI РОП Neoclassica"
                       Tool-loop: до 10 итераций, авто-вызов skills
  audit.py           — run_audit(amocrm_user_id) → list[DealAnalysis]
                       DealAnalysis: lead_id, lead_name, heat, score, reason,
                       next_step, days_inactive, lead_price, status_name, amocrm_link
                       Кэш 4h в HermesAuditCache (DB)
  digest.py          — format_digest(), format_deal_card(), format_top_hot()
  llm.py             — LLMClient singleton, читает DEEPSEEK_* vars
  context.py         — История чата по tg_user_id (DB)
  skills/
    loader.py        — auto-discover: сканирует hermes/skills/*.py
    *.py             — каждый файл: SCHEMA (dict) + async run(params, context)
```

---

## Conventions

### RBAC
```
admin (3) > manager (2) > seller (1)
```
«РОП» — это **только промпт-персона** Hermes, не реальная роль в БД.
Seller видит сделки, но не видит маржу/прибыль (проверяется в agent.py через `role_restriction`).

### Skills (Hermes tools)
Каждый файл в `hermes/skills/*.py` должен экспортировать:
```python
SCHEMA = {
    "name": "skill_name",
    "description": "...",
    "parameters": { "type": "object", "properties": {...}, "required": [...] }
}

async def run(params: dict, context: dict) -> dict:
    ...
```
`context` всегда содержит `{"llm": LLMClient, ...}`. В `agent.ask()` также передаются `amocrm_user_id`, `tg_user_id`, `role`.

### Telegram-сообщения
- Формат: **HTML** (`parse_mode="HTML"`), не Markdown.
- Теги: `<b>`, `<i>`, `<code>`, `<pre>`, `<a href="...">`.
- Весь user-facing текст — **на русском языке**.

### AmoCRM клиент (app/amocrm/client.py)
- **Только чтение** — методы: `get_account`, `get_pipelines`, `get_users`, `get_leads`, `get_active_leads`, `get_lead_notes`, `get_lead_tasks`.
- Нет методов `_post` / `_patch`. Запись в AmoCRM — **запрещена в v1**.
- Auth: `AMOCRM_LONG_LIVED_TOKEN` (Bearer). При 401/403 → уведомление admin.

### Deal heat scoring (hermes/skills/analyse_deal_heat.py)
```
hot  → days_inactive <= 3 ИЛИ price >= 500_000  → score = max(7, 10 - days)
warm → days_inactive 4–30                        → score = max(4, 7 - days // 2)
cold → days_inactive > 30                        → score = max(1, 4 - days // 10)
```

---

## LLM backend

Hermes использует **DeepSeek** через OpenAI-compatible API:
```
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```
`hermes/llm.py` использует `openai.AsyncOpenAI`. Чтобы переключиться на Claude (Anthropic),
нужно сменить SDK (openai → anthropic) — это не drop-in замена.

---

## Ключевые env vars (Railway Variables)

```
BOT_TOKEN
TELEGRAM_CHAT_ID
DATABASE_URL                    # PostgreSQL Railway
AMOCRM_SUBDOMAIN               # neocassica
AMOCRM_LONG_LIVED_TOKEN        # обновлять при 401/403
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_MODEL
BILLZ_SECRET
BILLZ_COMPANY_ID
WEBHOOK_SECRET                 # Utel
TIMEZONE=Asia/Tashkent
ADMIN_USER_ID                  # Telegram ID администратора
```

---

## Активная работа

→ Смотри **`ROADMAP.md`** — там все этапы с `[x]` (выполнено) и `[ ]` (в работе).
