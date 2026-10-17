from __future__ import annotations

from pathlib import Path

import pytest

from tandem.config import REPO_CONFIG_NAME, load_policy
from tandem.errors import InvalidInputError
from tandem.model.attribution import EvidenceClass
from tandem.model.interactions import WindowMode


def test_defaults_match_spec() -> None:
    policy = load_policy()
    assert policy.horizon_commits == 5
    assert policy.window_hours == 24
    assert policy.window_commits == 5
    assert policy.window_mode is WindowMode.EITHER
    assert policy.confidence is EvidenceClass.VERIFIED
    assert policy.include_worktrees is False
    assert policy.allow_execution is False


def test_precedence_cli_over_repo_over_global(tmp_path: Path) -> None:
    gconf = tmp_path / "global.toml"
    gconf.write_text("[tandem]\nhorizon_commits = 7\nwindow_hours = 12\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / REPO_CONFIG_NAME).write_text("[tandem]\nhorizon_commits = 9\n")
    policy = load_policy(repo, {"window_hours": 6.0}, trust_repo_config=True, global_config=gconf)
    assert policy.horizon_commits == 9
    assert policy.window_hours == 6.0


def test_untrusted_repo_config_is_ignored(tmp_path: Path) -> None:
    (tmp_path / REPO_CONFIG_NAME).write_text("[tandem]\nhorizon_commits = 9\n")
    assert load_policy(tmp_path).horizon_commits == 5


@pytest.mark.parametrize("key", ["claude_root = '/'", "allow_execution = true"])
def test_repo_config_cannot_widen_access(tmp_path: Path, key: str) -> None:
    (tmp_path / REPO_CONFIG_NAME).write_text(f"[tandem]\n{key}\n")
    with pytest.raises(InvalidInputError):
        load_policy(tmp_path, trust_repo_config=True)


def test_unknown_key_rejected(tmp_path: Path) -> None:
    gconf = tmp_path / "g.toml"
    gconf.write_text("[tandem]\nhorizon = 5\n")
    with pytest.raises(InvalidInputError):
        load_policy(global_config=gconf)


def test_invalid_values_rejected() -> None:
    with pytest.raises(InvalidInputError):
        load_policy(cli_overrides={"horizon_commits": 0})
    with pytest.raises(InvalidInputError):
        load_policy(cli_overrides={"confidence": "ambiguous"})
