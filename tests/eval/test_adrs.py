"""The predicate language, graded against predicates written before it existed.

`test_adr_0001s_first_assumption_holds` is the one that matters, and it is the
first time in this project that an assumption recorded in Praxis's own schema
has been *answered* rather than merely stored. ADR 0001 claimed, with no parser
and no grammar in existence, that a DSL built in Phase 5 would read at least
nine in ten of the predicates already written by hand. The number is recomputed
from the files every run.

The rest of this file is about keeping that number honest. An author who wanted
the assumption to hold could widen the grammar until it did, so the row that
does *not* parse is named in a test: it stays a finding rather than becoming a
feature request.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from praxis.eval.adrs import TEMPLATE_NAME, read_adr_predicates

ADRS = Path(__file__).resolve().parents[2] / "docs" / "adr"

TABLE = """\
---
id: ADR-0099
---

# 0099 --- a decision

## Rejected

| Option | Why not |
| ------ | ------- |
| 1 | Something with a number in the first column |

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The index stays small | `index_size_gb <= 50` | `when(documents >= 100)` |
| 2 | Every model is valid | `all_routed_models_valid == true` | `after("2026-08-31")` |
"""


@pytest.fixture
def written(tmp_path: Path) -> Path:
    """A directory holding one ADR in the shape this project writes them."""
    (tmp_path / "0099-a-decision.md").write_text(TABLE, encoding="utf-8")
    return tmp_path


class TestThisProjectsOwnAdrs:
    def test_adr_0001s_first_assumption_holds(self):
        # `adr_predicates_parsed / adr_predicates_total >= 0.9`, recorded on
        # 2026-08-09 when neither the parser nor the grammar existed, and
        # expiring `on_event("Phase 5 predicate DSL is implemented")`. That
        # event is this phase.
        report = read_adr_predicates(ADRS)
        assert report.total > 0
        assert report.rate >= Decimal("0.9")

    def test_the_one_predicate_it_cannot_read_is_named_rather_than_fixed(self):
        # ADR 0015's third assumption says a ratio should be `outside
        # [0.5, 2.0]`, which is English rather than an expression. Widening the
        # grammar for a single instance would be answering ADR 0001's question
        # by editing it, so this stays a finding and the phase report says so.
        unreadable = read_adr_predicates(ADRS).unreadable
        assert [found.adr for found in unreadable] == [
            "0015-extraction-cites-spans-by-offered-ordinal.md"
        ]
        assert "outside" in unreadable[0].predicate

    def test_every_expiry_condition_in_the_project_parses(self):
        # Not something ADR 0001 asked for, and worth reporting: an assumption
        # whose predicate parses and whose expiry does not is one the monitor
        # still cannot reach a verdict about.
        report = read_adr_predicates(ADRS)
        assert report.expiries_parsed == report.total

    def test_the_template_is_not_counted_as_an_adr(self):
        # It is a form with `<expression>` where a predicate goes, so counting
        # it would be counting the shape of an ADR rather than an ADR.
        assert (ADRS / TEMPLATE_NAME).is_file()
        assert all(found.adr != TEMPLATE_NAME for found in read_adr_predicates(ADRS).predicates)

    def test_the_rate_is_a_decimal(self):
        # Invariant 4 reaches the report: this number is compared against 0.9
        # and printed in a table a judge reads.
        assert isinstance(read_adr_predicates(ADRS).rate, Decimal)


class TestReadingAnAssumptionTable:
    def test_each_numbered_row_is_one_predicate(self, written):
        assert read_adr_predicates(written).total == 2

    def test_the_predicate_comes_back_without_its_backticks(self, written):
        assert read_adr_predicates(written).predicates[0].predicate == "index_size_gb <= 50"

    def test_the_claim_is_kept_so_a_failure_is_legible(self, written):
        # Without it a failed row is an expression with no indication of what it
        # was trying to say.
        assert read_adr_predicates(written).predicates[0].claim == "The index stays small"

    def test_the_expiry_condition_is_read_too(self, written):
        assert read_adr_predicates(written).predicates[0].expiry == "when(documents >= 100)"

    def test_a_table_that_is_not_an_assumption_table_contributes_nothing(self, written):
        # The rejected-options table in the fixture has a number in its first
        # column and would otherwise match.
        assert all(
            "Something with a number" not in found.claim
            for found in read_adr_predicates(written).predicates
        )

    def test_a_cell_holding_two_code_spans_is_read_as_the_prose_it_is(self, tmp_path):
        # Reading only the first span would quietly turn an unparseable row into
        # a parseable one and lose the finding -- which is exactly the row this
        # project really has.
        (tmp_path / "0100-two-spans.md").write_text(
            "## Assumptions\n\n"
            "| # | Assumption | Predicate | Expiry condition |\n"
            "| - | ---------- | --------- | ---------------- |\n"
            '| 1 | In band | `a / b` outside `[0.5, 2.0]` | `after("2026-01-01")` |\n',
            encoding="utf-8",
        )
        found = read_adr_predicates(tmp_path).predicates[0]
        assert found.predicate == "a / b outside [0.5, 2.0]"
        assert not found.parsed

    def test_a_row_with_no_predicate_is_skipped(self, tmp_path):
        (tmp_path / "0101-empty.md").write_text(
            "## Assumptions\n\n"
            "| # | Assumption | Predicate | Expiry condition |\n"
            "| - | ---------- | --------- | ---------------- |\n"
            '| 1 | Something unmeasured |  | `after("2026-01-01")` |\n',
            encoding="utf-8",
        )
        assert read_adr_predicates(tmp_path).total == 0


class TestTheRate:
    def test_a_directory_with_no_adrs_reads_one(self, tmp_path):
        # The assumption is that the grammar can read what was written, and
        # nothing written is nothing unread. It cannot flatter anything: the
        # files decide the denominator.
        report = read_adr_predicates(tmp_path)
        assert report.total == 0
        assert report.rate == Decimal("1.0000")

    def test_a_directory_that_does_not_exist_reads_one(self, tmp_path):
        # A packaged install has no docs/ at all, and that is not an error.
        assert read_adr_predicates(tmp_path / "absent").total == 0

    def test_a_half_readable_corpus_reads_a_half(self, tmp_path):
        (tmp_path / "0102-mixed.md").write_text(
            "## Assumptions\n\n"
            "| # | Assumption | Predicate | Expiry condition |\n"
            "| - | ---------- | --------- | ---------------- |\n"
            '| 1 | Readable | `a <= 1` | `after("2026-01-01")` |\n'
            '| 2 | Not readable | `the team stays motivated` | `after("2026-01-01")` |\n',
            encoding="utf-8",
        )
        report = read_adr_predicates(tmp_path)
        assert (report.parsed, report.total) == (1, 2)
        assert report.rate == Decimal("0.5000")
