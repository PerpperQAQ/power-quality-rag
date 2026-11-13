"""Shared, offline Tongyi catalogue captured from RAGFlow's model settings.

The catalogue describes capabilities, never account permissions or connectivity.
The same JSON is bundled into the frontend; startup only appends missing entries.
"""
import json
import re
from pathlib import Path

CATALOG = json.loads(Path(__file__).with_name("tongyi_models.json").read_text())
MODEL_INFO = {item["model"]: item for item in CATALOG["models"]}
DEFAULT_VISION = "qwen3.6-flash"


def catalog_rows():
    for item in CATALOG["models"]:
        for capability in item["capabilities"]:
            yield {
                "id": f"tongyi-{capability}-{re.sub(r'[^A-Za-z0-9_-]', '-', item['model'])}",
                "model": item["model"], "capability": capability,
                "enabled": not bool(item.get("disabledReason")),
            }


def selectable(model):
    return not MODEL_INFO.get(model, {}).get("disabledReason")


def embedding_input_budget(model):
    """Conservative UTF-8 budget, shared by chunking and index augmentation."""
    return 8000 if model in {"text-embedding-v3", "text-embedding-v4"} else 2000
