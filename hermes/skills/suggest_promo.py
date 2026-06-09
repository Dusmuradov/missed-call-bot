"""
Skill: suggest_promo
Генерирует акционные механики БЕЗ скидок: бандлы, подарки, бонусы.
"""
import json

SCHEMA = {
    "name": "suggest_promo",
    "description": (
        "Генерирует акционные механики без скидок на товары: бандлы (комплекты), "
        "подарки при достижении суммы покупки, бесплатная доставка/установка, бонусные позиции. "
        "Используй когда спрашивают про акции, промо, как продвинуть товар, как поднять средний чек, "
        "что предложить клиенту дополнительно, как продать залежавшийся товар без скидки."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": (
                    "Цель акции: 'increase_aov' (поднять средний чек), "
                    "'clear_stock' (продать залежавшийся товар), "
                    "'attract_new' (привлечь новых клиентов), "
                    "'retain' (удержать существующих). "
                    "По умолчанию — increase_aov."
                ),
                "enum": ["increase_aov", "clear_stock", "attract_new", "retain"],
            },
            "products": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Список конкретных товаров/категорий для включения в акцию.",
            },
            "avg_order_value": {
                "type": "number",
                "description": "Текущий средний чек в сумах (если известен). Помогает поставить порог для подарков.",
            },
            "extra_context": {
                "type": "string",
                "description": "Дополнительный контекст: особенности бизнеса, сезон, что уже пробовали.",
            },
        },
        "required": [],
    },
}


async def run(params: dict, context: dict) -> dict:
    llm = context.get("llm")
    if not llm:
        return {"error": "LLM недоступен"}

    goal = params.get("goal") or "increase_aov"
    products = params.get("products") or []
    aov = params.get("avg_order_value")
    extra = params.get("extra_context") or ""

    goal_labels = {
        "increase_aov": "поднять средний чек",
        "clear_stock": "распродать остатки без скидок",
        "attract_new": "привлечь новых клиентов",
        "retain": "удержать и вернуть существующих клиентов",
    }

    products_text = ", ".join(products) if products else "не указаны конкретные товары"
    aov_text = f"Текущий средний чек: {aov:,.0f} сум." if aov else ""

    prompt = f"""Ты маркетолог мебельной компании Neoclassica (Узбекистан). Придумай акционные механики БЕЗ скидок.

Цель: {goal_labels.get(goal, goal)}
Товары/категории: {products_text}
{aov_text}
{extra}

Правила:
- Никаких скидок и снижения цен
- Механики: бандлы (комплекты со скидкой на доп. товар — НЕТ, просто комплект как предложение), подарки при достижении суммы, бесплатная доставка/сборка/установка, бонусные аксессуары, программа лояльности, кешбэк бонусами (не деньгами)
- Каждая механика должна быть конкретной: с порогами, товарами, условиями
- Учитывай специфику мебели: высокий чек, долгое принятие решения, важность сервиса

Верни JSON:
{{
  "promos": [
    {{
      "type": "bundle|gift_threshold|free_service|loyalty|accessory_bonus",
      "title": "Короткое название акции",
      "mechanic": "Детальное описание как работает (1-2 предложения)",
      "trigger": "При каком условии клиент получает бонус",
      "reward": "Что именно получает клиент",
      "expected_effect": "На что влияет: AOV, конверсия, лояльность",
      "script": "Фраза менеджера клиенту (1 предложение)"
    }}
  ],
  "priority": "Название самой приоритетной механики для быстрого запуска",
  "insight": "1-2 предложения почему эти механики сработают именно сейчас"
}}"""

    try:
        resp = await llm.chat(
            [
                {"role": "system", "content": "Ты маркетолог. Отвечай ТОЛЬКО JSON без markdown."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=1200,
        )
        return json.loads(resp["content"])
    except json.JSONDecodeError:
        content = resp.get("content") if isinstance(resp, dict) else str(resp)
        return {"raw": content}
    except Exception as exc:
        return {"error": f"Ошибка генерации: {exc}"}
