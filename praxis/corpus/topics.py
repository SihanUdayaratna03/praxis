"""The material a generated corpus is made of: eight engineering decisions.

Separated from `generator` because the two change for different reasons. The
generator is machinery -- offsets, templates, the answer key -- and changing it
is a code change. This is content, and adding a topic should not mean reading
any of that.

Every topic carries a decision with real rejected options, an assumption that
is *quantified and forward-looking* (which is what makes it an estimate wearing
an assumption's clothes, and therefore what `FusionBridge` will be graded on),
an estimate, the outcome that actually happened, and a hypothetical that reads
like a decision and is not.

The estimates miss in both directions and by different amounts. A corpus whose
estimates were all optimistic would let a calibration agent score well by
always answering "over", which is the calibration equivalent of an extractor
that returns every sentence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Topic:
    """One engineering decision, with everything a document about it needs.

    Attributes:
        slug: Used in file names, so it is kebab-case and unique.
        subject: What the decision is about, as a noun phrase.
        driver: Why it came up. Gives a template something to open with.
        chosen: The option taken.
        rejected: The alternatives, each with the reason it lost.
        assumption: The belief the decision rests on, in plain words.
        predicate: The same claim as a checkable expression.
        expiry: When it should be re-examined.
        estimate_weeks: What was predicted.
        actual_weeks: What happened. Differs from the estimate in both
            directions across the set, on purpose.
        owner: Whose estimate it was. Calibration is per estimator.
        team: Who the decision binds.
        work_class: The grouping key calibration is computed within.
        hypothetical: A sentence that looks like a decision and is not.
    """

    slug: str
    subject: str
    driver: str
    chosen: str
    rejected: tuple[tuple[str, str], ...]
    assumption: str
    predicate: str
    expiry: str
    estimate_weeks: int
    actual_weeks: int
    owner: str
    team: str
    work_class: str
    hypothetical: str


TOPICS: Final[tuple[Topic, ...]] = (
    Topic(
        slug="search-index",
        subject="the product search index",
        driver="search latency crossed two seconds at the 95th percentile",
        chosen="OpenSearch on managed nodes",
        rejected=(
            ("Postgres full-text search", "ranking quality was not close on our own queries"),
            ("a hosted search vendor", "the per-query price does not survive our traffic growth"),
        ),
        assumption="the index stays under 50 GB for the next year",
        predicate="index_size_gb <= 50",
        expiry="when(indexed_documents >= 10000000)",
        estimate_weeks=4,
        actual_weeks=7,
        owner="Nadeesha",
        team="Discovery",
        work_class="infrastructure",
        hypothetical="If the vendor drops their price we would look at hosted search again.",
    ),
    Topic(
        slug="billing-events",
        subject="billing event delivery",
        driver="two customers were double-charged after a retry storm",
        chosen="an outbox table drained by a single worker",
        rejected=(
            ("publishing directly from the request handler", "a retry duplicates the charge"),
            (
                "a queue with exactly-once delivery claims",
                "the guarantee does not survive our consumer restarts",
            ),
        ),
        assumption="peak billing throughput stays under 200 events a second",
        predicate="peak_events_per_second <= 200",
        expiry="when(monthly_active_accounts >= 100000)",
        estimate_weeks=3,
        actual_weeks=3,
        owner="Priyanka",
        team="Payments",
        work_class="backend",
        hypothetical="Somebody suggested we could move billing onto the events platform entirely.",
    ),
    Topic(
        slug="mobile-offline",
        subject="offline support in the mobile app",
        driver="field engineers lose connectivity for hours at a time",
        chosen="a local SQLite mirror with last-writer-wins sync",
        rejected=(
            ("a CRDT-based sync layer", "nobody on the team has run one in production"),
            (
                "read-only caching",
                "engineers need to file reports while offline, not just read them",
            ),
        ),
        assumption="conflicting edits to one report stay below one percent of syncs",
        predicate="conflicting_sync_rate <= 0.01",
        expiry="when(field_engineers >= 500)",
        estimate_weeks=6,
        actual_weeks=11,
        owner="Tharindu",
        team="Mobile",
        work_class="mobile",
        hypothetical="We debated whether a CRDT would be worth it once the team is bigger.",
    ),
    Topic(
        slug="auth-migration",
        subject="the move off the legacy session store",
        driver="the session store is the only service still on the old cluster",
        chosen="signed tokens with a short refresh window",
        rejected=(
            ("lifting the session store onto the new cluster", "it postpones the work by a year"),
            ("a third-party identity provider", "the migration cost is larger than the problem"),
        ),
        assumption="the migration finishes within six weeks",
        predicate="migration_weeks <= 6",
        expiry="when(phases_completed >= 4)",
        estimate_weeks=6,
        actual_weeks=9,
        owner="Nadeesha",
        team="Platform",
        work_class="migration",
        hypothetical="One option raised was to keep both session paths alive indefinitely.",
    ),
    Topic(
        slug="report-exports",
        subject="scheduled report exports",
        driver="the nightly export has been timing out twice a week",
        chosen="streaming exports written straight to object storage",
        rejected=(
            (
                "raising the timeout again",
                "it has been raised twice and bought two months each time",
            ),
            ("pre-computing every report", "most of them are never opened"),
        ),
        assumption="no single export exceeds two gigabytes",
        predicate="max_export_gb <= 2",
        expiry="when(largest_account_rows >= 50000000)",
        estimate_weeks=2,
        actual_weeks=1,
        owner="Ishara",
        team="Reporting",
        work_class="backend",
        hypothetical="If exports keep growing we might have to pre-compute them after all.",
    ),
    Topic(
        slug="feature-flags",
        subject="feature flag evaluation",
        driver="a flag misfire took checkout down for eleven minutes",
        chosen="flags evaluated in-process from a periodically refreshed snapshot",
        rejected=(
            (
                "evaluating flags in a network call per request",
                "it puts a new dependency in the checkout path",
            ),
            (
                "compile-time flags",
                "we would lose the ability to turn something off during an incident",
            ),
        ),
        assumption="a stale flag snapshot of up to thirty seconds is acceptable",
        predicate="flag_staleness_seconds <= 30",
        expiry='on_event("an incident is prolonged by a stale flag")',
        estimate_weeks=2,
        actual_weeks=4,
        owner="Priyanka",
        team="Platform",
        work_class="backend",
        hypothetical="Marketing asked whether flags could be changed by non-engineers one day.",
    ),
    Topic(
        slug="data-retention",
        subject="raw event retention",
        driver="storage cost for raw events grew faster than revenue for two quarters",
        chosen="ninety days of raw events, rolled up monthly after that",
        rejected=(
            ("keeping everything forever", "the cost curve is steeper than the revenue curve"),
            ("thirty days", "it breaks quarter-over-quarter analysis, which finance depends on"),
        ),
        assumption="no analysis needs raw events older than ninety days",
        predicate="analyses_needing_raw_events_over_90_days == 0",
        expiry="when(quarters_since_decision >= 2)",
        estimate_weeks=3,
        actual_weeks=2,
        owner="Ishara",
        team="Data",
        work_class="data-engineering",
        hypothetical="If a regulator asks for two years of raw events this gets revisited.",
    ),
    Topic(
        slug="ci-runners",
        subject="continuous integration runners",
        driver="the median pull request waits eighteen minutes for a free runner",
        chosen="self-hosted runners with a hosted overflow pool",
        rejected=(
            (
                "buying more hosted minutes",
                "the monthly bill passes an engineer's salary at our growth rate",
            ),
            ("only self-hosted runners", "a bad week of hardware failures would stop every merge"),
        ),
        assumption="self-hosted runners cover at least eighty percent of jobs",
        predicate="self_hosted_job_share >= 0.8",
        expiry="when(monthly_ci_minutes >= 200000)",
        estimate_weeks=5,
        actual_weeks=8,
        owner="Tharindu",
        team="Developer Experience",
        work_class="infrastructure",
        hypothetical="There was a suggestion to move CI to a different provider entirely.",
    ),
)
"""Eight topics, reused across templates so one subject appears in several
document kinds -- an ADR, the meeting it came out of, the status update that
closed it. That is what a real corpus looks like, and it is also what makes
`ContradictionDetector` and `ArchaeologistAgent` gradeable later."""
