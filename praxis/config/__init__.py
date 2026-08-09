"""Configuration: typed settings and the single source of truth for model IDs."""

from praxis.config.models import (
    MOCK_MODEL_ID,
    NON_LLM_AGENTS,
    ModelRole,
    ModelSpec,
    estimate_cost_usd,
    resolve,
    role_for_agent,
)
from praxis.config.settings import Settings, get_settings

__all__ = [
    "MOCK_MODEL_ID",
    "NON_LLM_AGENTS",
    "ModelRole",
    "ModelSpec",
    "Settings",
    "estimate_cost_usd",
    "get_settings",
    "resolve",
    "role_for_agent",
]
