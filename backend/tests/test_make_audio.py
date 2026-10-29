import asyncio

from backend import make_audio

TEAMS = ["A팀", "B팀"]
PLAYERS = ["호랑이", "번개", "독수리"]
EXPECTED = 2 * 6 + len(PLAYERS) * 7 + 5


def test_makes_every_needed_file(tmp_path, fake_tts):
    result = asyncio.run(make_audio.generate(TEAMS, PLAYERS, tmp_path))
    assert len(result["made"]) == EXPECTED
    assert result["skipped"] == [] and result["failed"] == []
    assert len(fake_tts) == EXPECTED
    assert len(list(tmp_path.glob("*.mp3"))) == EXPECTED
    assert not (tmp_path / "cheer.mp3").exists()


def test_skips_existing_files(tmp_path, fake_tts):
    (tmp_path / "fixed_no_goal.mp3").write_bytes(b"recorded by hand")
    result = asyncio.run(make_audio.generate(TEAMS, PLAYERS, tmp_path))
    assert result["skipped"] == ["fixed_no_goal.mp3"]
    assert len(result["made"]) == EXPECTED - 1
    assert (tmp_path / "fixed_no_goal.mp3").read_bytes() == b"recorded by hand"

    again = asyncio.run(make_audio.generate(TEAMS, PLAYERS, tmp_path))
    assert again["made"] == [] and len(again["skipped"]) == EXPECTED
