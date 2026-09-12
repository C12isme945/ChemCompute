"""
Tests for ChemCompute Online Update checker and SemVer comparison.
"""

from chemcompute.updater.online_update import parse_semver, check_github_updates


def test_parse_semver():
    assert parse_semver("v0.3.0") == (0, 3, 0)
    assert parse_semver("0.3.1") == (0, 3, 1)
    assert parse_semver("v1.0.0-rc1") == (1, 0, 0)
    assert parse_semver("0.2.0") < parse_semver("0.3.0")
    assert parse_semver("0.3.0") < parse_semver("0.3.1")
    assert parse_semver("0.3.0") == parse_semver("v0.3.0")


def test_check_github_updates_structure():
    # Calling check_github_updates should not throw even if network fails or mock
    res = check_github_updates("0.2.0")
    assert isinstance(res, dict)
    assert "current_version" in res
    assert "latest_version" in res
    assert "has_update" in res
    assert "download_url" in res
    assert "status" in res
