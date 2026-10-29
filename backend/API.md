# 스타디움 연출 백엔드 API 계약 (v2.2, 10/29)

- 서버: 통합 노트북 `http://<노트북IP>:8000` (같은 노트북에서는 `http://localhost:8000`)
- 이 문서는 이(백엔드)가 관리합니다. 바꿀 때는 오·최에게 먼저 알립니다.

## v2.2에서 바뀐 점 (10/29 노골 카운트다운)

- `goal` 메시지에 `server_now`(보내는 순간의 서버 시각, Unix ms)가 추가됐습니다. 폰과 노트북 시계가 어긋나도 화면이 이 값으로 보정해 노골 정정 카운트다운을 셉니다.
- 노골 정정(`POST /api/goals/{goal_id}/cancel`)은 `cancel_deadline` 뒤 1초까지 받습니다(네트워크 지연 여유). `cancel_deadline`, `cancel_until` 값은 그대로 골 후 10초입니다.

## v2.1에서 바뀐 점 (10/29 리허설 점검 뒤)

- 골 신호의 `source`를 제한하지 않습니다. 문자열이면 그대로 기록하고, 없거나 `null`이면 `"live"`입니다.
- 골 기록(`state`·`match_end`의 `goals`, 결과 JSON)에 `sit2`가 추가됐습니다. 같은 팀·같은 선수의 유효한 골 중 시간 순 3번째 골만 `"해트트릭"`이고 나머지는 `null`입니다.
- `clock.elapsed`는 하프타임과 종료 때 0이 아니라 멈춘 순간의 경과 초를 유지합니다.

## v1에서 바뀐 점

- `state`를 감싸지 않고 펼칩니다(`"match"` 키 없음). 경기 상태에 `phase`, `half`, `clock`, `swapped`, `teams.*.players`가 추가됐습니다.
- `half`는 경기 상태와 골 기록 모두 숫자 `1`, `2`입니다. 골 기록에 `time`, `nick`이 추가됐습니다.
- `goal`, `goal_cancelled`, `scorer_set`, `match_end`에 필드가 추가됐습니다(아래 각 메시지 참고).
- `audio`, `sfx`는 경로가 아니라 파일 이름만 보냅니다. 함성 파일은 `cheer.mp3`입니다.
- `POST /api/match`가 팀 배열 형식도 받습니다. 득점자 지정은 `nick`, `player_index`도 받습니다. 하프타임은 여러 번 보내도 됩니다.
- 역전골/앞서가는골 판단 기준이 바뀌었습니다(아래 "상황 이름").
- 골 신호 `POST /api/events/goal`은 바뀌지 않았습니다.

## 화면을 그리는 규칙 (오)

1. 화면의 숫자, 팀 이름, 골 목록은 항상 `state` 메시지로 다시 그립니다.
2. 소리와 번쩍임 같은 연출은 `goal`, `scorer_set`, `goal_cancelled`, `period`, `match_end` 메시지로 냅니다. 이 메시지로 점수를 직접 더하거나 빼지 않습니다. 서버가 바로 뒤에 `state`를 보냅니다.
3. 소리 파일은 서버가 `audio`, `sfx` 필드에 파일 이름만 보냅니다. 화면은 `"/audio/" + encodeURIComponent(audio)`로 요청합니다. 파일 이름을 직접 조합하지 않습니다.
4. 소리 재생 순서는 `sfx`(있으면) → `audio`입니다. `audio` 재생이 실패하면(파일 없음 등) `text`를 브라우저 speechSynthesis(`lang = "ko-KR"`)로 읽습니다.
5. 브라우저는 사용자가 한 번 클릭하기 전에는 소리를 내지 않으므로 "소리 켜기" 버튼이 필요합니다.

## WebSocket: `ws://<서버>/ws`

연결하면 즉시 `state`가 한 번 옵니다. 끊기면 2초 뒤 다시 연결합니다. 클라이언트가 보낼 메시지는 없습니다. 조작은 전부 아래 REST로 합니다.

### state

`type` 옆에 아래 '경기 상태'의 필드가 그대로 펼쳐져 옵니다.

```json
{"type": "state", "status": "second_half", "phase": "second_half", "half": 2, "...": "아래 '경기 상태'의 나머지 필드"}
```

### goal (골 직후 1차 해설)

```json
{
  "type": "goal",
  "goal_id": 3,
  "team": "B",
  "situation": "역전골",
  "audio": "team_B팀_역전골.mp3",
  "sfx": "cheer.mp3",
  "text": "B팀! 드디어 경기를 뒤집습니다! 역전골!!",
  "cancel_deadline": 1793241010000,
  "cancel_until": 1793241010000,
  "server_now": 1793241000000,
  "score": {"A": 1, "B": 2},
  "time": "07:41",
  "half": 2
}
```

