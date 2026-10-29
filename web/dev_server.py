"""이(백엔드)의 서버가 나오기 전에 쓰는 대역 서버. 시연용이 아니라 화면 테스트용입니다.

README의 "이(백엔드)에게" 형식 그대로 동작합니다. 노트북 점수판 + 진짜 폰 주최자 화면을 혼자 연동해 볼 수 있습니다.

  pip install aiohttp            (이미 깔려 있으면 생략)
  python dev_server.py           (web 폴더 안에서)
  → 노트북 브라우저: http://localhost:8000/        (점수판)
  → 폰 크롬:         http://<노트북IP>:8000/host   (주최자, 같은 와이파이·핫스팟)
  → 골 신호 테스트:  python dev_server.py --goal left   (다른 터미널에서, 서버로 골 신호 1개 보냄)

음성: 저장소의 audio 폴더(없으면 web/audio)에 있는 mp3를 /audio/ 로 제공합니다. 없으면 화면이 브라우저 음성으로 대신 읽습니다.
"""
import argparse
import asyncio
import json
import os
import socket
import sys
import time

from aiohttp import ClientSession, WSMsgType, web

HERE = os.path.dirname(os.path.abspath(__file__))
IDS = ["A", "B"]
TEAM_LINES = {
    "선제골": "{t} 선제골입니다!!", "동점골": "{t} 동점골! 경기를 원점으로 돌립니다!",
    "역전골": "{t} 역전골! 드디어 경기를 뒤집습니다!!", "앞서가는 골": "{t} 골! 다시 앞서 나갑니다!",
    "추격골": "{t} 추격골! 한 골 따라붙습니다!", "달아나는 골": "{t} 골! 점수 차를 벌립니다!",
}
NICK_LINES = {
    "선제골": "{n}!!! 선제골입니다!!!", "동점골": "{n}!!! 동점골을 터뜨립니다!!!", "역전골": "{n}!!! 역전골입니다!!!",
    "앞서가는 골": "{n}!!! 다시 앞서가는 골!!!", "추격골": "{n}!!! 추격의 불씨를 살립니다!!!",
    "달아나는 골": "{n}!!! 쐐기를 박습니다!!!", "해트트릭": "{n}!!! 해트트릭입니다!!!",
}


def mmss(sec):
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


class Match:
    def __init__(self, teams=None, half_minutes=15):
        self.teams = teams or [{"name": "오렌지팀", "color": "#f97316", "players": ["번개", "캡틴"]},
                               {"name": "블루팀", "color": "#3b82f6", "players": ["탱크", "에이스"]}]
        self.half_minutes = half_minutes
        self.phase, self.half, self.swapped = "setup", 1, False
        self.score, self.goals, self.seq = [0, 0], [], 0
        self.running, self.base, self.since = False, 0.0, 0.0

    def elapsed(self):
        return self.base + (time.time() - self.since if self.running else 0)

    def start_clock(self):
        self.running, self.base, self.since = True, 0.0, time.time()

    def stop_clock(self):
        self.base, self.running = self.elapsed(), False

    def state(self):
        return {"type": "state", "phase": self.phase, "half": self.half, "half_minutes": self.half_minutes,
                "swapped": self.swapped, "score": list(self.score), "teams": self.teams, "goals": self.goals,
                "clock": {"running": self.running, "elapsed": int(self.elapsed())}}

    def situation(self, team):  # 동점 직전에 앞서 있던 팀 기준으로 역전/앞서가는 골 구분
        i, sc, last_leader = IDS.index(team), [0, 0], None
        for g in self.goals:
            if g["cancelled"]:
                continue
            sc[IDS.index(g["team"])] += 1
            if sc[0] != sc[1]:
                last_leader = 0 if sc[0] > sc[1] else 1
        mine, theirs = sc[i], sc[1 - i]
        if mine == theirs == 0:
            return "선제골"
        if mine + 1 == theirs:
            return "동점골"
        if mine + 1 < theirs:
            return "추격골"
        if mine > theirs:
            return "달아나는 골"
        return "역전골" if last_leader == 1 - i else "앞서가는 골"

    def add_goal(self, team):
        if self.phase not in ("first_half", "second_half"):
            raise ValueError("경기 중이 아닙니다 (주최자 폰에서 킥오프 먼저)")
        sit, i = self.situation(team), IDS.index(team)
        self.seq += 1
        g = {"goal_id": self.seq, "team": team, "half": self.half, "time": mmss(self.elapsed()),
             "situation": sit, "nick": None, "cancelled": False, "cancel_until": time.time() + 10}
        self.goals.append(g)
        self.score[i] += 1
        return {"type": "goal", "goal_id": g["goal_id"], "team": team, "score": list(self.score), "situation": sit,
                "audio": f"{team}_{sit}.mp3", "text": TEAM_LINES[sit].format(t=self.teams[i]["name"]),
                "half": self.half, "time": g["time"], "cancel_until": g["cancel_until"]}

    def find(self, gid):
        for g in self.goals:
            if g["goal_id"] == gid:
                return g
        raise ValueError(f"골 번호 {gid}이(가) 없습니다")


