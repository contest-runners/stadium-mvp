"""Stadium MVP backend.

Takes goal signals, decides score and situation, picks the commentary audio and
pushes it over WebSocket to the scoreboard and the host's phone. All state is
one in-memory dict. The contract with web (오) and detector (최) is backend/API.md (v2.1).

Run from the repo root:
    uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Union

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AfterValidator, BaseModel, Field

from . import lines, make_audio, situation

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
FALLBACK_DIR = ROOT / "backend" / "fallback"
# Module constants so tests can point them at a temp folder.
AUDIO_DIR = ROOT / "audio"
RESULTS_DIR = ROOT / "eval" / "results"

COOLDOWN_MS = 10_000  # detector signals on the same goal mouth
CANCEL_WINDOW_MS = 10_000
IN_PLAY = ("first_half", "second_half")
LIVE = ("first_half", "halftime", "second_half")
# Goal fields sent to the screens (plus time and nick). The rest (ts, conf, received_at,
# cancelled_at) only go to the results file.
GOAL_FIELDS = ("goal_id", "team", "scorer", "situation", "sit2", "half", "clock", "source", "cancelled",
               "cancel_deadline")
FIRST_HALF_SIDES = {"left": "A", "right": "B"}  # team defending each goal at kickoff


def now_ms() -> int:
    """The server's only clock, in Unix ms. Tests replace it."""
    return int(time.time() * 1000)


def default_match() -> dict:
    return {
        "status": "setup",
        "half": 1,
        "half_minutes": 15,
        "swap_at_half": False,  # stored only; the swap sent at halftime decides
        "period_started_at": None,
        "stopped_elapsed": 0,  # seconds shown while the clock is stopped (halftime, ended)
        "teams": {"A": {"name": "A팀", "color": "#f97316"}, "B": {"name": "B팀", "color": "#3b82f6"}},
        "players": {"A": [], "B": []},
        "swapped": False,
        "goals": [],
        "last_signal_at": {"left": None, "right": None},  # last accepted detector signal per goal mouth
    }


MATCH = default_match()
CONNECTIONS = set()

app = FastAPI(title="Stadium MVP backend")
# Allow everything, including pages opened from file://.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(RequestValidationError)
async def bad_request(request: Request, exc: RequestValidationError):
    # The contract answers bad values with 400 and a string detail, not FastAPI's 422 list.
    errors = "; ".join(
        f"{'.'.join(str(p) for p in e['loc'][1:]) or e['loc'][0]}: {e['msg']}" for e in exc.errors()
    )
    return JSONResponse(status_code=400, content={"detail": f"잘못된 값입니다 ({errors})"})


# ---- request bodies ----

def _name(value: str) -> str:
    value = value.strip()
    if not lines.clean_name(value):
        raise ValueError("이름이 비어 있습니다")
    return value


Name = Annotated[str, AfterValidator(_name)]


class TeamIn(BaseModel):
    name: Name
    color: str
    players: list[str] = []  # 오's format carries the players inside each team


class TeamsIn(BaseModel):
    A: TeamIn
    B: TeamIn


class PlayersIn(BaseModel):
    A: list[str]
    B: list[str]


class MatchIn(BaseModel):
    # v1 sends {"A": team, "B": team} plus top-level players; 오's screen sends [team A, team B].
    teams: Union[TeamsIn, Annotated[list[TeamIn], Field(min_length=2, max_length=2)]]
    players: PlayersIn | None = None
    half_minutes: int = Field(15, gt=0)
    swap_at_half: bool = False


class HalftimeIn(BaseModel):
    swap: bool = True


class GoalSignal(BaseModel):
    goal_side: Literal["left", "right"]
    ts: float | None = None
    conf: float | None = None
    source: str | None = None  # any label is recorded as sent; missing or null means "live"


class ManualGoalIn(BaseModel):
    team: Literal["A", "B"]


class ScorerIn(BaseModel):
    # Tried in this order.
    nickname: str | None = None
    nick: str | None = None
    player_index: int | None = None


# ---- state helpers ----

def other(team: str) -> str:
    return "B" if team == "A" else "A"


