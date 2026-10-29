import json
import re

from backend import devtool, main

MATCH_BODY = {
    "teams": {"A": {"name": "A팀", "color": "#f97316"}, "B": {"name": "B팀", "color": "#3b82f6"}},
    "players": {"A": ["호랑이", "번개"], "B": ["독수리", "여우"]},
    "half_minutes": 15,
}


class Recorder:
    """Fake WebSocket connection that keeps every message the server sends."""

    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


def start(client):
    assert client.post("/api/match", json=MATCH_BODY).status_code == 200
    assert client.post("/api/match/kickoff").status_code == 200


def signal(client, side):
    return client.post("/api/events/goal", json={"goal_side": side, "ts": 12.4, "conf": 0.91, "source": "live"}).json()


def state(client):
    return client.get("/api/match").json()


def test_goal_signal_and_cooldown(client, clock):
    start(client)
    # At kickoff A defends left, so a left goal is B's.
    assert signal(client, "left") == {"accepted": True, "goal_id": 1}
    m = state(client)
    assert m["score"] == {"A": 0, "B": 1}
    assert (m["goals"][0]["team"], m["goals"][0]["situation"]) == ("B", "선제골")

    clock.advance(5)
    assert signal(client, "left") == {"accepted": False, "reason": "cooldown"}
    assert signal(client, "right") == {"accepted": True, "goal_id": 2}
    m = state(client)
    assert m["score"] == {"A": 1, "B": 1}
    assert m["goals"][1]["situation"] == "동점골"

    clock.advance(5.1)  # 10.1 s after the first left signal
    assert signal(client, "left")["accepted"] is True
    assert state(client)["goals"][2]["situation"] == "앞서가는골"


def test_not_in_play(client):
    assert client.post("/api/match", json=MATCH_BODY).status_code == 200
    assert signal(client, "left") == {"accepted": False, "reason": "not_in_play"}
    client.post("/api/match/kickoff")
    client.post("/api/match/halftime", json={"swap": False})
    assert signal(client, "left") == {"accepted": False, "reason": "not_in_play"}
    assert state(client)["goals"] == []


def test_cancel_window(client, clock):
    start(client)
    goal_id = signal(client, "left")["goal_id"]
    clock.advance(9)
    r = client.post(f"/api/goals/{goal_id}/cancel")
    assert r.status_code == 200
    assert r.json()["score"] == {"A": 0, "B": 0}
    assert r.json()["goals"][0]["cancelled"] is True

    goal_id = client.post("/api/goals/manual", json={"team": "A"}).json()["goal_id"]
    clock.advance(11)
    assert client.post(f"/api/goals/{goal_id}/cancel").status_code == 409
    assert state(client)["score"] == {"A": 1, "B": 0}
    assert client.post("/api/goals/99/cancel").status_code == 404


def test_scorer_must_be_on_scoring_team(client):
    start(client)
    goal_id = signal(client, "left")["goal_id"]  # B's goal
    assert client.post(f"/api/goals/{goal_id}/scorer", json={"nickname": "호랑이"}).status_code == 400
    r = client.post(f"/api/goals/{goal_id}/scorer", json={"nickname": "독수리"})
    assert r.status_code == 200
    assert r.json()["goals"][0]["scorer"] == "독수리"


def test_halftime_swap_changes_left_goal_team(client, clock):
    start(client)
    signal(client, "left")
    assert state(client)["goals"][0]["team"] == "B"
    clock.advance(11)
    r = client.post("/api/match/halftime", json={"swap": True})
    assert r.json()["sides"] == {"left": "B", "right": "A"}
    client.post("/api/match/second-half")
    signal(client, "left")
    assert state(client)["goals"][1]["team"] == "A"


def test_demo_scenario(client, clock, tmp_path):
    recorder = Recorder()
    main.CONNECTIONS.add(recorder)
    names = devtool.load_names(tmp_path / "missing.json")  # no file: default names
    devtool.demo(devtool.Api("http://testserver", client), names, sleep=clock.advance)

    m = state(client)
    assert [g["situation"] for g in m["goals"] if not g["cancelled"]] == ["선제골", "동점골", "역전골", "동점골", "역전골"]
    assert m["score"] == {"A": 3, "B": 2}
    assert m["status"] == "ended"
    # A has 4 goals on record but one was cancelled and does not count.
    assert [g["cancelled"] for g in m["goals"] if g["team"] == "A"].count(True) == 1
    assert len([g for g in m["goals"] if g["team"] == "A"]) == 4
    scorer_sets = [msg for msg in recorder.messages if msg["type"] == "scorer_set"]
    assert (scorer_sets[-1]["scorer"], scorer_sets[-1]["situation"]) == ("호랑이", "해트트릭")


def test_end_writes_results(client, clock, tmp_path):
    start(client)
    signal(client, "left")
    client.post("/api/goals/1/scorer", json={"nickname": "독수리"})
    client.post("/api/goals/manual", json={"team": "A"})
    client.post("/api/goals/2/cancel")
    r = client.post("/api/match/end")
    assert r.status_code == 200 and r.json()["status"] == "ended"

    files = list((tmp_path / "results").glob("*.json"))
    assert len(files) == 1
    assert re.fullmatch(r"match_\d{8}_\d{6}\.json", files[0].name)
    text = files[0].read_text(encoding="utf-8")
    assert "독수리" in text  # written as UTF-8, not \u escapes
    data = json.loads(text)
    assert data["teams"] == MATCH_BODY["teams"]
    assert data["players"] == MATCH_BODY["players"]
    assert data["score"] == {"A": 0, "B": 1}
    live, manual = data["goals"]
    assert (live["ts"], live["conf"], live["source"], live["scorer"]) == (12.4, 0.91, "live", "독수리")
    assert live["received_at"] == clock.t and live["cancelled_at"] is None
    assert (manual["source"], manual["cancelled"], manual["cancelled_at"]) == ("manual", True, clock.t)


def test_websocket_state_then_goal(client):
    start(client)
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "state" and first["match"]["status"] == "first_half"
        signal(client, "left")
        goal = ws.receive_json()
        assert goal["type"] == "goal"
        assert (goal["goal_id"], goal["team"], goal["situation"]) == (1, "B", "선제골")
        assert goal["audio"] == "/audio/team_B팀_선제골.mp3"
        after = ws.receive_json()
        assert after["type"] == "state" and after["match"]["score"] == {"A": 0, "B": 1}