M = Match()
CLIENTS = set()


async def broadcast(msg):
    data = json.dumps(msg, ensure_ascii=False)
    for ws in list(CLIENTS):
        try:
            await ws.send_str(data)
        except Exception:
            CLIENTS.discard(ws)


def ok(data=None):
    return web.json_response(data or {"ok": True})


def fail(msg, code=400):
    return web.json_response({"error": msg}, status=code, dumps=lambda o: json.dumps(o, ensure_ascii=False))


async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    CLIENTS.add(ws)
    await ws.send_str(json.dumps(M.state(), ensure_ascii=False))
    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
    finally:
        CLIENTS.discard(ws)
    return ws


async def body(request):
    try:
        return await request.json()
    except Exception:
        return {}


async def get_match(request):
    st = M.state()
    st.pop("type")
    return web.json_response(st, dumps=lambda o: json.dumps(o, ensure_ascii=False))


async def create_match(request):
    global M
    b = await body(request)
    M = Match(b.get("teams"), b.get("half_minutes", 15))
    await broadcast(M.state())
    return ok()


async def kickoff(request):
    M.phase, M.half = "first_half", 1
    M.start_clock()
    await broadcast(M.state())
    return ok()


async def halftime(request):
    b = await body(request)
    if M.phase != "halftime":
        M.stop_clock()
    M.phase, M.swapped = "halftime", b.get("swap", True) is not False
    await broadcast(M.state())
    return ok()


async def second_half(request):
    M.phase, M.half = "second_half", 2
    M.start_clock()
    await broadcast(M.state())
    return ok()


async def end(request):
    M.stop_clock()
    M.phase = "ended"
    await broadcast({"type": "match_end", "score": list(M.score), "goals": M.goals, "audio": "fixed_match_end.mp3",
                     "text": f"최종 스코어 {M.score[0]} 대 {M.score[1]}, 경기 종료입니다!"})
    await broadcast(M.state())
    return ok()


async def goal_signal(request):  # 감지 스크립트(최) / live.py 가 보내는 골 신호
    b = await body(request)
    left_is_a = not M.swapped                 # 전반: 왼쪽 골대 = A팀 골대 (진영 교대하면 반대)
    defending = "A" if (b.get("goal_side") == "left") == left_is_a else "B"
    try:
        out = M.add_goal("B" if defending == "A" else "A")
    except ValueError as e:
        return fail(str(e))
    print(f"[골 신호] {b} → {out['team']}팀 {out['situation']}")
    await broadcast(out)
    return ok({"goal_id": out["goal_id"]})


async def manual_goal(request):
    b = await body(request)
    try:
        out = M.add_goal(str(b.get("team", "A")).upper())
    except ValueError as e:
        return fail(str(e))
    await broadcast(out)
    return ok({"goal_id": out["goal_id"]})