def clean_players(names: list[str]) -> list[str]:
    """Trim names and drop blank or duplicate ones, keeping the order."""
    result = []
    for name in names:
        name = name.strip()
        if lines.clean_name(name) and name not in result:
            result.append(name)
    return result


def sides() -> dict:
    """Team defending each goal (scoreboard view): the kickoff sides, flipped if swapped at halftime."""
    if MATCH["swapped"]:
        return {"left": FIRST_HALF_SIDES["right"], "right": FIRST_HALF_SIDES["left"]}
    return dict(FIRST_HALF_SIDES)


def valid_goals() -> list[dict]:
    return [g for g in MATCH["goals"] if not g["cancelled"]]


def score() -> dict:
    result = {"A": 0, "B": 0}
    for g in valid_goals():
        result[g["team"]] += 1
    return result


def audio_ready() -> bool:
    team_names = [MATCH["teams"][t]["name"] for t in ("A", "B")]
    nicknames = MATCH["players"]["A"] + MATCH["players"]["B"]
    return all((AUDIO_DIR / f).is_file() for f in lines.required_audio(team_names, nicknames))


def refresh_sit2():
    """Mark each player's third valid goal, in time order, as the hat trick; every other goal gets None."""
    counts = {}
    for g in MATCH["goals"]:
        g["sit2"] = None
        if g["cancelled"] or g["scorer"] is None:
            continue
        key = (g["team"], g["scorer"])
        counts[key] = counts.get(key, 0) + 1
        if counts[key] == 3:
            g["sit2"] = "해트트릭"


def goal_view(goal: dict) -> dict:
    return {**{k: goal[k] for k in GOAL_FIELDS}, "time": goal["clock"], "nick": goal["scorer"]}


def period_elapsed(now: int) -> int:
    """Whole seconds since the current half started. Only valid while a half is running."""
    return (now - MATCH["period_started_at"]) // 1000


def public_state() -> dict:
    now = now_ms()
    running = MATCH["status"] in IN_PLAY
    return {
        "status": MATCH["status"],
        "phase": MATCH["status"],
        "half": MATCH["half"],
        "half_minutes": MATCH["half_minutes"],
        "period_started_at": MATCH["period_started_at"],
        "server_now": now,
        "clock": {"running": running, "elapsed": period_elapsed(now) if running else MATCH["stopped_elapsed"]},
        "teams": {t: {**MATCH["teams"][t], "players": MATCH["players"][t]} for t in ("A", "B")},
        "players": MATCH["players"],
        "score": score(),
        "sides": sides(),
        "swapped": MATCH["swapped"],
        "audio_ready": audio_ready(),
        "goals": [goal_view(g) for g in MATCH["goals"]],
    }


def fixed_line(key: str) -> dict:
    return {"audio": lines.audio_file("fixed", key), "text": lines.FIXED_LINES[key]}


async def publish(event: dict | None = None):
    """Send the event (if any) to every connection, then the fresh state."""
    messages = ([event] if event else []) + [{"type": "state", **public_state()}]
    for message in messages:
        for ws in list(CONNECTIONS):
            try:
                await ws.send_json(message)
            except Exception:  # connection already gone
                CONNECTIONS.discard(ws)


def require_status(*allowed: str):
    if MATCH["status"] not in allowed:
        raise HTTPException(409, f"지금 상태({MATCH['status']})에서는 할 수 없습니다. 가능한 상태: {', '.join(allowed)}")


def find_goal(goal_id: int) -> dict:
    for g in MATCH["goals"]:
        if g["goal_id"] == goal_id:
            return g
    raise HTTPException(404, f"{goal_id}번 골이 없습니다.")


def pick_scorer(body: ScorerIn, team: str) -> str:
    """nickname, then nick, then player_index into the scoring team's players."""
    for name in (body.nickname, body.nick):
        if name and name.strip():
            return name.strip()
    if body.player_index is None:
        raise HTTPException(400, "nickname, nick, player_index 중 하나가 필요합니다.")
    players = MATCH["players"][team]
    if not 0 <= body.player_index < len(players):
        raise HTTPException(400, f"player_index {body.player_index}에 해당하는 {MATCH['teams'][team]['name']} 선수가 없습니다.")
    return players[body.player_index]


