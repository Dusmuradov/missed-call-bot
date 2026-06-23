# ROADMAP — AmoCRM + Hermes РОП интеграция

> **Для агентов:** читай `AGENTS.md` сначала — там стек, conventions, структура файлов.
> Отмечай этап `[x]` и добавляй commit-хэш/дату после завершения.
> **Не меняй порядок этапов** — каждый следующий зависит от предыдущего.

---

## Цель

Бот следит за сделками всех сотрудников, анализирует ошибки по методологии РОП,
даёт рекомендации и составляет **ежедневный план задач** (P1/P2/P3) для каждого продавца.
Менеджер/РОП получает командную сводку.

Всё — через Telegram (read-only AmoCRM в v1, без записи в CRM).

---

## Зафиксированные решения

| Вопрос | Решение |
|---|---|
| Аудитория плана | Каждый продавец получает личный план + менеджеры получают командную сводку |
| Запись в AmoCRM | **Нет** в v1 — только чтение + рекомендации в Telegram |
| Методология РОП | Методы уже зашиты в `hermes/skills/*.py` — следовать им, не изобретать новое |
| Расписание | Ежедневно утром (новый job рядом с `hermes_morning_digest_job`) |
| Уровень доступа | sellers: P1/P2/P3 план по своим сделкам. managers/admin: командная сводка |

---

## Ключевые строительные блоки (переиспользовать!)

Перед кодингом прочти эти файлы:

| Файл | Что даёт |
|---|---|
| `hermes/audit.py` | `run_audit(amocrm_user_id)` → `list[DealAnalysis]`; `DealAnalysis` dataclass |
| `hermes/skills/analyse_deal_heat.py` | hot/warm/cold + score (thresholds: ≤3дн→hot, ≤30дн→warm, >30→cold) |
| `hermes/skills/coach_manager.py` | РОП-персона ("жёсткий но справедливый РОП"), паттерн strengths/issues/priority_actions |
| `hermes/skills/suggest_next_step.py` | `action + script + urgency` per deal |
| `hermes/skills/analyse_conversation.py` | `sentiment + client_objections` из заметок |
| `hermes/digest.py` | `format_digest()`, `format_deal_card()` — Telegram HTML паттерны |
| `app/amocrm/client.py` | `get_lead_notes()`, `get_lead_tasks()` per lead |
| `app/scheduler.py` | `hermes_morning_digest_job()` — паттерн daily job, `_send_to_managers()` |
| `app/bot/hermes_handlers.py` | `_get_user_with_amo()` helper, команды /hot /tasks |
| `app/telegram.py` | `send_to_user(uid, text)` |
| `app/config.py` | Pydantic Settings — добавлять новые env vars сюда |

---

## Этапы

---

### [x] Stage 0 — Foundations (ВЫПОЛНЕНО)

Всё уже на GitHub в ветке `main`.

- **CRM auth сервис** (`app/crm/client.py`) — login → access_token → M2M token, cache + DB + lock.
  Commit: `2444016`. ⚠️ Переменные `CRM_*` в Railway пока не заданы — сервис не активен.
  Это для отдельной CRM-системы, не AmoCRM.

- **AmoCRM long-lived-token-only** (`app/amocrm/client.py`) — убран весь OAuth/refresh flow.
  `get_valid_client()` → `AmocrmClient(subdomain, long_lived_token)`. Commit: `105db71`.

- **Еженедельный AI-дайджест** (`app/scheduler.py` + `app/billz/weekly_ai.py`) —
  каждый понедельник 09:00 (Asia/Tashkent). Анализирует BILLZ + AmoCRM + Utel за неделю.
  Commits: `ca35307`, `32a8fe3`.

---

### [ ] Stage 1 — ROP error/mistake analysis engine

**Цель:** для каждой активной сделки продавца — обнаружить типовые ошибки продажного процесса
по методологии РОП, уже заложенной в `hermes/skills/`.

**Первый шаг:** прочитай `hermes/skills/coach_manager.py`, `hermes/skills/suggest_next_step.py`,
`hermes/skills/analyse_conversation.py` — именно эти методы должны лечь в основу.

**Создать:** `hermes/rop_errors.py`

```python
# Сигнатура
async def detect_errors(deal: DealAnalysis, tasks: list[dict], notes: list[dict]) -> list[DealError]:
    ...

@dataclass
class DealError:
    severity: str        # "critical" | "warning" | "info"
    code: str            # машиночитаемый код ошибки
    message: str         # человекочитаемое описание
    recommendation: str  # что сделать
```

**Таксономия ошибок** (выровнена с существующими heat-thresholds):

| Код | Условие | Severity |
|---|---|---|
| `no_task` | Нет ни одной открытой задачи у сделки | critical |
| `overdue_task` | Есть задача с `complete_till` < now | critical |
| `stale_hot` | heat=hot, days_inactive > 3 | critical |
| `stale_warm` | heat=warm, days_inactive > 7 | warning |
| `cold_high_value` | heat=cold, lead_price >= 500_000 | warning |
| `stuck_initial` | В начальном статусе воронки > 5 дней | warning |
| `unanswered_objection` | В последней заметке есть возражение (см. `analyse_conversation`) | info |
| `no_contact` | Нет заметок (`notes == []`) | info |

