"""Where the database file lives, and whether that location is safe.

Implements [ADR 0010](../../docs/adr/0010-store-location-under-a-syncing-filesystem.md).

The repository this project is developed in sits inside a OneDrive folder, and
SQLite in WAL mode keeps `-wal` and `-shm` sidecars whose contents must stay
consistent with the main file. A sync client uploads all three independently, so
a restored set can pair a `.db` with a `-wal` from a different instant -- which
is corruption that surfaces as a malformed-database error long after the write
that caused it. The fix is not to detect that; it is not to be there.

So the default data directory is the platform's own, and the detection below
exists for the case the ADR flagged as the residual risk: `%LOCALAPPDATA%` can
itself be redirected into OneDrive by an enterprise known-folder policy, in
which case the safe default is silently unsafe. That is why this warns rather
than fails -- a deliberate override is legitimate, and a tool that refuses to
run is a tool that gets forced.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import platformdirs

APP_NAME: Final = "praxis"

SYNC_ROOT_NAMES: Final[tuple[str, ...]] = (
    "onedrive",
    "dropbox",
    "google drive",
    "googledrive",
    "icloud drive",
    "iclouddrive",
    "box sync",
)
"""Directory names that indicate a file-sync client owns the tree.

Matched case-insensitively against each path component, and against the start of
a component so `OneDrive - Contoso` is caught -- the business variant of the
folder name is the one most likely to appear on a machine with a redirected
known folder, which is exactly the case this check exists for.
"""


def default_data_dir() -> Path:
    """Return the platform data directory Praxis stores its database in.

    `%LOCALAPPDATA%\\praxis` on Windows, `$XDG_DATA_HOME/praxis` (falling back
    to `~/.local/share/praxis`) elsewhere. `PRAXIS_DATA_DIR` overrides it.

    Resolved through `platformdirs` rather than assembled by hand: the XDG
    fallback chain and the macOS convention are the kind of detail that stays
    quietly wrong for a year, and this is not a place where being quietly wrong
    is cheap.
    """
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))


def sync_root_of(path: Path) -> str | None:
    """Return the sync-client directory `path` sits under, or `None`.

    Purely lexical -- it inspects the path's components and touches no disk.
    A junction or symlink into a synced tree therefore goes unnoticed, which is
    the known limit of this check and the reason ADR 0010 assumption 5 exists.

    Args:
        path: The directory to examine. Need not exist.

    Returns:
        The name of the offending path component, or `None` if there is none.
    """
    for part in path.parts:
        lowered = part.lower()
        for name in SYNC_ROOT_NAMES:
            if lowered == name or lowered.startswith(f"{name} ") or lowered.startswith(f"{name}-"):
                return part
    return None


def sync_warning(path: Path) -> str | None:
    """Return an actionable warning if `path` is under a sync root, else `None`.

    The wording names the concrete remedy rather than the risk in the abstract,
    because someone reading this has already chosen the location and needs to
    know what to do about it, not to be told it was a bad idea.
    """
    root = sync_root_of(path)
    if root is None:
        return None
    return (
        f"data_dir sits under {root!r}, which a file-sync client manages. "
        f"SQLite's WAL sidecars can be uploaded out of step with the database, "
        f"which corrupts it silently. Either set PRAXIS_DATA_DIR to a path "
        f"outside {root!r}, or set PRAXIS_JOURNAL_MODE=delete to drop the "
        f"sidecars entirely. See docs/adr/0010."
    )
