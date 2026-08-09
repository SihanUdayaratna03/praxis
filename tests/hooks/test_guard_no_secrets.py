"""The credential guard: it must catch real secrets and not cry wolf.

Both halves matter. A scanner that misses a key is useless; a scanner that
flags every placeholder in the documentation gets disabled within a week, at
which point it also misses the key.
"""

from __future__ import annotations

import json
from types import ModuleType

import pytest

# Assembled at runtime so this file does not itself contain a string that
# looks like a live credential to any other scanner.
ANTHROPIC_SHAPED = "sk-" + "ant-" + "A" * 40
AWS_SHAPED = "AKIA" + "B" * 16
GITHUB_SHAPED = "ghp_" + "c" * 36
PRIVATE_KEY_HEADER = "-----BEGIN " + "RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    "text",
    [
        f'ANTHROPIC_API_KEY="{ANTHROPIC_SHAPED}"',
        f"aws_key = {AWS_SHAPED}",
        f"auth_token: {GITHUB_SHAPED}",
        PRIVATE_KEY_HEADER,
    ],
)
def test_credential_shapes_are_caught(guard_no_secrets: ModuleType, text: str) -> None:
    assert guard_no_secrets.scan_text(text, "somefile.py")


@pytest.mark.parametrize(
    "text",
    [
        "ANTHROPIC_API_KEY=",
        'api_key = "placeholder"',
        'password = "changeme"',
        "secret: <your-secret-here>",
        'auth_token = "${VAULT_TOKEN}"',
        "PRAXIS_LLM_PROVIDER=mock",
        "# Set PRAXIS_ANTHROPIC_API_KEY in .env",
    ],
)
def test_placeholders_and_documentation_are_not_flagged(
    guard_no_secrets: ModuleType, text: str
) -> None:
    assert guard_no_secrets.scan_text(text, "docs/example.md") == []


def test_a_real_dotenv_is_refused(guard_no_secrets: ModuleType) -> None:
    assert guard_no_secrets._dotenv_violation(".env") is not None
    assert guard_no_secrets._dotenv_violation("config/.env.local") is not None


def test_the_example_dotenv_is_allowed(guard_no_secrets: ModuleType) -> None:
    assert guard_no_secrets._dotenv_violation(".env.example") is None


def test_the_hooks_directory_exempts_itself(guard_no_secrets: ModuleType) -> None:
    """A scanner that scans its own pattern table finds only itself."""
    assert guard_no_secrets._is_exempt(".claude/hooks/guard_no_secrets.py")
    assert guard_no_secrets._is_exempt(".claude\\hooks\\guard_no_secrets.py")
    assert not guard_no_secrets._is_exempt("praxis/config/settings.py")


def test_exemption_holds_for_absolute_paths(guard_no_secrets: ModuleType) -> None:
    """Regression: the staged scan sees relative paths, tool payloads absolute.

    A prefix-only match exempted the hooks directory during a commit and not
    during an edit, so writing this very test file was blocked.
    """
    assert guard_no_secrets._is_exempt("C:\\Users\\x\\proj\\tests\\hooks\\test_guard.py")
    assert guard_no_secrets._is_exempt("/home/x/proj/.claude/hooks/guard_no_secrets.py")
    assert not guard_no_secrets._is_exempt("/home/x/proj/praxis/cli.py")


def test_scanning_survives_text_the_console_codepage_cannot_encode(
    guard_no_secrets: ModuleType,
) -> None:
    """Regression: box-drawing characters in a diagram crashed the scanner.

    subprocess's text=True decodes with the locale encoding, cp1252 on this
    machine, so `git show` on ARCHITECTURE.md raised inside the reader thread
    and left stdout as None. The scanner then crashed on every commit touching
    a file containing a diagram.
    """
    diagram = (
        "\u250c\u2500\u2500\u2510\n"
        "\u2502 sp \u2502  \u2192 Decision  \u2713\n"
        "\u2514\u2500\u2500\u2518"
    )

    assert guard_no_secrets.scan_text(diagram, "ARCHITECTURE.md") == []


def test_git_output_is_decoded_as_utf8(guard_no_secrets: ModuleType) -> None:
    result = guard_no_secrets._git("--version")

    assert result.returncode == 0
    assert isinstance(result.stdout, str)


def test_a_malformed_hook_payload_never_blocks(
    guard_no_secrets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failing open on an unparseable payload; failing closed would wedge edits."""
    monkeypatch.setattr("sys.stdin", _Stdin("not json at all"))

    assert guard_no_secrets.check_tool_call() == 0


def test_writing_a_secret_through_a_tool_call_is_blocked(
    guard_no_secrets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:

    payload = json.dumps(
        {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "praxis/x.py",
                "content": f'KEY = "{ANTHROPIC_SHAPED}"',
            },
        }
    )
    monkeypatch.setattr("sys.stdin", _Stdin(payload))

    assert guard_no_secrets.check_tool_call() == 2


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text
