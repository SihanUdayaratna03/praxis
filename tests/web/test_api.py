"""The read-only API, over a real store rather than a mocked one.

Two things are being defended beyond "the routes answer". Nothing here writes,
because ADR 0003's single-writer assumption rests on it. And a refusal survives
the HTTP boundary as a refusal: `BiasDetective` returning "two outcomes short"
must not arrive at the browser as a factor.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from praxis.config.models import MOCK_MODEL_ID, ModelRole
from praxis.llm.trace import LLMTrace
from praxis.llm.types import CallOutcome, StopReason, TokenUsage
from praxis.store.connection import transaction
from praxis.store.reports import stats
from praxis.store.traces import write_trace

from tests.store.conftest import World


def a_trace(**overrides) -> LLMTrace:
    """One recorded model call, in the shape the panel reads."""
    fields = {
        "run_id": "RUN-000000000001",
        "agent": "DecisionScout",
        "task": "scan_for_decisions",
        "role": ModelRole.SCAN,
        "provider": "mock",
        "model_id": MOCK_MODEL_ID,
        "prompt_hash": "a" * 32,
        "outcome": CallOutcome.OK,
        "usage": TokenUsage(input_tokens=120, output_tokens=40),
        "cost_usd": Decimal("0"),
        "latency_ms": 12,
        "request_json": '{"system":"You extract decisions.","user":"ADR 0011."}',
        "stop_reason": StopReason.END_TURN,
        "response_text": '{"claims":[{"statement":"block grouping beats the floor"}]}',
        "occurred_at": datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
    }
    return LLMTrace(**(fields | overrides))


class TestOverview:
    def test_an_empty_store_answers_with_zeros_rather_than_an_error(self, client):
        body = client.get("/api/overview").json()
        assert body["decisions"] == 0
        assert body["assumptions"] == 0
        assert body["calibration_bias"] is None
        assert body["assumptions_valid"] == 0.0

    def test_the_counts_match_the_store(self, populated, repository, graph: World):
        body = populated.get("/api/overview").json()
        counts = stats(repository.connection)
        assert body["decisions"] == counts.records["decision"]
        assert body["assumptions"] == 1
        assert body["estimates"] == 1
        assert body["findings"] == 1

    def test_the_provider_is_reported_for_the_status_chip(self, client):
        assert client.get("/api/overview").json()["provider"] == "mock"

    def test_the_schema_version_is_reported(self, populated, repository):
        body = populated.get("/api/overview").json()
        assert body["schema_version"] == stats(repository.connection).schema_version


class TestDecisions:
    def test_the_index_lists_every_decision_with_its_counts(self, populated, graph: World):
        rows = populated.get("/api/decisions").json()
        by_id = {row["decision"]["id"]: row for row in rows}
        assert by_id[graph.decision.id]["assumptions"] == 1
        assert by_id[graph.decision.id]["at_risk"] is False

    def test_the_drill_down_returns_the_whole_argument_chain(self, populated, graph: World):
        body = populated.get(f"/api/decisions/{graph.decision.id}").json()
        assert body["decision"]["id"] == graph.decision.id
        line = body["lines"][0]
        assert line["assumption"]["id"] == graph.assumption.id
        assert line["estimate"]["id"] == graph.estimate.id
        assert line["outcome"]["id"] == graph.outcome.id
        assert line["findings"][0]["id"] == graph.finding.id

    def test_the_drill_down_carries_the_audit_trail(self, populated, graph: World):
        body = populated.get(f"/api/decisions/{graph.decision.id}").json()
        assert [event["entity_id"] for event in body["audit"]] == [graph.decision.id]

    def test_an_unknown_decision_is_a_404(self, populated):
        assert populated.get("/api/decisions/D-9999").status_code == 404


class TestEstimates:
    def test_the_drill_down_reports_what_leaned_on_the_estimate(self, populated, graph: World):
        body = populated.get(f"/api/estimates/{graph.estimate.id}").json()
        assert body["outcome"]["id"] == graph.outcome.id
        reached = {node["id"]: node["depth"] for node in body["impacted"]}
        assert reached[graph.decision.id] == 2
        assert reached[graph.other_decision.id] == 1

    def test_an_unknown_estimate_is_a_404(self, populated):
        assert populated.get("/api/estimates/EST-9999").status_code == 404

    def test_a_quantity_crosses_the_boundary_as_an_exact_string(self, populated, graph: World):
        """Invariant 4 does not stop at HTTP. A float here would round."""
        body = populated.get("/api/estimates").json()
        assert body[0]["active_quantity"] == "6.5"
        assert isinstance(body[0]["active_quantity"], str)


class TestCalibration:
    def test_a_refusal_arrives_as_a_refusal_and_not_as_a_factor(self, populated):
        body = populated.get("/api/calibration").json()
        assert body["minimum_sample"] == 5
        assert body["factors"], "the group exists even though it cannot speak"
        for factor in body["factors"]:
            # One outcome is nowhere near n=5, so nothing may claim to speak.
            assert factor["speaks"] is False
            assert factor["factor"] is None
            assert factor["reason"]

    def test_the_history_is_returned_beside_the_factors(self, populated, graph: World):
        body = populated.get("/api/calibration").json()
        assert [row["estimate_id"] for row in body["history"]] == [graph.estimate.id]
        assert body["history"][0]["resolved"] is True


class TestFusion:
    def test_every_priced_edge_is_returned_refusals_included(self, populated, graph: World):
        body = populated.get("/api/fusion").json()
        assert len(body["priced"]) == 1
        priced = body["priced"][0]
        assert priced["assumption_id"] == graph.assumption.id
        assert priced["describe"], "the CLI's own sentence crosses the boundary"

    def test_a_refusal_below_the_sample_floor_does_not_flip(self, populated):
        body = populated.get("/api/fusion").json()
        assert body["priced"][0]["verdict"] == "no_factor"
        assert body["flips"] == []

    def test_an_empty_store_has_nothing_to_price(self, client):
        body = client.get("/api/fusion").json()
        assert body["priced"] == []
        assert body["flips"] == []


class TestQueueAndTimeline:
    def test_the_queue_returns_the_finding_with_its_subject_label(self, populated, graph: World):
        rows = populated.get("/api/queue").json()
        assert rows[0]["finding"]["id"] == graph.finding.id
        assert rows[0]["subject_label"] == graph.assumption.predicate

    def test_the_queue_can_narrow_to_the_unchallenged(self, populated):
        assert len(populated.get("/api/queue?undecided_only=true").json()) == 1

    def test_the_timeline_is_newest_first_and_reports_its_total(self, populated):
        body = populated.get("/api/timeline").json()
        assert body["total"] == len(body["events"])
        assert body["events"][0]["action"] in {"created", "revised", "retracted"}

    def test_the_timeline_refuses_a_page_size_over_the_ceiling(self, populated):
        assert populated.get("/api/timeline?limit=100000").status_code == 422


class TestTraces:
    def test_the_panel_is_empty_rather_than_broken_when_nothing_has_run(self, populated):
        body = populated.get("/api/traces").json()
        assert body["traces"] == []
        assert body["runs"] == []
        assert body["total"] == 0

    def test_a_recorded_call_arrives_with_its_request_and_its_answer(self, repository, populated):
        """The reasoning panel needs both sides, not a summary of them."""
        with transaction(repository.connection):
            write_trace(repository.connection, a_trace())
        body = populated.get("/api/traces").json()
        assert body["total"] == 1
        assert body["runs"] == ["RUN-000000000001"]
        row = body["traces"][0]
        assert row["agent"] == "DecisionScout"
        assert "You extract decisions" in row["request_json"]
        assert "block grouping" in row["response_text"]
        assert row["seq"] == 1
        assert row["cost_usd"] == "0"

    def test_the_panel_narrows_to_one_agent(self, repository, populated):
        with transaction(repository.connection):
            write_trace(repository.connection, a_trace())
            write_trace(
                repository.connection, a_trace(agent="ChallengerAgent", prompt_hash="b" * 32)
            )
        narrowed = populated.get("/api/traces?agent=ChallengerAgent").json()
        assert [t["agent"] for t in narrowed["traces"]] == ["ChallengerAgent"]


class TestTheApiNeverWrites:
    def test_no_route_changes_the_store(self, populated, repository):
        """ADR 0003 assumption 3: the dashboard gains no write endpoints."""
        before = stats(repository.connection)
        for path in (
            "/api/overview",
            "/api/decisions",
            "/api/assumptions",
            "/api/estimates",
            "/api/calibration",
            "/api/fusion",
            "/api/queue",
            "/api/timeline",
            "/api/traces",
        ):
            assert populated.get(path).status_code == 200
        after = stats(repository.connection)
        assert before == after

    def test_the_schema_declares_no_write_method(self, populated):
        """The claim as a property of the app, not of the routes I remembered."""
        paths = populated.get("/api/openapi.json").json()["paths"]
        methods = {method for spec in paths.values() for method in spec}
        assert methods == {"get"}


class TestPages:
    def test_the_landing_page_is_served(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Praxis" in response.text

    def test_the_dashboard_shell_is_served(self, client):
        assert client.get("/dashboard").status_code == 200