async def add_goal(team: str, source: str, ts: float | None = None, conf: float | None = None) -> int:
    now = now_ms()
    sit = situation.goal_situation([g["team"] for g in valid_goals()], team)
    elapsed = period_elapsed(now)
    goal = {
        "goal_id": len(MATCH["goals"]) + 1,
        "team": team,
        "scorer": None,
        "situation": sit,
        "sit2": None,  # "해트트릭" on a player's third valid goal; see refresh_sit2
        "half": MATCH["half"],
        "clock": f"{elapsed // 60:02d}:{elapsed % 60:02d}",
        "source": source,
        "cancelled": False,
        "cancel_deadline": now + CANCEL_WINDOW_MS,
        "ts": ts,
        "conf": conf,
        "received_at": now,
        "cancelled_at": None,
    }
    MATCH["goals"].append(goal)
    name = MATCH["teams"][team]["name"]
    await publish({
        "type": "goal",
        "goal_id": goal["goal_id"],
        "team": team,
        "situation": sit,
        "audio": lines.audio_file("team", name, sit),
        "sfx": lines.CROWD_FILE,
        "text": lines.TEAM_LINES[sit].format(name=name),
        "cancel_deadline": goal["cancel_deadline"],
        "cancel_until": goal["cancel_deadline"],
        "score": score(),
        "time": goal["clock"],
        "half": goal["half"],
    })
    return goal["goal_id"]


