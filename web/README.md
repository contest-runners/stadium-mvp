# web (오) — 점수판 · 주최자 폰 화면

| 파일 | 화면 | 백엔드에서 띄우는 주소 |
|---|---|---|
| `index.html` | 점수판 (노트북·TV) | `http://노트북IP:8000/` |
| `host.html` | 주최자 폰 (등록 → 경기 → 하프타임 → 종료) | `http://노트북IP:8000/host` |

## 백엔드 없이 테스트 (가짜 모드)

```
cd web
python -m http.server 5500
```
같은 브라우저에서 **점수판을 먼저** 열고, 그다음 주최자 화면을 엽니다. 두 탭이 서로 연동됩니다.
- 점수판: `http://localhost:5500/index.html?mock=1` → "소리 켜기"
- 주최자: `http://localhost:5500/host.html?mock=1` (개발자 도구 폰 화면으로 보면 편함)

점수판 왼쪽 위의 흐린 버튼(마우스 올리면 보임)으로도 골·노골·하프타임을 넣을 수 있습니다.
mp3가 없으면 브라우저 음성으로, 함성 파일이 없으면 임시 함성으로 대신 재생합니다.

주소 옵션: `?mock=1` 가짜 모드, `?server=http://IP:8000` 다른 서버에 붙기, `?dev=1` 진짜 서버에서도 테스트 버튼 표시.

## 노트북 점수판 + 진짜 폰으로 혼자 테스트 (대역 서버)

이의 서버가 나오기 전까지 쓰는 테스트용 서버입니다. 아래 형식 그대로 동작합니다.

```
cd web
python dev_server.py
```
1. 노트북 브라우저: `http://localhost:8000/` → "소리 켜기"
2. 폰을 노트북과 같은 와이파이(또는 노트북 핫스팟)에 연결 → 폰 크롬: 서버가 출력한 `http://노트북IP:8000/host`
3. 폰에서 등록 → 킥오프
4. 다른 터미널에서 골 신호: `python dev_server.py --goal left` (감지 스크립트가 보내는 것과 같은 신호)

폰에서 안 열리면 대부분 윈도우 방화벽 문제입니다. 처음 실행할 때 뜨는 허용 창에서 "허용"을 누르세요.
이의 서버가 나오면 `dev_server.py` 대신 이의 서버를 켜고 같은 주소로 접속하면 됩니다.

## 서버 형식이 바뀌면

두 파일 안의 **"공통 블록"** (`normalize` 함수 근처)만 고치면 됩니다. 두 파일에 같은 블록이 들어 있으니 둘 다 고쳐 주세요.

---

## 이(백엔드)에게: 화면이 기대하는 형식

팀장 문서 예시 형식을 기준으로 만들었고, 아래와 다르게 와도 웬만하면 받도록 해 두었습니다
(점수는 `[2,1]` 배열·`{"A":2,"B":1}` 둘 다 OK, `audio`/`voice` 둘 다 OK, `역전`/`역전골` 둘 다 OK).
**굵게 표시한 것**이 꼭 필요한 값입니다.

### 서버 → 화면 (`WS /ws`)

| type | 필드 | 화면에서 쓰는 곳 |
|---|---|---|
| `state` | **`phase`** (`setup`·`first_half`·`halftime`·`second_half`·`ended`), **`score`**, **`teams`** `[{name, color, players:["번개",…]}, …]`, `goals`, `clock` `{running, elapsed초}` | 새로고침·재접속 복구, 화면 전환, 득점자 버튼, 시계 |
| `goal` | **`goal_id`**, **`team`** (`"A"`/`"B"`), **`score`**, **`situation`**, **`audio`**, `text`, `time`, `cancel_until`(초) | 점수·연출·1차 해설, 주최자 골 카드 |
| `goal_cancelled` | **`goal_id`**, **`score`**, `audio` | 점수 복구, "노골" 연출 |
| `scorer_set` | **`goal_id`**, **`team`**, **`nick`**, **`situation`**(해트트릭이면 `"해트트릭"`), **`audio`**, `player_goals` | 2차 해설, 득점 기록 |
| `match_end` | **`score`**, `goals` `[{goal_id, team, half, time, situation, nick, cancelled}]` | 종료 화면 |

- `text`가 오면 그 문장을 화면에 띄우고, 없으면 화면이 `situation`으로 문장을 만듭니다.
- `phase` 상태가 바뀔 때(킥오프·하프타임·후반·종료)마다 `state`를 한 번 보내 주세요.
- `GET /api/match`는 `state`와 같은 내용(type 제외)을 돌려주면 됩니다.

### 화면 → 서버 (주최자 폰이 보내는 요청)

| 버튼 | 요청 | body |
|---|---|---|
| 킥오프 | `POST /api/match` 다음 `POST /api/match/kickoff` | `{"teams":[{"name","color","players":[…]}, …], "half_minutes":15, "swap_at_half":true}` |
| 수동 골 | `POST /api/goals/manual` | `{"team":"A"}` |
| 노골 | `POST /api/goals/{id}/cancel` | 없음 |
| 득점자 | `POST /api/goals/{id}/scorer` | `{"player_index":0, "nick":"번개"}` (둘 다 보냄, 편한 쪽 사용) |
| 하프타임 | `POST /api/match/halftime` | `{"swap":true}` (바꿈/그대로 고르면 다시 보냄) |
| 후반 시작 | `POST /api/match/second-half` | 없음 |
| 경기 종료 | `POST /api/match/end` | 없음 |

### 음성 파일
- `GET /audio/{파일명}`으로 받습니다. 파일명은 `audio` 필드 그대로 (`A_역전골.mp3`, `번개_해트트릭.mp3`).
- 화면이 쓰는 고정 파일: `cheer.mp3`(함성), `노골.mp3`, `경기종료.mp3`. 이름이 다르면 알려 주세요.

### 상황 분류 확인 부탁
`0:1 → 1:1 → 2:1 → 2:2 → 3:2` 의 마지막 골은 **앞서가는 골**이어야 합니다 (리드하다 따라잡힌 뒤 다시 앞서감).
"이 경기에서 뒤진 적이 있음"으로 판단하면 역전골로 잘못 나옵니다. **"동점이 되기 직전에 누가 앞서 있었나"**로 판단해 주세요.
가짜 모드의 `Mock.situation` 함수가 이 방식입니다.
