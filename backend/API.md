# 스타디움 연출 백엔드 API 계약 (v1, 10/29)

- 서버: 통합 노트북 `http://<노트북IP>:8000` (같은 노트북에서는 `http://localhost:8000`)
- 이 문서는 이(백엔드)가 관리합니다. 바꿀 때는 오·최에게 먼저 알립니다.

## 화면을 그리는 규칙 (오)

1. 화면의 숫자, 팀 이름, 골 목록은 항상 `state` 메시지로 다시 그립니다.
2. 소리와 번쩍임 같은 연출은 `goal`, `scorer_set`, `goal_cancelled`, `period`, `match_end` 메시지로 냅니다. 이 메시지로 점수를 직접 더하거나 빼지 않습니다. 서버가 바로 뒤에 `state`를 보냅니다.
3. 소리 파일 경로는 서버가 `audio`, `sfx` 필드에 완성된 경로로 보냅니다. 파일 이름을 직접 조합하지 않습니다.
4. 소리 재생 순서는 `sfx`(있으면) → `audio`입니다. `audio` 재생이 실패하면(파일 없음 등) `text`를 브라우저 speechSynthesis(`lang = "ko-KR"`)로 읽습니다.
5. 브라우저는 사용자가 한 번 클릭하기 전에는 소리를 내지 않으므로 "소리 켜기" 버튼이 필요합니다.

## WebSocket: `ws://<서버>/ws`

연결하면 즉시 `state`가 한 번 옵니다. 끊기면 2초 뒤 다시 연결합니다. 클라이언트가 보낼 메시지는 없습니다. 조작은 전부 아래 REST로 합니다.

### state

```json
{"type": "state", "match": { "...": "아래 '경기 상태'" }}
```

### goal (골 직후 1차 해설)

```json
{
  "type": "goal",
  "goal_id": 3,
  "team": "B",
  "situation": "역전골",
  "audio": "/audio/team_B팀_역전골.mp3",
  "sfx": "/audio/fixed_crowd.mp3",
  "text": "B팀! 드디어 경기를 뒤집습니다! 역전골!!",
  "cancel_deadline": 1793241010000
}
```

`cancel_deadline`(Unix ms)까지 주최자 폰에 "노골" 버튼과 카운트다운을 보여 줍니다.

### scorer_set (득점자 탭 후 2차 해설)

```json
{
  "type": "scorer_set",
  "goal_id": 3,
  "team": "B",
  "scorer": "독수리",
  "situation": "역전골",
  "audio": "/audio/player_독수리_역전골.mp3",
  "text": "독수리!!! 역전골입니다!!!"
}
```

같은 선수의 3번째 골이면 `situation`이 `"해트트릭"`입니다.

### goal_cancelled (노골 정정)

```json
{"type": "goal_cancelled", "goal_id": 3, "team": "B", "audio": "/audio/fixed_no_goal.mp3", "text": "판독 결과, 노골입니다!"}
```

### period (킥오프, 하프타임, 후반 시작)

```json
{"type": "period", "status": "halftime", "audio": "/audio/fixed_halftime.mp3", "text": "전반전 종료!"}
```

`status`는 `first_half`(킥오프), `halftime`, `second_half` 중 하나입니다.

### match_end

```json
{"type": "match_end", "score": {"A": 3, "B": 2}, "winner": "A", "audio": "/audio/fixed_match_end.mp3", "text": "경기 종료!"}
```

`winner`는 `"A"`, `"B"`, 무승부면 `null`입니다.

## 경기 상태 (`state.match`, `GET /api/match`)

```json
{
  "status": "second_half",
  "half_minutes": 15,
  "period_started_at": 1793240000000,
  "server_now": 1793240123456,
  "teams": {"A": {"name": "A팀", "color": "#f97316"}, "B": {"name": "B팀", "color": "#3b82f6"}},
  "players": {"A": ["호랑이", "번개"], "B": ["독수리", "여우"]},
  "score": {"A": 2, "B": 1},
  "sides": {"left": "B", "right": "A"},
  "audio_ready": true,
  "goals": [
    {"goal_id": 1, "team": "A", "scorer": "호랑이", "situation": "선제골", "half": "first",
     "clock": "03:12", "source": "live", "cancelled": false, "cancel_deadline": 1793239202000}
  ]
}
```