def save_results() -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(now_ms() / 1000).strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"match_{stamp}.json"
    data = {"teams": MATCH["teams"], "players": MATCH["players"], "score": score(), "goals": MATCH["goals"]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def prepare_audio(team_names: list[str], nicknames: list[str]):
    """Make the missing mp3s for the new names, then push the recomputed audio_ready.

    Runs after the response is sent. If edge-tts fails the match goes on and the
    screens read `text` aloud instead.
    """
    try:
        result = await make_audio.generate(team_names, nicknames, AUDIO_DIR)
        print(f"음성 생성: 만듦 {len(result['made'])}, 건너뜀 {len(result['skipped'])}, 실패 {len(result['failed'])}",
              flush=True)
    except Exception as e:
        print(f"음성 생성 중단: {type(e).__name__}: {e}", flush=True)
    await publish()


# ---- match flow ----

@app.post("/api/match")
async def new_match(body: MatchIn, background: BackgroundTasks):
    a, b = body.teams if isinstance(body.teams, list) else (body.teams.A, body.teams.B)
    players = body.players or PlayersIn(A=a.players, B=b.players)
    MATCH.clear()
    MATCH.update(default_match())
    MATCH.update(
        teams={"A": {"name": a.name, "color": a.color}, "B": {"name": b.name, "color": b.color}},
        players={"A": clean_players(players.A), "B": clean_players(players.B)},
        half_minutes=body.half_minutes,
        swap_at_half=body.swap_at_half,
    )
    await publish()
    background.add_task(prepare_audio, [a.name, b.name], MATCH["players"]["A"] + MATCH["players"]["B"])
    return public_state()


@app.get("/api/match")
async def get_match():
    return public_state()


@app.post("/api/match/kickoff")
async def kickoff():
    require_status("setup")
    MATCH.update(status="first_half", period_started_at=now_ms())
    await publish({"type": "period", "status": "first_half", **fixed_line("kickoff")})
    return public_state()


@app.post("/api/match/halftime")
async def halftime(body: HalftimeIn | None = None):
    # 오's screen calls this twice: on the halftime button, then with the side choice.
    # swap is always applied to the kickoff sides, so repeats never flip twice.
    swap = body.swap if body else True
    if MATCH["status"] == "halftime":
        MATCH["swapped"] = swap
        await publish()
        return public_state()
    require_status("first_half")
    MATCH.update(status="halftime", stopped_elapsed=period_elapsed(now_ms()), period_started_at=None, swapped=swap)
    await publish({"type": "period", "status": "halftime", **fixed_line("halftime")})
    return public_state()


@app.post("/api/match/second-half")
async def second_half():
    require_status("halftime")
    MATCH.update(status="second_half", half=2, period_started_at=now_ms())
    await publish({"type": "period", "status": "second_half", **fixed_line("second_half")})
    return public_state()


@app.post("/api/match/end")
async def end_match():
    require_status(*LIVE)
    if MATCH["status"] in IN_PLAY:  # ended from halftime keeps the first half's stopped time
        MATCH["stopped_elapsed"] = period_elapsed(now_ms())
    MATCH.update(status="ended", period_started_at=None)
    print(f"결과 저장: {save_results()}", flush=True)
    final = score()
    winner = "A" if final["A"] > final["B"] else "B" if final["B"] > final["A"] else None
    await publish({
        "type": "match_end",
        "score": final,
        "winner": winner,
        "goals": [goal_view(g) for g in MATCH["goals"]],
        **fixed_line("match_end"),
    })
    return public_state()


# ---- goals ----

@app.post("/api/events/goal")
async def goal_signal(body: GoalSignal):
    if MATCH["status"] not in IN_PLAY:
        return {"accepted": False, "reason": "not_in_play"}
    now = now_ms()
    last = MATCH["last_signal_at"][body.goal_side]
    if last is not None and now - last < COOLDOWN_MS:
        return {"accepted": False, "reason": "cooldown"}
    MATCH["last_signal_at"][body.goal_side] = now
    # The goal mouth's defender conceded, so the other team scored.
    team = other(sides()[body.goal_side])
    source = "live" if body.source is None else body.source
    return {"accepted": True, "goal_id": await add_goal(team, source, body.ts, body.conf)}


@app.post("/api/goals/manual")
async def manual_goal(body: ManualGoalIn):
    require_status(*IN_PLAY)
    return {"accepted": True, "goal_id": await add_goal(body.team, "manual")}


@app.post("/api/goals/{goal_id}/cancel")
async def cancel_goal(goal_id: int):
    goal = find_goal(goal_id)
    require_status(*LIVE)
    if goal["cancelled"]:
        raise HTTPException(409, f"{goal_id}번 골은 이미 취소됐습니다.")
    now = now_ms()
    if now > goal["cancel_deadline"]:
        raise HTTPException(409, f"{goal_id}번 골은 취소 가능 시간(10초)이 지났습니다.")
    goal.update(cancelled=True, cancelled_at=now)
    refresh_sit2()
    await publish({
        "type": "goal_cancelled",
        "goal_id": goal_id,
        "team": goal["team"],
        "score": score(),
        **fixed_line("no_goal"),
    })
    return public_state()


@app.post("/api/goals/{goal_id}/scorer")
async def set_scorer(goal_id: int, body: ScorerIn):
    goal = find_goal(goal_id)
    require_status(*LIVE)
    if goal["cancelled"]:
        raise HTTPException(409, f"{goal_id}번 골은 취소된 골입니다.")
    team = goal["team"]
    nickname = pick_scorer(body, team)
    if nickname not in MATCH["players"][team]:
        raise HTTPException(400, f"{nickname}은(는) {MATCH['teams'][team]['name']} 선수가 아닙니다.")
    goal["scorer"] = nickname
    refresh_sit2()
    player_goals = sum(1 for g in valid_goals() if g["team"] == team and g["scorer"] == nickname)
    sit = situation.scorer_situation(player_goals, goal["situation"])
    await publish({
        "type": "scorer_set",
        "goal_id": goal_id,
        "team": team,
        "scorer": nickname,
        "nick": nickname,
        "situation": sit,
        "goal_situation": goal["situation"],
        "player_goals": player_goals,
        "audio": lines.audio_file("player", nickname, sit),
        "text": lines.PLAYER_LINES[sit].format(name=nickname),
    })
    return public_state()


# ---- WebSocket ----

@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    CONNECTIONS.add(ws)
    try:
        await ws.send_json({"type": "state", **public_state()})
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass  # clients send nothing; just wait for the close
    except WebSocketDisconnect:
        pass
    finally:
        CONNECTIONS.discard(ws)


# ---- pages and static files ----

def page(name: str) -> FileResponse:
    path = WEB_DIR / name
    return FileResponse(path if path.is_file() else FALLBACK_DIR / name)


@app.get("/")
async def index():
    return page("index.html")


@app.get("/host")
async def host():
    return page("host.html")


AUDIO_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")
app.mount("/web", StaticFiles(directory=WEB_DIR, check_dir=False), name="web")