**Переиспользовать:**
- `DealAnalysis` из `hermes/audit.py`
- `call_skill("analyse_conversation", ...)` для обнаружения возражений в заметках
- Heat thresholds из `analyse_deal_heat.py` (не дублировать константы — импортировать или читать поля `DealAnalysis`)

**Acceptance criteria:**
- Функция `detect_errors(deal, tasks, notes)` возвращает `list[DealError]` (пустой список = всё ок).
- Не делает лишних API-запросов к AmoCRM (данные передаются аргументами).
- Покрывается юнит-тестом с fixture-данными (Stage 6).

---

### [ ] Stage 2 — Daily prioritized task plan (per seller)

**Цель:** сформировать личный план продавца на день в формате P1/P2/P3 из аудита + ошибок Stage 1.

**Создать:** `hermes/daily_plan.py`

```python
async def build_daily_plan(
    amocrm_user_id: int,
    tg_user_id: int,
) -> str:
    """Возвращает готовое Telegram HTML-сообщение с планом P1/P2/P3."""
    ...
```

**Логика приоритизации:**

| Приоритет | Критерий |
|---|---|
| 🔴 P1 — Сделать сейчас | `critical` ошибки ИЛИ heat=hot |
| 🟡 P2 — Сделать сегодня | `warning` ошибки ИЛИ heat=warm и days > 5 |
| ⚪ P3 — На контроле | heat=warm без ошибок ИЛИ heat=cold с высокой ценой |

**Формат сообщения** (Telegram HTML):
```
📋 <b>Твой план на сегодня</b>

🔴 <b>P1 — Срочно (N сделок)</b>
• <a href="...">Сделка #123</a> — <i>нет задачи</i>
  → Создать задачу: звонок сегодня до 14:00

🟡 <b>P2 — До конца дня (N сделок)</b>
...

⚪ <b>P3 — На контроле (N сделок)</b>
...
```

**Переиспользовать:**
- `run_audit(amocrm_user_id)` из `hermes/audit.py`
- `detect_errors()` из Stage 1
- `format_deal_card()` из `hermes/digest.py` как образец форматирования
- `suggest_next_step` skill — вызвать для P1-сделок, вставить `script` в план

**Acceptance criteria:**
- `build_daily_plan()` возвращает строку HTML без ошибок для продавца с активными сделками.
- Пустой план (нет сделок) → короткое сообщение «Сделок нет — хорошее время проработать базу».
- Для P1-сделок есть конкретный скрипт/действие (не просто «позвони»).

---

### [ ] Stage 3 — РОП team roll-up (командная сводка)

**Цель:** менеджер/admin получает одно сообщение: у кого какие ошибки, кто отстаёт, топ-риски.

**Создать:** `hermes/rop_rollup.py`

```python
async def build_rop_rollup() -> str:
    """
    Собирает аудиты всех пользователей с amocrm_user_id.
    Возвращает Telegram HTML-сводку для руководителя.
    """
    ...
```

**Логика:**
1. Запросить всех `BotUser` с `amocrm_user_id is not None` и `role in ("seller", "manager")`.
2. Для каждого вызвать `run_audit()` (кэш 4h — не ударит по AmoCRM).
3. По каждому запустить `detect_errors()` (Stage 1).
4. Агрегировать: топ-N сделок в зоне риска; у кого больше всего critical-ошибок; total hot/warm/cold по команде.
5. Добавить coaching-блок — вызвать `coach_manager` skill для отстающего (у кого max critical errors).

**Переиспользовать:**
- `run_audit()` из `hermes/audit.py`
- `detect_errors()` из Stage 1
- `coach_manager.py` паттерн (strengths/issues/priority_actions)
- `_send_to_managers()` из `app/scheduler.py` для доставки

**Acceptance criteria:**
- Если у пользователя нет `amocrm_user_id` — пропускать (не падать).
- Если ни у кого нет активных сделок — краткое «Сделок нет».
- Сводка вмещается в 2 Telegram-сообщения (4096 символов каждое).

---

### [ ] Stage 4 — Scheduling + delivery

**Цель:** ежедневный утренний job — каждый продавец получает свой план, менеджеры — сводку.

**Изменить:** `app/scheduler.py`

Добавить job по образцу `hermes_morning_digest_job()`:

```python
async def rop_daily_plan_job():
    """
    09:00 Asia/Tashkent ежедневно.
    Sellers → build_daily_plan() → send_to_user()
    Managers/admin → build_rop_rollup() → send_to_user()
    """
    ...

# Регистрация:
scheduler.add_job(
    rop_daily_plan_job,
    CronTrigger(hour=settings.rop_plan_hour, minute=5, timezone="Asia/Tashkent"),
    id="rop_daily_plan",
    replace_existing=True,
)
```

**Изменить:** `app/config.py` — добавить `rop_plan_hour: int = 9`

