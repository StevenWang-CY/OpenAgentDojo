"""MissionLoader scan + sha-stability tests."""

from __future__ import annotations

from app.missions.loader import MissionLoader


def test_loader_scans_sample(sample_mission_yaml) -> None:
    loader = MissionLoader(sample_mission_yaml.parent)
    loaded = loader.scan()
    assert len(loaded) == 1
    assert loaded[0].manifest.id == "sample-mission"
    sha1 = loaded[0].manifest_sha256
    loaded2 = loader.scan()
    assert loaded2[0].manifest_sha256 == sha1, "manifest hash should be stable"
    assert len(sha1) == 64


def test_loader_returns_empty_on_missing_root(tmp_path) -> None:
    loader = MissionLoader(tmp_path / "nonexistent")
    assert loader.scan() == []
