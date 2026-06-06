from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_loaded: dict[str, Any] = {}


def load_skills() -> list[dict]:
    """Сканирует hermes/skills/*.py, возвращает список OpenAI tool schemas."""
    global _loaded
    skills_dir = Path(__file__).parent
    tools = []
    for path in sorted(skills_dir.glob("*.py")):
        if path.stem in ("__init__", "loader"):
            continue
        try:
            module = importlib.import_module(f"hermes.skills.{path.stem}")
            if hasattr(module, "SCHEMA"):
                _loaded[module.SCHEMA["name"]] = module
                tools.append({"type": "function", "function": module.SCHEMA})
                logger.debug("Loaded skill: %s", module.SCHEMA["name"])
        except Exception as exc:
            logger.warning("Failed to load skill %s: %s", path.stem, exc)
    return tools


def get_skill(name: str) -> Any | None:
    return _loaded.get(name)


async def call_skill(name: str, params: dict, context: dict) -> dict:
    if not _loaded:
        load_skills()
    module = _loaded.get(name)
    if module is None:
        return {"error": f"skill not found: {name}"}
    try:
        return await module.run(params, context)
    except Exception as exc:
        logger.exception("Skill %s failed: %s", name, exc)
        return {"error": str(exc)}