`cancel_until`(Unix ms, `cancel_deadline`과 같은 값)까지 주최자 폰에 "노골" 버튼과 카운트다운을 보여 줍니다. `cancel_until`은 서버 시계 기준이므로, 폰은 자기 시계(`Date.now()`)와 바로 비교하지 말고 `server_now`(이 메시지를 보낸 순간의 서버 시각)로 보정합니다. 예: 남은 시간 = `cancel_until - server_now`(골 직후라 10초) − 메시지를 받은 뒤 지난 시간. `score`는 이 골이 들어간 뒤의 점수, `time`은 그 반이 시작된 뒤 지난 시간입니다.

### scorer_set (득점자 탭 후 2차 해설)

```json
{
  "type": "scorer_set",
  "goal_id": 3,
  "team": "B",
  "scorer": "독수리",
  "nick": "독수리",
  "situation": "역전골",
  "goal_situation": "역전골",
  "player_goals": 1,
  "audio": "player_독수리_역전골.mp3",
  "text": "독수리!!! 역전골입니다!!!"
}
```

`player_goals`는 그 선수의 유효한(취소 안 된) 골 수입니다. 같은 선수의 3번째 골이면 `situation`이 `"해트트릭"`이고, `goal_situation`에는 그 골의 원래 상황이 남습니다. 득점 기록에 남는 해트트릭 표시는 바로 뒤에 오는 `state`의 골 기록 `sit2`를 씁니다.

### goal_cancelled (노골 정정)

```json
{"type": "goal_cancelled", "goal_id": 3, "team": "B", "score": {"A": 1, "B": 1}, "audio": "fixed_no_goal.mp3", "text": "판독 결과, 노골입니다!"}
```

`score`는 취소한 뒤의 점수입니다.

### period (킥오프, 하프타임, 후반 시작)

```json
{"type": "period", "status": "halftime", "audio": "fixed_halftime.mp3", "text": "전반전 종료!"}
```

`status`는 `first_half`(킥오프), `halftime`, `second_half` 중 하나입니다.

### match_end

```json
{"type": "match_end", "score": {"A": 3, "B": 2}, "winner": "A", "goals": ["...취소된 골을 포함한 전체 골 기록"], "audio": "fixed_match_end.mp3", "text": "경기 종료!"}
```

`winner`는 `"A"`, `"B"`, 무승부면 `null`입니다. `goals`는 경기 상태의 `goals`와 같은 형식입니다.

## 경기 상태 (`state`, `GET /api/match`)

`GET /api/match`는 아래 필드를 그대로 돌려줍니다(`type` 없음).

```json
{
  "status": "second_half",
  "phase": "second_half",
  "half": 2,
  "half_minutes": 15,
  "period_started_at": 1793240000000,
  "server_now": 1793240123456,
  "clock": {"running": true, "elapsed": 123},
  "teams": {"A": {"name": "A팀", "color": "#f97316", "players": ["호랑이", "번개"]},
            "B": {"name": "B팀", "color": "#3b82f6", "players": ["독수리", "여우"]}},
  "players": {"A": ["호랑이", "번개"], "B": ["독수리", "여우"]},
  "score": {"A": 2, "B": 1},
  "sides": {"left": "B", "right": "A"},
  "swapped": true,
  "audio_ready": true,
  "goals": [
    {"goal_id": 1, "team": "A", "scorer": "호랑이", "nick": "호랑이", "situation": "선제골", "sit2": null, "half": 1,
     "clock": "03:12", "time": "03:12", "source": "live", "cancelled": false, "cancel_deadline": 1793239202000}
  ]
}
```

- `status`: `setup` → `first_half` → `halftime` → `second_half` → `ended`. `phase`는 `status`와 같은 값입니다.
- `half`: 지금(또는 마지막으로 진행한) 반. 후반이 시작되면 `2`가 됩니다.
- 타이머: `clock.running`은 전반·후반 진행 중에만 `true`이고, `clock.elapsed`는 그 반이 시작된 뒤 지난 정수 초입니다. 받은 순간의 경과 시간은 `server_now - period_started_at`과 같고, 화면에서 1초씩 올립니다. 하프타임과 종료 때는 `running`이 `false`이고 `elapsed`는 멈춘 순간의 값을 유지합니다(하프타임에 바로 종료하면 전반이 멈춘 값). `setup`일 때만 `0`이고, 후반이 시작되면 `0`부터 다시 셉니다. 경기 중이 아니면 `period_started_at`은 `null`입니다.
- `teams.A.players`, `teams.B.players`는 최상위 `players`와 같은 목록입니다.
- `sides`: 왼쪽·오른쪽 골대를 **지키는** 팀입니다(점수판 화면 기준). 킥오프 때는 `left: A`, `right: B`이고, 하프타임에 진영을 바꾸면 뒤집힙니다. `swapped`는 진영을 바꿨으면 `true`입니다.
- `goals`: 취소된 골도 `cancelled: true`로 남습니다(결과 화면의 "노골 정정" 표시용). `scorer`(= `nick`)는 지정 전이면 `null`입니다. `time`은 `clock`과 같은 `"MM:SS"`입니다. `sit2`는 같은 팀·같은 선수의 유효한 골 중 시간 순 3번째 골에만 `"해트트릭"`이고 나머지는 `null`입니다. 득점자를 바꾸거나 골을 취소하면 모든 골의 `sit2`를 다시 계산합니다.
- `audio_ready`: 지금 이름으로 필요한 음성 파일이 다 만들어졌으면 `true`입니다(설정 화면의 "음성 준비 중" 표시용).