**Изменить:** `.env.example` — добавить `ROP_PLAN_HOUR=9`

**Важно:** job **дополняет**, не заменяет `hermes_morning_digest_job` (они разные по смыслу).

**Acceptance criteria:**
- Sellers получают план в 09:05, менеджеры — сводку в 09:05 (через ±1 мин после sellers).
- Если у продавца нет `amocrm_user_id` — пропустить, не слать пустое сообщение.
- Ошибки в одном пользователе не роняют цикл для остальных (try/except per user).

---

### [ ] Stage 5 — Bot commands + menu + manual HTTP trigger

**Цель:** on-demand доступ — продавец может запросить план в любой момент, руководитель — сводку.

**Изменить:** `app/bot/hermes_handlers.py`

```python
@router.message(Command("plan"), F.chat.type == "private")
async def cmd_plan(message: Message):
    """Seller → личный план P1/P2/P3. Manager/admin → командная сводка."""
    user = await _get_user_with_amo(message)
    if not user:
        return
    if user.role in ("manager", "admin"):
        text = await build_rop_rollup()
    else:
        text = await build_daily_plan(user.amocrm_user_id, user.tg_user_id)
    await message.answer(text, parse_mode="HTML")
```

**Изменить:** `app/bot/menu.py` — добавить кнопку «📋 Мой план» в seller_menu.

**Изменить:** `app/main.py` — добавить ручной HTTP-триггер:

```python
@app.get("/rop/run-plan", tags=["rop"])
async def rop_run_plan():
    """Ручной запуск ROP daily plan — для тестирования."""
    asyncio.create_task(rop_daily_plan_job())
    return {"ok": True, "message": "ROP plan job запущен в фоне"}
```

**Acceptance criteria:**
- `/plan` отвечает seller'у его планом, manager'у — сводкой.
- Кнопка в меню видна seller'у.
- GET `/rop/run-plan` → получение сообщений в Telegram в течение 30 сек.

---

### [ ] Stage 6 — Tests + verification

**Цель:** убедиться, что error detection работает на fixture-данных до деплоя.

**Создать:** `tests/test_rop_errors.py`

```python
# Fixture: сделка без задач → должна дать DealError(code="no_task", severity="critical")
# Fixture: сделка с overdue task → DealError(code="overdue_task")
# Fixture: cold deal с price >= 500_000 → DealError(code="cold_high_value")
# Fixture: deal без заметок → DealError(code="no_contact")
```

**End-to-end проверка:**
1. Задать `AMOCRM_LONG_LIVED_TOKEN` в Railway (уже должен быть).
2. Вызвать `GET /rop/run-plan`.
3. Убедиться, что каждый продавец с `amocrm_user_id` получил сообщение в Telegram.
4. Убедиться, что менеджеры получили командную сводку.

---

### [ ] Stage 7 — AmoCRM task creation (опционально, только по явному запросу)

**Деферировано.** Реализовывать только если владелец явно подтвердит.

**Риски:** запись в CRM требует токен с дополнительными scope; риск задублировать задачи.

**Что нужно:**
- Добавить `_post()` метод в `app/amocrm/client.py`.
- `create_task(lead_id, text, complete_till_ts, responsible_user_id)` → `POST /api/v4/tasks`.
- Idempotency guard: проверить, нет ли уже задачи с тем же текстом за сегодня перед созданием.
- Обновить `AMOCRM_LONG_LIVED_TOKEN` на токен с правами на запись.

---

## Открытые вопросы

1. **LLM backend:** Hermes использует DeepSeek (`deepseek-chat`). Владелец упоминал «Claude-based агент» — возможно, имел в виду будущий переход. Переключение требует смены SDK (`openai` → `anthropic`). Уточнить перед Stage 1.

2. **Пороги «высокой ценности»:** сейчас `price >= 500_000` (UZS/условные единицы) в `analyse_deal_heat`. Уточнить у владельца — эта цифра в сумах или в другой валюте?

3. **Порог «залежавшейся»** сделки: `stuck_initial` — что считать «начальным статусом» воронки? Нужен `AMOCRM_INITIAL_STATUS_ID` (уже в `.env.example`, но значение `0`).

4. **Конфликт jobs:** `hermes_morning_digest_job` уже шлёт дайджест в 09:00. `rop_daily_plan_job` будет слать план в 09:05. Не слишком ли много сообщений утром? Возможно, объединить в одно.

---

## Прогресс

| Этап | Статус | Коммит |
|---|---|---|
| Stage 0 — Foundations | ✅ DONE | `32a8fe3` |
| Stage 1 — Error analysis | ⏳ TODO | — |
| Stage 2 — Daily plan | ⏳ TODO | — |
| Stage 3 — Team roll-up | ⏳ TODO | — |
| Stage 4 — Scheduling | ⏳ TODO | — |
| Stage 5 — Bot commands | ⏳ TODO | — |
| Stage 6 — Tests | ⏳ TODO | — |
| Stage 7 — CRM write | 🔒 Deferred | — |
