"""What the CLI's output looks like, kept apart from what its commands do.

`praxis.cli` is about wiring: which commands exist, what they open, what they
exit with. This is about rendering. Splitting them keeps either one readable as
one thing, and it is the only reason the split exists -- there is no boundary
being defended here, unlike the ones in `praxis.llm` and `praxis.store`.
"""

from __future__ import annotations

from rich.table import Table

from praxis.config.models import ModelRole, resolve, role_for_agent, routed_agents
from praxis.config.settings import Settings
from praxis.domain.enums import GRAPH_KINDS, RecordKind
from praxis.domain.links import LinkType
from praxis.store.reports import StoreStats


def configuration_table(settings: Settings) -> Table:
    """The resolved settings, with the key reported as present or absent.

    Never printed, only counted: a CLI that renders a secret puts it in a
    terminal scrollback and a screen recording, and this command is the one
    people run while someone is watching.
    """
    table = Table(title="Configuration", show_header=True, header_style="bold")
    table.add_column("Setting")
    table.add_column("Value")

    key_state = "set" if settings.anthropic_api_key is not None else "not set"
    for name, value in (
        ("llm_provider", settings.llm_provider.value),
        ("anthropic_api_key", key_state),
        ("offline", str(settings.is_offline)),
        ("data_dir", str(settings.data_dir)),
        ("db_path", str(settings.db_path)),
        ("trace_dir", str(settings.trace_dir)),
        ("seed", str(settings.seed)),
        ("cost_ceiling_usd", f"{settings.cost_ceiling_usd:.2f}"),
        ("log_level", settings.log_level),
        ("log_format", settings.log_format.value),
    ):
        table.add_row(name, value)
    return table


def routing_table() -> Table:
    """Every role, the model serving it, its price and the agents on it."""
    table = Table(title="Model routing", show_header=True, header_style="bold")
    table.add_column("Role")
    table.add_column("Model")
    table.add_column("USD / Mtok in")
    table.add_column("USD / Mtok out")
    table.add_column("Agents")

    for role in ModelRole:
        spec = resolve(role)
        agents = sorted(a for a in routed_agents() if role_for_agent(a) is role)
        table.add_row(
            role.value,
            spec.model_id,
            f"{spec.input_usd_per_mtok}",
            f"{spec.output_usd_per_mtok}",
            "\n".join(agents) or "-",
        )
    return table


def records_table(stats: StoreStats) -> Table:
    """Current, unretracted records per kind, zeros included."""
    table = Table(title="Records", show_header=True, header_style="bold")
    table.add_column("Kind")
    table.add_column("Current", justify="right")

    for kind in RecordKind:
        if kind in GRAPH_KINDS:
            table.add_row(kind.value, str(stats.records.get(kind, 0)))
    return table


def links_table(stats: StoreStats) -> Table:
    """Current, unretracted edges per type, zeros included."""
    table = Table(title="Edges", show_header=True, header_style="bold")
    table.add_column("Type")
    table.add_column("Current", justify="right")

    for link_type in LinkType:
        table.add_row(link_type.value, str(stats.links.get(link_type, 0)))
    return table
