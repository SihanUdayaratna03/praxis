"""ADR 0010: the store's default location, and the check on the default itself.

The interesting test here is the enterprise one. `%LOCALAPPDATA%` can be
redirected into OneDrive by a known-folder policy, which would make the safe
default silently unsafe -- ADR 0010 assumption 1 exists for exactly that, and
`sync_root_of` is what would fire it.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest
from praxis.config.settings import JournalMode, Settings
from praxis.store.location import default_data_dir, sync_root_of, sync_warning


def test_the_default_is_the_platform_data_directory():
    default = default_data_dir()

    assert default.name == "praxis"
    assert default.is_absolute()


def test_the_default_is_not_inside_the_repository():
    # The whole point of ADR 0010. A relative default would resolve inside the
    # OneDrive-synced working copy on the machine this is developed on.
    assert default_data_dir().is_absolute()
    assert Path.cwd() not in default_data_dir().parents


def test_settings_uses_the_platform_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PRAXIS_DATA_DIR", raising=False)

    assert Settings().data_dir == default_data_dir()


def test_the_environment_still_overrides_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("PRAXIS_DATA_DIR", str(tmp_path / "elsewhere"))

    assert Settings().data_dir == tmp_path / "elsewhere"


# The Windows cases are spelled as PureWindowsPath rather than Path. Under a
# POSIX Path a backslash is an ordinary character, so the whole string is one
# component and a lexical check has nothing to look at -- the assertion would
# hold on Windows and fail in CI, testing the platform rather than the rule.


@pytest.mark.parametrize(
    "path",
    [
        PureWindowsPath(r"C:\Users\sihan\OneDrive\Desktop\Praxis Agents\.praxis"),
        PureWindowsPath(r"C:\Users\sihan\OneDrive - Contoso\praxis"),
        PurePosixPath("/home/sihan/Dropbox/praxis"),
        PurePosixPath("/Users/sihan/Google Drive/praxis"),
        PurePosixPath("/Users/sihan/Library/Mobile Documents/iCloud Drive/praxis"),
    ],
)
def test_known_sync_roots_are_detected(path):
    assert sync_root_of(path) is not None


@pytest.mark.parametrize(
    "path",
    [
        PureWindowsPath(r"C:\Users\sihan\AppData\Local\praxis"),
        PurePosixPath("/home/sihan/.local/share/praxis"),
        PurePosixPath("/var/lib/praxis"),
        PurePosixPath("/home/sihan/onedriver/praxis"),  # a substring, not a sync root
    ],
)
def test_ordinary_paths_are_not_flagged(path):
    assert sync_root_of(path) is None


def test_the_business_variant_of_the_folder_name_is_caught():
    # OneDrive for Business appends the tenant name, and it is the variant most
    # likely to appear on a machine with a redirected known folder.
    redirected = PureWindowsPath(r"C:\Users\s\OneDrive - Acme Corp\praxis")

    assert sync_root_of(redirected) == "OneDrive - Acme Corp"


def test_detection_is_case_insensitive():
    assert sync_root_of(PurePosixPath("/home/s/dropbox/praxis")) is not None
    assert sync_root_of(PurePosixPath("/home/s/DROPBOX/praxis")) is not None


def test_a_redirected_local_appdata_would_be_caught():
    # ADR 0010 assumption 1. If a known-folder policy redirects %LOCALAPPDATA%,
    # the default this project trusts is unsafe, and this is what notices.
    redirected = PureWindowsPath(r"C:\Users\sihan\OneDrive\AppData\Local\praxis")

    assert sync_root_of(redirected) == "OneDrive"


def test_the_warning_names_a_remedy_rather_than_the_risk():
    warning = sync_warning(Path("/home/s/Dropbox/praxis"))

    assert warning is not None
    assert "PRAXIS_DATA_DIR" in warning
    assert JournalMode.DELETE.value in warning
    assert "0010" in warning


def test_no_warning_for_a_safe_path():
    assert sync_warning(Path("/var/lib/praxis")) is None


def test_the_real_default_on_this_machine_is_reported():
    # Not an assertion about the outcome -- on a machine with a redirected known
    # folder this is expected to be non-None, and that is the finding.
    warning = sync_warning(default_data_dir())

    assert warning is None or "PRAXIS_DATA_DIR" in warning
    assert os.name in {"nt", "posix"}