## REST

에러는 FastAPI 기본 형식 `{"detail": "..."}`입니다. 400은 잘못된 값, 404는 없는 골, 409는 지금 할 수 없는 동작입니다.

- `POST /api/match` — 새 경기로 초기화하고 음성 생성을 시작합니다(없는 파일만 백그라운드에서 만들고, 끝나면 `state`를 다시 보냅니다). 응답은 경기 상태입니다. 본문은 두 형식 중 하나입니다.
  - 팀 배열(오의 화면): `{"teams": [{"name": "A팀", "color": "#f97316", "players": ["호랑이", "번개"]}, {"name": "B팀", "color": "#3b82f6", "players": ["독수리", "여우"]}], "half_minutes": 15, "swap_at_half": true}`. 배열 0번이 A, 1번이 B입니다.
  - v1 형식: `{"teams": {"A": {"name": "A팀", "color": "#f97316"}, "B": {"name": "B팀", "color": "#3b82f6"}}, "players": {"A": ["호랑이", "번개"], "B": ["독수리", "여우"]}, "half_minutes": 15}`
  - 선수 이름은 앞뒤 공백을 지우고, 빈 이름과 같은 팀 안의 중복은 뺍니다. `swap_at_half`는 저장만 하며, 하프타임에 보내는 `swap`이 우선합니다.
- `POST /api/match/kickoff` — 본문 없음. `setup`에서만 됩니다.
- `POST /api/match/halftime` — 본문 `{"swap": true}`(없으면 `true`로 봅니다). `first_half`에서 보내면 하프타임으로 들어가고 `period` 메시지와 전반 종료 음성이 나갑니다. `halftime`에서 다시 보내면 진영만 다시 정합니다(메시지는 `state`만). 진영은 항상 킥오프 진영을 기준으로 `swap: true`면 뒤집은 상태, `false`면 그대로입니다. 같은 요청을 여러 번 보내도 여러 번 뒤집히지 않습니다.
- `POST /api/match/second-half` — 본문 없음. `halftime`에서만 됩니다.
- `POST /api/match/end` — 본문 없음. 경기 중이나 하프타임에 됩니다. 결과를 `eval/results/`에 JSON으로 저장합니다(골 기록에 `sit2` 포함).
- `POST /api/events/goal` — 감지 스크립트가 보내는 골 신호입니다.
  본문: `{"goal_side": "left", "ts": 12.4, "conf": 0.91, "source": "live"}`
  응답(항상 200): `{"accepted": true, "goal_id": 3}` 또는 `{"accepted": false, "reason": "cooldown"}` 또는 `{"accepted": false, "reason": "not_in_play"}`
- `POST /api/goals/{goal_id}/cancel` — 본문 없음. `cancel_deadline` 뒤 1초까지는 받고(네트워크 지연 여유), 그보다 늦으면 409입니다.
- `POST /api/goals/{goal_id}/scorer` — 본문 `{"nickname": "호랑이"}`, `{"nick": "호랑이"}`, `{"player_index": 0}` 중 하나. 여러 개가 오면 `nickname` → `nick` → `player_index` 순으로 씁니다. `player_index`는 득점 팀 `players` 배열의 위치입니다. 득점 팀 선수가 아니면 400입니다. 다시 보내면 득점자가 바뀝니다.
- `POST /api/goals/manual` — 본문 `{"team": "A"}`. 감지가 놓친 골을 수동으로 넣습니다. 쿨다운이 없고 `source`는 `"manual"`입니다.
- `GET /api/match` — 경기 상태입니다. 새로고침이나 재접속 때 씁니다.

## 골 신호 (최)

- `goal_side`: `"left"` 또는 `"right"`. 점수판 화면 기준 골대입니다. 득점 팀은 그 골대를 지키는 팀의 상대입니다.
- `ts`: 영상 시작부터의 초. `conf`: 공 검출 신뢰도. `source`: 신호 출처 이름(`"live"`, `"replay"`, 테스트 버튼의 `"test"` 등). 문자열이면 무엇이든 받아 골 기록에 그대로 남기고, 없거나 `null`이면 `"live"`입니다.
- 같은 골대에서 10초 안에 다시 온 신호는 서버가 무시합니다(`reason: "cooldown"`). 여러 번 보내도 한 골로 처리됩니다.

## 상황 이름 (`situation`)

선제골, 동점골, 역전골, 앞서가는골, 추격골, 달아나는골, 해트트릭(2차 해설에서만)

- 한 골 차로 앞서게 되는 골(선제골 제외)은, 바로 앞의 유효한 골을 같은 팀이 넣었으면(뒤지다가 동점을 만든 뒤 앞섬) 역전골, 상대 팀이 넣었으면(상대가 따라붙은 동점을 다시 깸) 앞서가는골입니다. 취소된 골은 따지지 않습니다.
