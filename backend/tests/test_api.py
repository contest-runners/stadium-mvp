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

    def of(self, kind):
        return [m for m in self.messages if m["type"] == kind]


def record():
    recorder = Recorder()
    main.CONNECTIONS.add(recorder)
    return recorder


def start(client):
    assert client.post("/api/match", json=MATCH_BODY).status_code == 200
    assert client.post("/api/match/kickoff").status_code == 200


def signal(client, side):
    return client.post("/api/events/goal", json={"goal_side": side, "ts": 12.4, "conf": 0.91, "source": "live"}).json()


def manual(client, team):
    return client.post("/api/goals/manual", json={"team": team}).json()["goal_id"]


def state(client):
    return client.get("/api/match").json()


def run_demo(client, clock, tmp_path):
    names = devtool.load_names(tmp_path / "missing.json")  # no file: default names
    devtool.demo(devtool.Api("http://testserver", client), names, sleep=clock.advance)


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


def test_goal_signal_records_any_source(client, clock):
    start(client)
    body = {"goal_side": "left", "ts": 1.0, "conf": 1}
    assert client.post("/api/events/goal", json={**body, "source": "test"}).json()["accepted"] is True
    clock.advance(11)
    client.post("/api/events/goal", json=body)  # no source
    clock.advance(11)
    client.post("/api/events/goal", json={**body, "source": None})
    clock.advance(11)
    client.post("/api/events/goal", json={**body, "source": "replay"})
    assert [g["source"] for g in state(client)["goals"]] == ["test", "live", "live", "replay"]
    assert client.post("/api/events/goal", json={**body, "source": 1}).status_code == 400


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

    goal_id = manual(client, "A")
    clock.advance(12)  # past the 10 s window and the 1 s grace
    assert client.post(f"/api/goals/{goal_id}/cancel").status_code == 409
    assert state(client)["score"] == {"A": 1, "B": 0}
    assert client.post("/api/goals/99/cancel").status_code == 404


def test_cancel_grace_after_deadline(client, clock):
    start(client)
    goal_id = manual(client, "A")
    clock.advance(10.5)  # 0.5 s after cancel_deadline
    assert client.post(f"/api/goals/{goal_id}/cancel").status_code == 200

    goal_id = manual(client, "B")
    clock.advance(11.5)  # 1.5 s after cancel_deadline
    assert client.post(f"/api/goals/{goal_id}/cancel").status_code == 409
    assert state(client)["score"] == {"A": 0, "B": 1}


def test_goal_message_has_server_now(client, clock):
    recorder = record()
    start(client)
    clock.advance(30)
    manual(client, "A")
    goal = recorder.of("goal")[0]
    assert goal["server_now"] == clock.t
    # cancel_deadline and cancel_until stay 10 s after the goal.
    assert goal["cancel_until"] - goal["server_now"] == goal["cancel_deadline"] - goal["server_now"] == 10_000


def test_situation_ignores_cancelled_goal(client):
    start(client)
    manual(client, "B")  # 0:1
    manual(client, "A")  # 1:1
    cancelled = manual(client, "B")  # 1:2, then cancelled
    client.post(f"/api/goals/{cancelled}/cancel")
    manual(client, "A")  # 2:1; the last valid goal before it is A's equaliser
    assert state(client)["goals"][-1]["situation"] == "역전골"


def test_scorer_must_be_on_scoring_team(client):
    start(client)
    goal_id = signal(client, "left")["goal_id"]  # B's goal
    assert client.post(f"/api/goals/{goal_id}/scorer", json={"nickname": "호랑이"}).status_code == 400
    r = client.post(f"/api/goals/{goal_id}/scorer", json={"nickname": "독수리"})
    assert r.status_code == 200
    assert r.json()["goals"][0]["scorer"] == "독수리"


