"""Command-line tool for driving the backend without the web screens.

    python -m backend.devtool setup
    python -m backend.devtool kickoff
    python -m backend.devtool goal left
    python -m backend.devtool scorer 1 호랑이
    python -m backend.devtool demo
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_NAMES = {
    "teams": {"A": "A팀", "B": "B팀"},
    "players": {"A": ["호랑이", "번개"], "B": ["독수리", "여우"]},
}
COLORS = {"A": "#f97316", "B": "#3b82f6"}


class ApiError(Exception):
    pass


class Api:
    """Calls the server. `session` can be a requests.Session or FastAPI's TestClient."""

    def __init__(self, server: str, session=None):
        self.server = server.rstrip("/")
        self.session = session or requests.Session()

    def __call__(self, method: str, path: str, body: dict | None = None) -> dict:
        r = self.session.request(method, self.server + path, json=body)
        try:
            data = r.json()
        except ValueError:
            data = {"detail": r.text}
        if r.status_code >= 400:
            raise ApiError(f"{method} {path} -> {r.status_code} {data.get('detail')}")
        return data


def load_names(path) -> dict:
    """Names from a names file (relative paths are from the repo root), or the defaults if it is missing."""
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    if path.is_file():
        print(f"이름 파일: {path}")
        return json.loads(path.read_text(encoding="utf-8-sig"))
    return DEFAULT_NAMES


def match_body(names: dict) -> dict:
    return {
        "teams": {t: {"name": names["teams"][t], "color": COLORS[t]} for t in ("A", "B")},
        "players": names["players"],
        "half_minutes": 15,
    }


def demo(api: Api, names: dict, sleep=time.sleep, interval: float = 3.0):
    """Rehearsal scenario, one step every `interval` seconds.

    Detector signals go to the goal the other team defends at that moment, and are
    spaced so one goal mouth never gets two signals within the 10 s cooldown.
    """
    first = {t: names["players"][t][0] for t in ("A", "B")}
    step = 0

    def run(label, method, path, body=None):
        nonlocal step
        if step:
            sleep(interval)
        step += 1
        result = api(method, path, body)
        m = api("GET", "/api/match")
        note = ""
        if "goal_id" in result:
            g = m["goals"][result["goal_id"] - 1]
            note = f" | {g['goal_id']}번 골: {g['team']} {g['situation']}"
        print(f"[{step:2}] {label}: A {m['score']['A']} : {m['score']['B']} B ({m['status']}){note}")
        return result

    def signal(team):
        sides = api("GET", "/api/match")["sides"]
        side = "right" if sides["left"] == team else "left"
        r = run(f"{team} 골 신호({side})", "POST", "/api/events/goal",
                {"goal_side": side, "ts": 0, "conf": 1.0, "source": "live"})
        if not r["accepted"]:
            raise ApiError(f"골 신호가 거절됐습니다: {r['reason']}")
        return r["goal_id"]

    def manual(team):
        return run(f"{team} 수동 골", "POST", "/api/goals/manual", {"team": team})["goal_id"]

    def scorer(goal_id, team):
        run(f"{goal_id}번 골 득점자 {first[team]}", "POST", f"/api/goals/{goal_id}/scorer", {"nickname": first[team]})

    run("경기 설정", "POST", "/api/match", match_body(names))
    run("킥오프", "POST", "/api/match/kickoff")
    scorer(signal("A"), "A")  # 1:0 선제골
    manual("B")  # 1:1 동점골
    run("하프타임(진영 교체)", "POST", "/api/match/halftime", {"swap": True})
    run("후반 시작", "POST", "/api/match/second-half")
    scorer(signal("B"), "B")  # 1:2 역전골
    goal_id = signal("A")  # 2:2, then cancelled right away
    run(f"{goal_id}번 골 취소", "POST", f"/api/goals/{goal_id}/cancel")
    scorer(manual("A"), "A")  # 2:2 동점골
    scorer(signal("A"), "A")  # 3:2 역전골, the scorer's 3rd goal: 해트트릭
    run("경기 종료", "POST", "/api/match/end")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m backend.devtool", description="백엔드 테스트 도구")
    parser.add_argument("--server", default="http://localhost:8000")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup").add_argument("--names", default="audio/names.json")
    sub.add_parser("kickoff")
    sub.add_parser("halftime").add_argument("--swap", action="store_true")
    sub.add_parser("second-half")
    sub.add_parser("end")
    sub.add_parser("state")
    sub.add_parser("goal").add_argument("side", choices=["left", "right"])
    sub.add_parser("manual").add_argument("team", choices=["A", "B"])
    p = sub.add_parser("scorer")
    p.add_argument("goal_id", type=int)
    p.add_argument("nickname")
    sub.add_parser("cancel").add_argument("goal_id", type=int)
    sub.add_parser("demo").add_argument("--names", default="audio/names.json")
    args = parser.parse_args(argv)

    api = Api(args.server)
    calls = {
        "setup": lambda: api("POST", "/api/match", match_body(load_names(args.names))),
        "kickoff": lambda: api("POST", "/api/match/kickoff"),
        "halftime": lambda: api("POST", "/api/match/halftime", {"swap": args.swap}),
        "second-half": lambda: api("POST", "/api/match/second-half"),
        "end": lambda: api("POST", "/api/match/end"),
        "state": lambda: api("GET", "/api/match"),
        "goal": lambda: api("POST", "/api/events/goal",
                            {"goal_side": args.side, "ts": 0, "conf": 1.0, "source": "live"}),
        "manual": lambda: api("POST", "/api/goals/manual", {"team": args.team}),
        "scorer": lambda: api("POST", f"/api/goals/{args.goal_id}/scorer", {"nickname": args.nickname}),
        "cancel": lambda: api("POST", f"/api/goals/{args.goal_id}/cancel"),
    }
    try:
        if args.cmd == "demo":
            demo(api, load_names(args.names))
        else:
            print(json.dumps(calls[args.cmd](), ensure_ascii=False, indent=2))
    except ApiError as e:
        sys.exit(f"오류: {e}")
    except requests.ConnectionError:
        sys.exit(f"서버에 연결할 수 없습니다: {args.server}")


if __name__ == "__main__":
    main()
