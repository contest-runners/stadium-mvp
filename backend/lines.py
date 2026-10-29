"""Commentary lines and the audio file naming rule.

The server and the audio generation script both use this module, so a line's
text and its mp3 file name always agree. Messages carry only the file name;
screens request "/audio/" + encodeURIComponent(name).
"""
import re

# 1st commentary, right after the goal. {name} is the team name.
TEAM_LINES = {
    "선제골": "{name}! 선제골입니다!!",
    "동점골": "{name}! 경기를 원점으로 돌립니다! 동점골!!",
    "역전골": "{name}! 드디어 경기를 뒤집습니다! 역전골!!",
    "앞서가는골": "{name}! 다시 앞서 나갑니다!!",
    "추격골": "{name}! 추격의 불씨를 살립니다!!",
    "달아나는골": "{name}! 한 골 더 달아납니다!!",
}

# 2nd commentary, after the scorer is tapped. {name} is the nickname.
PLAYER_LINES = {
    "선제골": "{name}!!! 선제골의 주인공입니다!!!",
    "동점골": "{name}!!! 동점골입니다!!!",
    "역전골": "{name}!!! 역전골입니다!!!",
    "앞서가는골": "{name}!!! 리드를 가져옵니다!!!",
    "추격골": "{name}!!! 추격골입니다!!!",
    "달아나는골": "{name}!!! 격차를 벌립니다!!!",
    "해트트릭": "{name}!!! 해트트릭입니다!!!",
}

FIXED_LINES = {
    "no_goal": "판독 결과, 노골입니다!",
    "kickoff": "경기 시작합니다!",
    "halftime": "전반전 종료!",
    "second_half": "후반전 시작합니다!",
    "match_end": "경기 종료!",
}

# Crowd cheer. A person drops this file in; it is not generated.
CROWD_FILE = "cheer.mp3"

_FORBIDDEN = re.compile(r'[\\/:*?"<>|]')


def clean_name(name: str) -> str:
    """Name as used in file names: spaces become _, characters Windows forbids are dropped."""
    return _FORBIDDEN.sub("", re.sub(r"\s", "_", name.strip()))


def audio_file(kind: str, name: str, situation: str | None = None) -> str:
    """File name inside the audio folder.

    kind is "team" (name = team name), "player" (name = nickname) or
    "fixed" (name = a FIXED_LINES key, no situation).
    """
    if kind == "fixed":
        return f"fixed_{name}.mp3"
    return f"{kind}_{clean_name(name)}_{situation}.mp3"


def required_audio(team_names: list[str], nicknames: list[str]) -> dict[str, str]:
    """Every mp3 the given names need, as {file name: text to speak}. The crowd cheer is not included."""
    files = {}
    for name in team_names:
        for situation, line in TEAM_LINES.items():
            files[audio_file("team", name, situation)] = line.format(name=name)
    for name in nicknames:
        for situation, line in PLAYER_LINES.items():
            files[audio_file("player", name, situation)] = line.format(name=name)
    for key, line in FIXED_LINES.items():
        files[audio_file("fixed", key)] = line
    return files