def test_scorer_by_nick_and_player_index(client):
    start(client)
    goal_id = signal(client, "left")["goal_id"]  # B: 독수리, 여우
    url = f"/api/goals/{goal_id}/scorer"

    def scorer(body):
        r = client.post(url, json=body)
        return r.json()["goals"][0]["nick"] if r.status_code == 200 else r.status_code

    assert scorer({"player_index": 1}) == "여우"
    assert scorer({"nick": "독수리"}) == "독수리"
    assert scorer({"nick": "여우", "player_index": 0}) == "여우"  # nick before player_index
    assert scorer({"nickname": "독수리", "nick": "여우"}) == "독수리"  # nickname first
    assert scorer({"player_index": 2}) == 400
    assert scorer({}) == 400


def test_halftime_swap_changes_left_goal_team(client, clock):
    start(client)
    signal(client, "left")
    assert state(client)["goals"][0]["team"] == "B"
    clock.advance(11)
    r = client.post("/api/match/halftime", json={"swap": True})
    assert r.json()["sides"] == {"left": "B", "right": "A"}
    client.post("/api/match/second-half")
    signal(client, "left")
    m = state(client)
    assert m["goals"][1]["team"] == "A"
    assert (m["half"], m["goals"][0]["half"], m["goals"][1]["half"]) == (2, 1, 2)


def test_halftime_twice_true_flips_once(client):
    recorder = record()
    start(client)
    assert client.post("/api/match/halftime", json={"swap": True}).status_code == 200
    r = client.post("/api/match/halftime", json={"swap": True})
    assert r.status_code == 200
    assert r.json()["sides"] == {"left": "B", "right": "A"}
    assert r.json()["swapped"] is True
    assert len([m for m in recorder.of("period") if m["status"] == "halftime"]) == 1


def test_halftime_true_then_false_keeps_first_half_sides(client):
    start(client)
    client.post("/api/match/halftime", json={"swap": True})
    r = client.post("/api/match/halftime", json={"swap": False})
    assert r.json()["sides"] == {"left": "A", "right": "B"}
    assert r.json()["swapped"] is False
    assert r.json()["status"] == "halftime"


def test_halftime_without_swap_means_swap(client):
    start(client)
    r = client.post("/api/match/halftime")
    assert r.json()["swapped"] is True
    client.post("/api/match/second-half")
    assert client.post("/api/match/halftime", json={"swap": True}).status_code == 409


def test_new_match_accepts_team_array(client):
    body = {
        "teams": [
            {"name": " 먼작귀 ", "color": "#f00", "players": [" 우사기 ", "치이", "", "   ", "우사기"]},
            {"name": "티니핑", "color": "#00f", "players": ["하츄핑", "아자핑"]},
        ],
        "half_minutes": 12,
        "swap_at_half": True,
    }
    r = client.post("/api/match", json=body)
    assert r.status_code == 200
    m = r.json()
    assert m["teams"]["A"] == {"name": "먼작귀", "color": "#f00", "players": ["우사기", "치이"]}
    assert m["teams"]["B"] == {"name": "티니핑", "color": "#00f", "players": ["하츄핑", "아자핑"]}
    assert m["players"] == {"A": ["우사기", "치이"], "B": ["하츄핑", "아자핑"]}
    assert m["half_minutes"] == 12
    assert main.MATCH["swap_at_half"] is True
    # swap_at_half is only stored; the swap sent at halftime decides.
    client.post("/api/match/kickoff")
    assert client.post("/api/match/halftime", json={"swap": False}).json()["swapped"] is False

    one_team = {"teams": [body["teams"][0]], "half_minutes": 12}
    assert client.post("/api/match", json=one_team).status_code == 400