- `status`: `setup` → `first_half` → `halftime` → `second_half` → `ended`
- 타이머: 받은 순간의 경과 시간은 `server_now - period_started_at`이고, 화면에서 1초씩 올립니다. 경기 중이 아니면 `period_started_at`은 `null`입니다.
- `sides`: 왼쪽·오른쪽 골대를 **지키는** 팀입니다(점수판 화면 기준). 하프타임에 진영을 바꾸면 뒤집힙니다.
- `goals`: 취소된 골도 `cancelled: true`로 남습니다(결과 화면의 "노골 정정" 표시용). `scorer`는 지정 전이면 `null`입니다.
- `audio_ready`: 지금 이름으로 필요한 음성 파일이 다 만들어졌으면 `true`입니다(설정 화면의 "음성 준비 중" 표시용).

## REST

에러는 FastAPI 기본 형식 `{"detail": "..."}`입니다. 400은 잘못된 값, 404는 없는 골, 409는 지금 할 수 없는 동작입니다.

- `POST /api/match` — 새 경기로 초기화하고 음성 생성을 시작합니다. 응답은 경기 상태입니다.
  본문: `{"teams": {"A": {"name": "A팀", "color": "#f97316"}, "B": {"name": "B팀", "color": "#3b82f6"}}, "players": {"A": ["호랑이", "번개"], "B": ["독수리", "여우"]}, "half_minutes": 15}`
- `POST /api/match/kickoff` — 본문 없음. `setup`에서만 됩니다.
- `POST /api/match/halftime` — 본문 `{"swap": true}`. `first_half`에서만 됩니다. `swap`이 `true`면 진영을 바로 바꿉니다.
- `POST /api/match/second-half` — 본문 없음. `halftime`에서만 됩니다.
- `POST /api/match/end` — 본문 없음. 경기 중이나 하프타임에 됩니다. 결과를 `eval/results/`에 JSON으로 저장합니다.
- `POST /api/events/goal` — 감지 스크립트가 보내는 골 신호입니다.
  본문: `{"goal_side": "left", "ts": 12.4, "conf": 0.91, "source": "live"}`
  응답(항상 200): `{"accepted": true, "goal_id": 3}` 또는 `{"accepted": false, "reason": "cooldown"}` 또는 `{"accepted": false, "reason": "not_in_play"}`
- `POST /api/goals/{goal_id}/cancel` — 본문 없음. `cancel_deadline`이 지나면 409입니다.
- `POST /api/goals/{goal_id}/scorer` — 본문 `{"nickname": "호랑이"}`. 득점 팀 선수가 아니면 400입니다. 다시 보내면 득점자가 바뀝니다.
- `POST /api/goals/manual` — 본문 `{"team": "A"}`. 감지가 놓친 골을 수동으로 넣습니다. 쿨다운이 없고 `source`는 `"manual"`입니다.
- `GET /api/match` — 경기 상태입니다. 새로고침이나 재접속 때 씁니다.

## 골 신호 (최)

- `goal_side`: `"left"` 또는 `"right"`. 점수판 화면 기준 골대입니다. 득점 팀은 그 골대를 지키는 팀의 상대입니다.
- `ts`: 영상 시작부터의 초. `conf`: 공 검출 신뢰도. `source`: `"live"` 또는 `"replay"`.
- 같은 골대에서 10초 안에 다시 온 신호는 서버가 무시합니다(`reason: "cooldown"`). 여러 번 보내도 한 골로 처리됩니다.

## 상황 이름 (`situation`)

선제골, 동점골, 역전골, 앞서가는골, 추격골, 달아나는골, 해트트릭(2차 해설에서만)
