"""Stadium MVP backend.

Takes goal signals, decides score and situation, picks the commentary audio and
pushes it over WebSocket to the scoreboard and the host's phone. All state is
one in-memory dict. The contract with web (오) and detector (최) is backend/API.md.

Run from the repo root:
    uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

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
# Goal fields sent in `state`. The rest (ts, conf, received_at, cancelled_at) only go to the results file.
GOAL_FIELDS = ("goal_id", "team", "scorer", "situation", "half", "clock", "source", "cancelled", "cancel_deadline")


def now_ms() -> int:
    """The server's only clock, in Unix ms. Tests replace it."""
    return int(time.time() * 1000)


def default_match() -> dict:
    return {
        "status": "setup",
        "half_minutes": 15,
        "period_started_at": None,
        "teams": {"A": {"name": "A팀", "color": "#f97316"}, "B": {"name": "B팀", "color": "#3b82f6"}},
        "players": {"A": [], "B": []},
        "sides": {"left": "A", "right": "B"},
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


class TeamsIn(BaseModel):
    A: TeamIn
    B: TeamIn


class PlayersIn(BaseModel):
    A: list[Name]
    B: list[Name]


class MatchIn(BaseModel):
    teams: TeamsIn
    players: PlayersIn
    half_minutes: int = Field(15, gt=0)


class HalftimeIn(BaseModel):
    swap: bool = False


class GoalSignal(BaseModel):
    goal_side: Literal["left", "right"]
    ts: float | None = None
    conf: float | None = None
    source: Literal["live", "replay"] = "live"


class ManualGoalIn(BaseModel):
    team: Literal["A", "B"]


class ScorerIn(BaseModel):
    nickname: str


# ---- state helpers ----

def other(team: str) -> str:
    return "B" if team == "A" else "A"


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


def public_state() -> dict:
    return {
        "status": MATCH["status"],
        "half_minutes": MATCH["half_minutes"],
        "period_started_at": MATCH["period_started_at"],
        "server_now": now_ms(),
        "teams": MATCH["teams"],
        "players": MATCH["players"],
        "score": score(),
        "sides": MATCH["sides"],
        "audio_ready": audio_ready(),
        "goals": [{k: g[k] for k in GOAL_FIELDS} for g in MATCH["goals"]],
    }


def fixed_line(key: str) -> dict:
    return {"audio": lines.audio_url("fixed", key), "text": lines.FIXED_LINES[key]}


async def publish(event: dict | None = None):
    """Send the event (if any) to every connection, then the fresh state."""
    messages = ([event] if event else []) + [{"type": "state", "match": public_state()}]
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


async def add_goal(team: str, source: str, ts: float | None = None, conf: float | None = None) -> int:
    now = now_ms()
    before = [g["team"] for g in valid_goals()]
    s, o = before.count(team) + 1, before.count(other(team))
    sit = situation.classify(s, o, situation.has_trailed(before, team))
    elapsed = (now - MATCH["period_started_at"]) // 1000
    goal = {
        "goal_id": len(MATCH["goals"]) + 1,
        "team": team,
        "scorer": None,
        "situation": sit,
        "half": "first" if MATCH["status"] == "first_half" else "second",
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
        "audio": lines.audio_url("team", name, sit),
        "sfx": lines.CROWD_URL,
        "text": lines.TEAM_LINES[sit].format(name=name),
        "cancel_deadline": goal["cancel_deadline"],
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
    MATCH.clear()
    MATCH.update(default_match())
    MATCH.update(teams=body.teams.model_dump(), players=body.players.model_dump(), half_minutes=body.half_minutes)
    await publish()
    background.add_task(prepare_audio, [body.teams.A.name, body.teams.B.name], body.players.A + body.players.B)
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
    require_status("first_half")
    MATCH.update(status="halftime", period_started_at=None)
    if body and body.swap:
        MATCH["sides"] = {"left": MATCH["sides"]["right"], "right": MATCH["sides"]["left"]}
    await publish({"type": "period", "status": "halftime", **fixed_line("halftime")})
    return public_state()


@app.post("/api/match/second-half")
async def second_half():
    require_status("halftime")
    MATCH.update(status="second_half", period_started_at=now_ms())
    await publish({"type": "period", "status": "second_half", **fixed_line("second_half")})
    return public_state()


@app.post("/api/match/end")
async def end_match():
    require_status(*LIVE)
    MATCH.update(status="ended", period_started_at=None)
    print(f"결과 저장: {save_results()}", flush=True)
    final = score()
    winner = "A" if final["A"] > final["B"] else "B" if final["B"] > final["A"] else None
    await publish({"type": "match_end", "score": final, "winner": winner, **fixed_line("match_end")})
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
    team = other(MATCH["sides"][body.goal_side])
    return {"accepted": True, "goal_id": await add_goal(team, body.source, body.ts, body.conf)}


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
    await publish({"type": "goal_cancelled", "goal_id": goal_id, "team": goal["team"], **fixed_line("no_goal")})
    return public_state()


@app.post("/api/goals/{goal_id}/scorer")
async def set_scorer(goal_id: int, body: ScorerIn):
    goal = find_goal(goal_id)
    require_status(*LIVE)
    if goal["cancelled"]:
        raise HTTPException(409, f"{goal_id}번 골은 취소된 골입니다.")
    team, nickname = goal["team"], body.nickname.strip()
    if nickname not in MATCH["players"][team]:
        raise HTTPException(400, f"{nickname}은(는) {MATCH['teams'][team]['name']} 선수가 아닙니다.")
    goal["scorer"] = nickname
    sit = situation.scorer_situation(valid_goals(), team, nickname, goal["situation"])
    await publish({
        "type": "scorer_set",
        "goal_id": goal_id,
        "team": team,
        "scorer": nickname,
        "situation": sit,
        "audio": lines.audio_url("player", nickname, sit),
        "text": lines.PLAYER_LINES[sit].format(name=nickname),
    })
    return public_state()


# ---- WebSocket ----

@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    CONNECTIONS.add(ws)
    try:
        await ws.send_json({"type": "state", "match": public_state()})
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