def test_state_is_flat_with_v2_fields(client, clock):
    start(client)
    clock.advance(75)
    m = state(client)
    assert "match" not in m
    assert m["phase"] == m["status"] == "first_half"
    assert m["half"] == 1
    assert m["teams"]["A"] == {"name": "A팀", "color": "#f97316", "players": ["호랑이", "번개"]}
    assert m["players"] == MATCH_BODY["players"]
    assert m["clock"] == {"running": True, "elapsed": 75}
    assert m["swapped"] is False

    goal_id = signal(client, "left")["goal_id"]
    client.post(f"/api/goals/{goal_id}/scorer", json={"nickname": "독수리"})
    goal = state(client)["goals"][0]
    assert (goal["half"], goal["clock"], goal["time"], goal["nick"]) == (1, "01:15", "01:15", "독수리")

    client.post("/api/match/halftime", json={"swap": False})
    assert state(client)["clock"] == {"running": False, "elapsed": 75}


def test_clock_keeps_elapsed_when_stopped(client, clock):
    assert client.post("/api/match", json=MATCH_BODY).status_code == 200
    assert state(client)["clock"] == {"running": False, "elapsed": 0}
    client.post("/api/match/kickoff")
    clock.advance(75)
    client.post("/api/match/halftime", json={"swap": True})
    clock.advance(30)
    client.post("/api/match/halftime", json={"swap": False})  # the side choice re-sent
    assert state(client)["clock"] == {"running": False, "elapsed": 75}

    client.post("/api/match/second-half")
    assert state(client)["clock"] == {"running": True, "elapsed": 0}
    clock.advance(40)
    manual(client, "A")
    assert state(client)["goals"][0]["time"] == "00:40"
    client.post("/api/match/end")
    clock.advance(10)
    assert state(client)["clock"] == {"running": False, "elapsed": 40}

    # Ending from halftime keeps the first half's stopped time; a new match starts from 0.
    client.post("/api/match", json=MATCH_BODY)
    client.post("/api/match/kickoff")
    clock.advance(20)
    client.post("/api/match/halftime")
    client.post("/api/match/end")
    assert state(client)["clock"] == {"running": False, "elapsed": 20}
    client.post("/api/match", json=MATCH_BODY)
    assert state(client)["clock"] == {"running": False, "elapsed": 0}


def test_sit2_marks_third_goal_of_player(client, clock):
    recorder = record()
    start(client)

    def score_by(team, nickname):
        goal_id = manual(client, team)
        client.post(f"/api/goals/{goal_id}/scorer", json={"nickname": nickname})
        return goal_id

    def sit2():
        return [g["sit2"] for g in state(client)["goals"]]

    for _ in range(3):
        score_by("A", "호랑이")
    assert sit2() == [None, None, "해트트릭"]
    # The state sent right after scorer_set already carries it, so the host's log keeps the tag.
    assert recorder.messages[-1]["type"] == "state"
    assert [g["sit2"] for g in recorder.messages[-1]["goals"]] == [None, None, "해트트릭"]

    client.post("/api/goals/2/scorer", json={"nickname": "번개"})  # scorer changed: only 2 left
    assert sit2() == [None, None, None]
    client.post("/api/goals/2/scorer", json={"nickname": "호랑이"})
    assert sit2() == [None, None, "해트트릭"]

    # Scorers named out of order: the third goal in time gets it, not the last one named.
    b_goals = [manual(client, "B") for _ in range(3)]
    for goal_id in reversed(b_goals):
        client.post(f"/api/goals/{goal_id}/scorer", json={"nickname": "독수리"})
    assert sit2() == [None, None, "해트트릭", None, None, "해트트릭"]

    client.post("/api/goals/1/cancel")  # 호랑이 drops to 2 valid goals
    assert sit2() == [None, None, None, None, None, "해트트릭"]
    score_by("A", "호랑이")
    assert sit2() == [None, None, None, None, None, "해트트릭", "해트트릭"]

    client.post("/api/match/end")
    assert [g["sit2"] for g in recorder.of("match_end")[0]["goals"]] == sit2()
    data = json.loads(next(main.RESULTS_DIR.glob("*.json")).read_text(encoding="utf-8"))
    assert [g["sit2"] for g in data["goals"]] == sit2()


