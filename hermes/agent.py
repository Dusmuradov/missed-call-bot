from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def _md_to_html(text: str) -> str:
    """
    Конвертирует Markdown → Telegram HTML.
    Таблицы оборачивает в <pre> для монопробельного отображения.
    """
    # [text](url) → <a href="url">text</a>
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\)\s]+)\)', r'<a href="\2">\1</a>', text)

    # --- Markdown-таблицы → <pre> ---
    def convert_table(m: re.Match) -> str:
        lines = [l for l in m.group(0).splitlines() if l.strip()]
        # Убрать разделительную строку |---|---|
        rows = [l for l in lines if not re.match(r'^\s*\|[-| :]+\|\s*$', l)]
        table_lines = []
        for row in rows:
            cells = [c.strip() for c in row.strip().strip('|').split('|')]
            table_lines.append('  '.join(f'{c:<18}' for c in cells).rstrip())
        return '<pre>' + '\n'.join(table_lines) + '</pre>'

    # Блок таблицы — 2+ строк с pipe
    text = re.sub(
        r'((?:[ \t]*\|.+\n?){2,})',
        convert_table,
        text,
    )

    # **жирный** → <b>жирный</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    # *курсив* → <i>курсив</i>
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text)
    # `код` → <code>код</code>
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)
    # ### Заголовки → <b>
    text = re.sub(r'^#{1,3}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    # Лишние пустые строки
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

_SYSTEM_PROMPT_BASE = """Ты Hermes — AI руководитель отдела продаж (РОП) мебельной компании Neoclassica (Узбекистан).

Твоя задача — помогать команде продавать больше и эффективнее. У тебя есть инструменты (skills) для получения реальных данных: воронка AmoCRM, звонки Utel, склад и продажи BILLZ.

Как ты работаешь:
- Когда нужны цифры — используй инструменты, не придумывай
- Отвечай конкретно: цифры, скрипты, следующие шаги
- Если менеджер описывает ситуацию с клиентом — квалифицируй лид и дай скрипт
- Если спрашивают о возражении — дай готовый ответ
- Если спрашивают о команде или показателях — запроси данные и разбери их
- Никогда не говори «это зависит» без конкретного ответа следом
- Если инструмент вернул ОШИБКУ (ключ "error" в ответе) — отвечай ТОЛЬКО: «Данные временно недоступны, попробуй позже.» Точка. Больше ничего.
- Если инструмент вернул пустой список (count=0, leads=[]) — это значит данных нет за тот период, а не ошибка. Скажи коротко что именно не найдено, например: «За сегодня необработанных лидов нет.»
- Выбирая period для инструмента, учитывай текущее время: если сейчас ночь (0:00–06:00) и пользователь говорит «за сегодня» или ссылается на данные из предыдущего сообщения — используй period="yesterday", т.к. рабочий день ещё не начался.
- СТРОГО ЗАПРЕЩЕНО: просить пользователя что-либо делать с токенами, API, настройками, правами доступа. Это не его зона ответственности.
- СТРОГО ЗАПРЕЩЕНО: объяснять технические причины ошибок, предлагать создавать/удалять токены, давать инструкции по AmoCRM/API.
- СТРОГО ЗАПРЕЩЕНО: говорить "зайди в AmoCRM → ...", "открой фильтр", "перейди в раздел" — ты сам берёшь данные через инструменты, не направляй пользователя вручную.

Стиль: прямой, деловой, без воды. Как опытный РОП на планёрке.
Язык: русский.

Форматирование — Telegram HTML:
- Заголовки и акценты: <b>текст</b>
- Числа/коды: <code>123</code>
- Таблицы — через <pre>...</pre> с выравниванием пробелами, например:
<pre>Оператор       Мин    Звонков
Марат          143    98
Muzaffar       121    95</pre>
- Списки — обычные: "1. пункт" или "- пункт"
- Ссылки: <a href="https://example.amocrm.ru/leads/detail/123">Сделка #123</a>
- Не используй **звёздочки**, |пайпы|, --- разделители."""


async def ask(tg_user_id: int, user_text: str, amocrm_user_id: int | None = None, role: str = "employee") -> str:
    """
    Основная точка входа: обрабатывает сообщение менеджера через tool-loop.
    Возвращает текстовый ответ агента.
    """
    import zoneinfo
    from datetime import datetime as _dt
    from hermes.context import get_history, save_message, trim_history
    from hermes.llm import get_llm_client
    from hermes.skills.loader import call_skill, load_skills

    llm = get_llm_client()
    tools = load_skills()
    history = await get_history(tg_user_id, limit=20)

    context = {"llm": llm, "amocrm_user_id": amocrm_user_id, "tg_user_id": tg_user_id, "role": role}

    now_tashkent = _dt.now(zoneinfo.ZoneInfo("Asia/Tashkent"))
    time_ctx = f"\nТекущее время (Ташкент): {now_tashkent.strftime('%d.%m.%Y %H:%M')} (день недели: {now_tashkent.strftime('%A')})."

    role_restriction = ""
    if role == "seller":
        role_restriction = (
            "\n\nОГРАНИЧЕНИЯ ДЛЯ РОЛИ «ПРОДАВЕЦ»: этот пользователь — рядовой сотрудник. "
            "ЗАПРЕЩЕНО показывать: маржинальность, прибыль, себестоимость, наценку, закупочные цены, "
            "рентабельность, чистую/валовую прибыль, любые финансовые показатели кроме суммы сделки (price). "
            "Если спрашивают о прибыли/марже — ответь: «Эта информация недоступна в твоём уровне доступа.»"
        )

    system_content = _SYSTEM_PROMPT_BASE + time_ctx + role_restriction

    messages: list[dict] = [
        {"role": "system", "content": system_content},
        *history,
        {"role": "user", "content": user_text},
    ]

    answer = ""
    for iteration in range(5):
        response = await llm.chat(messages, tools=tools if tools else None, temperature=0.3)

        if response["tool_calls"]:
            # Добавляем ответ ассистента с tool_calls в историю
            messages.append({
                "role": "assistant",
                "content": response["content"],
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": tc["function"],
                    }
                    for tc in response["tool_calls"]
                ],
            })

            # Выполняем каждый tool call
            for tc in response["tool_calls"]:
                skill_name = tc["function"]["name"]
                try:
                    params = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    params = {}

                logger.debug("Agent: calling skill %s (iter=%d)", skill_name, iteration)
                result = await call_skill(skill_name, params, context)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })

        else:
            # Финальный текстовый ответ — конвертируем Markdown → Telegram HTML
            answer = _md_to_html((response["content"] or "").strip())
            # Если ответ начинается с "недоступн" — обрезаем до первой строки
            if re.search(r'недоступн', answer[:80], re.IGNORECASE):
                answer = "Данные временно недоступны, попробуй позже."
            break
    else:
        answer = "Не удалось получить ответ — превышено количество шагов."

    if answer:
        await save_message(tg_user_id, "user", user_text)
        await save_message(tg_user_id, "assistant", answer)
        await trim_history(tg_user_id, keep=50)

    return answer or "Нет ответа от агента."