async def cancel(request):
    try:
        g = M.find(int(request.match_info["id"]))
    except ValueError as e:
        return fail(str(e), 404)
    if g["cancelled"]:
        return ok()
    if time.time() > g["cancel_until"] + 0.5:
        return fail("10초가 지나 노골 정정을 할 수 없습니다")
    g["cancelled"] = True
    M.score[IDS.index(g["team"])] -= 1
    await broadcast({"type": "goal_cancelled", "goal_id": g["goal_id"], "team": g["team"], "score": list(M.score),
                     "audio": "fixed_no_goal.mp3", "text": "판독 결과, 노골입니다!"})
    return ok()


async def scorer(request):
    b = await body(request)
    try:
        g = M.find(int(request.match_info["id"]))
    except ValueError as e:
        return fail(str(e), 404)
    if g["cancelled"]:
        return fail("노골 처리된 골입니다")
    players = M.teams[IDS.index(g["team"])].get("players", [])
    idx = b.get("player_index")
    nick = b.get("nick") or (players[idx] if isinstance(idx, int) and 0 <= idx < len(players) else "")
    g["nick"] = nick
    n = sum(1 for x in M.goals if not x["cancelled"] and x["team"] == g["team"] and x["nick"] == nick)
    sit = "해트트릭" if n == 3 else g["situation"]
    await broadcast({"type": "scorer_set", "goal_id": g["goal_id"], "team": g["team"], "nick": nick,
                     "player_goals": n, "situation": sit, "goal_situation": g["situation"],
                     "audio": f"{nick}_{sit}.mp3", "text": NICK_LINES[sit].format(n=nick)})
    return ok()


@web.middleware
async def cors(request, handler):  # 다른 주소(예: python -m http.server)에서 연 화면도 붙을 수 있게
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


async def send_goal(port, side):
    async with ClientSession() as s:
        async with s.post(f"http://localhost:{port}/api/events/goal",
                          json={"goal_side": side, "ts": 0, "conf": 1, "source": "test"}) as r:
            print(r.status, await r.text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--goal", choices=["left", "right"], help="서버를 켜지 않고, 켜져 있는 서버로 골 신호만 1개 보냄")
    args = ap.parse_args()
    if args.goal:
        asyncio.run(send_goal(args.port, args.goal))
        return

    app = web.Application(middlewares=[cors])
    app.router.add_get("/", lambda r: web.FileResponse(os.path.join(HERE, "index.html")))
    app.router.add_get("/host", lambda r: web.FileResponse(os.path.join(HERE, "host.html")))
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/match", get_match)
    app.router.add_post("/api/match", create_match)
    app.router.add_post("/api/match/kickoff", kickoff)
    app.router.add_post("/api/match/halftime", halftime)
    app.router.add_post("/api/match/second-half", second_half)
    app.router.add_post("/api/match/end", end)
    app.router.add_post("/api/events/goal", goal_signal)
    app.router.add_post("/api/goals/manual", manual_goal)
    app.router.add_post("/api/goals/{id}/cancel", cancel)
    app.router.add_post("/api/goals/{id}/scorer", scorer)
    audio = next((p for p in (os.path.join(HERE, "..", "audio"), os.path.join(HERE, "audio")) if os.path.isdir(p)), None)
    if audio:
        app.router.add_static("/audio/", audio)

    ip = local_ip()
    print("대역 서버 (화면 테스트용)")
    print(f"  점수판  : http://localhost:{args.port}/")
    print(f"  주최자  : http://{ip}:{args.port}/host   ← 폰 크롬 (같은 와이파이·핫스팟)")
    print(f"  골 신호 : python dev_server.py --goal left   (다른 터미널)")
    print(f"  음성    : {os.path.abspath(audio) if audio else '없음 → 브라우저 음성으로 대체'}")
    web.run_app(app, host="0.0.0.0", port=args.port, print=None)


if __name__ == "__main__":
    sys.exit(main())
