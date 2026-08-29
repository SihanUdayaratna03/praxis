"""The prompt library, and the digest table that makes a version bump mandatory.

The interesting test in this file is `test_shipped_prompts_have_not_been_edited`.
Nothing at runtime can tell an edited prompt from one that was always that way,
so the enforcement is here: a prompt's SHA-256 is written down, and changing the
file without adding a new version fails the suite with a message saying so.
"""

from __future__ import annotations

import re

import pytest
from praxis.ingest import segmenter
from praxis.prompts.library import (
    Prompt,
    PromptError,
    PromptNotFoundError,
    PromptRenderError,
    every_prompt,
    load,
    versions_of,
)

# Every prompt this build ships, by id, with the digest of its bytes.
#
# Adding a row is how a new prompt lands. CHANGING a row is almost always
# wrong: a prompt that has been edited is a prompt that produced traces nobody
# can reproduce, so the fix is a new file at the next version and a new row.
# The one legitimate edit is a typo fixed before the prompt has ever run.
SHIPPED: dict[str, str] = {
    "answer_why_not@v1": ("84545460c97653038f0381a1122421ac52a7623a2142a1f29c8a33b2603d2dbe"),
    "classify_work@v1": ("ea83286351445249e18b31a3dee8b22d911c04d23938742e9bf5c4d3773d89b4"),
    "extract_assumptions@v1": ("901030479201b71dd8afb9afe4a8bcc6aba108072befd4f876f3daf850983486"),
    "extract_estimates@v1": ("866f76e98def53bcf532c4af754763b1014ab03197f969f78a4317baa31d0865"),
    "formalize_assumption@v1": ("943d6f1f4898b3f8caaa90aee15ff2707e4fde4939ace152eeb31330babf678a"),
    "group_blocks@v1": "00f9f9da6d7485ef4157fbeb1d95620b92ff083cf96798eeb93d0999a9cef72c",
    "judge_contradiction@v1": ("8ead31233bc8dbd9159e9d6fa0523112ee28869447d9e940d85712e762ceec14"),
    "match_outcome@v1": ("875475647c386c54bc0377a6a9b83d0d4db36626fb001587a82e89e6c68e031d"),
    "match_event@v1": "c8781c6ceaa3c2b67225da3620e015c1abf610533cba80b34de69df6ae7d74a2",
    "scan_for_decisions@v1": ("66b4da5129cb270889d9f531ee52e5b0dfa16bc100462e86e4b5d4912b5b7103"),
    "structure_decision@v1": ("c74ca84ae7ae94fb4bda71eb97bfb44c77fe2b5784178ef664caf89c43b8fc6a"),
}


def test_shipped_prompts_have_not_been_edited():
    """A prompt file's bytes are pinned, so an edit needs a version bump."""
    actual = {prompt.id: prompt.sha256 for prompt in every_prompt()}
    assert actual == SHIPPED, (
        "a shipped prompt changed. If this is a real change, add the new text as "
        "the next version rather than editing the file, and add a row here."
    )


def test_every_prompt_is_named_after_a_task():
    """Prompt names are snake case, so they can be an `LLMRequest.task` verbatim."""
    for prompt in every_prompt():
        assert re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", prompt.name)
        assert prompt.version >= 1


def test_every_prompt_says_something():
    for prompt in every_prompt():
        assert prompt.template.strip()
        assert prompt.template.endswith("\n")


def test_load_returns_the_newest_version_by_default():
    for name in {prompt.name for prompt in every_prompt()}:
        assert load(name).version == max(versions_of(name))


def test_load_can_ask_for_one_version():
    """A replayed run asks for the version the trace recorded, not the newest."""
    assert load("group_blocks", version=1).id == "group_blocks@v1"


def test_load_refuses_an_unknown_name():
    with pytest.raises(PromptNotFoundError, match="no prompt named"):
        load("no_such_prompt")


def test_load_refuses_an_unknown_version():
    with pytest.raises(PromptNotFoundError, match="has no version 99"):
        load("group_blocks", version=99)


def test_versions_of_an_unknown_prompt_is_empty():
    assert versions_of("no_such_prompt") == ()


def test_the_id_carries_the_version():
    prompt = Prompt(name="a_task", version=7, template="hello\n")
    assert prompt.id == "a_task@v7"


def test_the_digest_is_of_the_template_bytes():
    """Two prompts with the same text agree, and a changed character does not."""
    one = Prompt(name="a_task", version=1, template="hello\n")
    same = Prompt(name="b_task", version=9, template="hello\n")
    other = Prompt(name="a_task", version=1, template="hellp\n")
    assert one.sha256 == same.sha256
    assert one.sha256 != other.sha256


def test_render_substitutes_placeholders():
    prompt = Prompt(name="a_task", version=1, template="at most $limit blocks\n")
    assert prompt.render(limit=12) == "at most 12 blocks\n"


def test_render_leaves_braces_alone():
    """Prompts carry JSON examples and predicates. Braces are not placeholders."""
    prompt = Prompt(name="a_task", version=1, template='answer {"a": 1} for $x\n')
    assert prompt.render(x="now") == 'answer {"a": 1} for now\n'


def test_render_refuses_to_leave_a_placeholder_behind():
    """An unsubstituted placeholder reaching a model is a different question."""
    prompt = Prompt(name="a_task", version=1, template="at most $limit blocks\n")
    with pytest.raises(PromptRenderError, match="a_task@v1"):
        prompt.render(other=1)


def test_render_reports_a_malformed_template():
    prompt = Prompt(name="a_task", version=1, template="a $ b\n")
    with pytest.raises(PromptRenderError):
        prompt.render()


def test_render_of_a_plain_prompt_is_the_text():
    """A prompt with no placeholders still goes through render."""
    prompt = Prompt(name="a_task", version=1, template="say something\n")
    assert prompt.render() == "say something\n"


def test_prompt_errors_share_a_base():
    """One `except PromptError` catches everything this module raises."""
    assert issubclass(PromptNotFoundError, PromptError)
    assert issubclass(PromptRenderError, PromptError)


def test_the_segmenter_reads_its_prompt_from_the_library():
    """Phase 3's system prompt is no longer a constant in the agent module."""
    assert not hasattr(segmenter, "_SYSTEM_PROMPT")
    assert load(segmenter.SEGMENT_TASK).template