def test_goal_message_has_score_and_cancel_until(client, clock):
    recorder = record()
    start(client)
    clock.advance(75)
    signal(client, "left")
    goal = recorder.of("goal")[0]
    assert goal["score"] == {"A": 0, "B": 1}
    assert goal["cancel_until"] == goal["cancel_deadline"] == clock.t + 10_000
    assert (goal["time"], goal["half"]) == ("01:15", 1)
    assert (goal["audio"], goal["sfx"]) == ("team_B팀_선제골.mp3", "cheer.mp3")


def test_cancel_scorer_and_end_messages(client):
    recorder = record()
    start(client)
    goal_id = signal(client, "left")["goal_id"]
    client.post(f"/api/goals/{goal_id}/scorer", json={"nickname": "독수리"})
    scorer_set = recorder.of("scorer_set")[0]
    assert (scorer_set["nick"], scorer_set["player_goals"], scorer_set["goal_situation"]) == ("독수리", 1, "선제골")

    cancelled = manual(client, "A")
    client.post(f"/api/goals/{cancelled}/cancel")
    assert recorder.of("goal_cancelled")[0]["score"] == {"A": 0, "B": 1}

    client.post("/api/match/end")
    goals = recorder.of("match_end")[0]["goals"]
    assert [(g["goal_id"], g["cancelled"]) for g in goals] == [(1, False), (2, True)]
    assert goals[0]["nick"] == "독수리"


def test_demo_scenario(client, clock, tmp_path):
    recorder = record()
    run_demo(client, clock, tmp_path)

    m = state(client)
    assert [g["situation"] for g in m["goals"] if not g["cancelled"]] == ["선제골", "동점골", "역전골", "동점골", "역전골"]
    assert m["score"] == {"A": 3, "B": 2}
    assert m["status"] == "ended"
    # A has 4 goals on record but one was cancelled and does not count.
    assert [g["cancelled"] for g in m["goals"] if g["team"] == "A"].count(True) == 1
    assert len([g for g in m["goals"] if g["team"] == "A"]) == 4
    last = recorder.of("scorer_set")[-1]
    assert (last["scorer"], last["situation"], last["goal_situation"], last["player_goals"]) == (
        "호랑이", "해트트릭", "역전골", 3)


def test_audio_fields_are_file_names(client, clock, tmp_path):
    recorder = record()
    run_demo(client, clock, tmp_path)
    sounds = [(m["type"], m.get("audio"), m.get("sfx")) for m in recorder.messages if m["type"] != "state"]
    assert {kind for kind, _, _ in sounds} == {"period", "goal", "scorer_set", "goal_cancelled", "match_end"}
    for kind, audio, sfx in sounds:
        assert audio.endswith(".mp3") and "/" not in audio, (kind, audio)
        assert sfx in (None, "cheer.mp3"), (kind, sfx)
    assert ("scorer_set", "player_호랑이_해트트릭.mp3", None) in sounds
    assert ("goal_cancelled", "fixed_no_goal.mp3", None) in sounds


def test_end_writes_results(client, clock, tmp_path):
    start(client)
    signal(client, "left")
    client.post("/api/goals/1/scorer", json={"nickname": "독수리"})
    manual(client, "A")
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
    live, hand = data["goals"]
    assert (live["ts"], live["conf"], live["source"], live["scorer"]) == (12.4, 0.91, "live", "독수리")
    assert live["received_at"] == clock.t and live["cancelled_at"] is None
    assert (hand["source"], hand["cancelled"], hand["cancelled_at"]) == ("manual", True, clock.t)


def test_websocket_state_then_goal(client):
    start(client)
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "state" and first["phase"] == "first_half" and "match" not in first
        signal(client, "left")
        goal = ws.receive_json()
        assert goal["type"] == "goal"
        assert (goal["goal_id"], goal["team"], goal["situation"]) == (1, "B", "선제골")
        assert goal["audio"] == "team_B팀_선제골.mp3"
        after = ws.receive_json()
        assert after["type"] == "state" and after["score"] == {"A": 0, "B": 1}
