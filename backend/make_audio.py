"""Generate the commentary mp3s with edge-tts.

Names come from audio/names.json (same format as devtool setup). Texts and file
names come from backend/lines.py. The crowd cheer (lines.CROWD_FILE, cheer.mp3)
is not generated; a person picks a free crowd sound and drops it in.

    python -m backend.make_audio
    python -m backend.make_audio --force --voice ko-KR-HyunsuMultilingualNeural --rate=+25%
"""
import argparse
import asyncio
import json
import secrets
from pathlib import Path

import edge_tts

from . import lines

ROOT = Path(__file__).resolve().parent.parent
AUDIO_DIR = ROOT / "audio"
NAMES_FILE = AUDIO_DIR / "names.json"
EXAMPLE_NAMES = {
    "teams": {"A": "A팀", "B": "B팀"},
    "players": {"A": ["호랑이", "번개"], "B": ["독수리", "여우"]},
}
DEFAULT_VOICE = "ko-KR-InJoonNeural"  # male; `edge-tts --list-voices` lists the others
DEFAULT_RATE = "+15%"
CONCURRENCY = 4


async def synthesize(text: str, path: Path, voice: str, rate: str):
    """The only edge-tts call. Tests replace it."""
    await edge_tts.Communicate(text, voice, rate=rate).save(str(path))


async def generate(team_names: list[str], nicknames: list[str], audio_dir: Path, force: bool = False,
                   voice: str = DEFAULT_VOICE, rate: str = DEFAULT_RATE) -> dict:
    """Make the mp3s these names need, 4 at a time. Existing files are skipped unless force.

    Returns {"made": [file, ...], "skipped": [file, ...], "failed": [(file, error), ...]}.
    """
    audio_dir.mkdir(parents=True, exist_ok=True)
    result = {"made": [], "skipped": [], "failed": []}
    slots = asyncio.Semaphore(CONCURRENCY)

    async def make(file: str, text: str):
        path = audio_dir / file
        if path.is_file() and not force:
            result["skipped"].append(file)
            return
        # edge-tts writes as it receives, so a failed run would leave a broken mp3
        # that later runs skip. Write to a unique temp name and rename when complete.
        part = path.with_name(f"{file}.{secrets.token_hex(4)}.part")
        async with slots:
            try:
                await synthesize(text, part, voice, rate)
                part.replace(path)
                result["made"].append(file)
            except Exception as e:
                part.unlink(missing_ok=True)
                result["failed"].append((file, f"{type(e).__name__}: {e}"))

    await asyncio.gather(*(make(f, t) for f, t in lines.required_audio(team_names, nicknames).items()))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m backend.make_audio", description="해설 음성 mp3 생성")
    parser.add_argument("--force", action="store_true", help="이미 있는 파일도 다시 만든다")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help=f"edge-tts 음성 (기본 {DEFAULT_VOICE})")
    parser.add_argument("--rate", default=DEFAULT_RATE,
                        help=f"말 속도 (기본 {DEFAULT_RATE}). 느리게 하려면 --rate=-10%% 처럼 =를 붙인다")
    args = parser.parse_args(argv)

    if not NAMES_FILE.is_file():
        NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
        NAMES_FILE.write_text(json.dumps(EXAMPLE_NAMES, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{NAMES_FILE}을(를) 예시 이름으로 만들었습니다. 이름을 채운 뒤 다시 실행하세요.")
        return

    names = json.loads(NAMES_FILE.read_text(encoding="utf-8-sig"))
    team_names = [names["teams"]["A"], names["teams"]["B"]]
    nicknames = names["players"]["A"] + names["players"]["B"]
    print(f"음성 {args.voice}, 속도 {args.rate}, 폴더 {AUDIO_DIR}")
    result = asyncio.run(generate(team_names, nicknames, AUDIO_DIR, args.force, args.voice, args.rate))

    for file, error in sorted(result["failed"]):
        print(f"  실패: {file} ({error})")
    if result["failed"] and not result["made"]:
        print("edge-tts 연결이 막힌 것 같습니다. 화면은 text를 브라우저 음성으로 읽어 대체합니다.")
    print(f"만듦 {len(result['made'])}개, 건너뜀 {len(result['skipped'])}개, 실패 {len(result['failed'])}개")
    samples = [lines.audio_file("team", team_names[0], "역전골"),
               lines.audio_file("player", nicknames[0], "해트트릭") if nicknames else None,
               lines.audio_file("fixed", "no_goal")]
    ready = [AUDIO_DIR / f for f in samples if f and (AUDIO_DIR / f).is_file()][:2]
    if ready:
        print("바로 들어 보기:")
        for path in ready:
            print(f"  {path}")


if __name__ == "__main__":
    main()
