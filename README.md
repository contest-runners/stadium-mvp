# 생활체육 자동 스타디움 연출 (stadium-mvp)

생활체육 풋살 경기에서 카메라 영상으로 골을 자동 감지해 점수판을 올리고, 선제골·동점골·역전골 같은 상황에 맞는 해설과 함성을 틀어 주는 MVP입니다.
주최자는 폰으로 팀과 선수 별명을 등록하고, 경기 중에는 득점자 지정과 노골 정정만 탭으로 합니다.

▶ 시연 영상: [시연 영상 링크]

## 폴더 구성

- `backend/` (이) — FastAPI 서버. 골 신호를 받아 점수와 상황을 판정하고, 해설 음성을 골라 WebSocket으로 점수판과 폰에 보냅니다. 화면·감지와 주고받는 형식은 `backend/API.md`에 있습니다.
- `detector/` (최) — 영상에서 공을 찾아(YOLO) 골문 안에 들어가면 서버로 골 신호를 보냅니다. 골문 지정, 감지, 재생 스크립트가 있습니다.
- `web/` (오) — 노트북·TV용 점수판(`index.html`)과 주최자 폰 화면(`host.html`).
- `eval/` (조) — 평가 자료. 경기 결과 JSON은 `eval/results/`에 저장되고, 평가 영상(`eval/videos/`)은 용량 때문에 Google Drive로 따로 공유합니다.
- `audio/` — 해설 음성 mp3, 함성(`cheer.mp3`), 팀·선수 이름 목록(`names.json`). `backend/make_audio.py`가 만들고 화면이 재생합니다.

## 설치

Python 3.11, 저장소 루트에서 실행합니다. 아래는 Windows 기준이며, macOS·Linux에서는 `.venv/Scripts/python.exe` 대신 `.venv/bin/python`을 씁니다.

```
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
.venv/Scripts/python.exe -m pip install ultralytics
```

ultralytics는 감지용입니다(PyTorch, OpenCV가 같이 설치됩니다). GPU가 없어도 CPU에서 동작합니다. 공 감지 모델 `detector/weights/ball_s.pt`는 저장소에 들어 있습니다. 평가에 쓴 원본 촬영 영상은 용량 때문에 저장소에 없고 `eval/videos/`에 따로 넣어 씁니다.

## 실행 순서

1. 서버를 켭니다. 처음 실행할 때 Windows 방화벽 창이 뜨면 "허용"을 눌러야 폰에서 접속됩니다.
   ```
   .venv/Scripts/python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
   ```
2. 노트북 브라우저에서 점수판 `http://localhost:8000/`을 열고 "소리 켜기"를 누릅니다. F 키로 전체 화면이 됩니다.
3. 노트북과 같은 네트워크(Wi-Fi 또는 노트북 핫스팟)에 연결한 폰의 크롬에서 `http://<노트북IP>:8000/host`를 엽니다(IP는 `ipconfig`의 IPv4 주소). 팀 이름·조끼 색·전반 시간·하프타임 진영 교대를 정하고, 선수 별명을 등록한 뒤 "킥오프"를 누릅니다. 새 이름의 해설 음성은 서버가 백그라운드에서 만들고, 다 만들어지기 전에는 화면이 브라우저 음성으로 읽습니다.
4. 감지 신호를 보냅니다. 이 노트북(CPU)에서는 감지가 초당 약 4.7프레임이라 30fps 영상을 실시간으로 따라가지 못합니다. 그래서 골 시각을 미리 뽑아 두고, 영상 재생에 맞춰 신호를 보내는 replay 방식을 씁니다. 아래 예시의 영상 이름은 평가 영상 중 하나입니다.

   ① 골문 지정(영상마다 1회, 직접 클릭). 가까운 포스트 바닥 → 포스트 위 → 그물 뒤 위 → 그물 뒤 바닥 순으로 클릭하고 s로 저장합니다(z: 되돌리기, q: 저장 없이 종료). 골문이 가려진 장면이면 `--t 30`처럼 다른 시점(초)을 고릅니다.
   ```
   .venv/Scripts/python.exe detector/set_goal.py --video eval/videos/20261028_152454.mp4 --goal-side left --out detector/goal_20261028_152454.json
   ```
   ② 골 시각 미리 뽑기(서버 불필요, 이 노트북에서 영상 길이의 약 6.5배 걸림)
   ```
   .venv/Scripts/python.exe detector/run.py --video eval/videos/20261028_152454.mp4 --goal-side left --goal detector/goal_20261028_152454.json --no-realtime --no-show --events-out eval/results/20261028_152454_events.csv --save eval/results/20261028_152454_overlay.mp4
   ```
   ③ 킥오프한 뒤, 영상을 재생하면서 골 시각에 맞춰 서버로 신호 보내기
   ```
   .venv/Scripts/python.exe detector/replay.py --video eval/results/20261028_152454_overlay.mp4 --events eval/results/20261028_152454_events.csv --goal-side left --server http://localhost:8000
   ```
   `--goal-side`는 카메라가 보는 골대가 점수판의 왼쪽(`left`)인지 오른쪽(`right`)인지입니다. 킥오프 때 왼쪽 골대는 A팀이 지키므로 `left` 골대에 들어간 골은 B팀 득점입니다. 하프타임에 진영을 바꿔도 카메라는 그대로 두면 서버가 팀을 바꿔 계산합니다. 실시간 처리가 되는 컴퓨터에서는 `detector/run.py`에 `--server http://localhost:8000`을 붙여 바로 보낼 수 있습니다.

## 카메라와 감지 없이 확인하기

서버를 켜고 점수판(`http://localhost:8000/`)을 연 상태에서 실행합니다.

```
.venv/Scripts/python.exe -m backend.devtool demo
```

`audio/names.json`의 이름으로 경기를 만들고 킥오프, 골 신호, 수동 골, 득점자 지정, 하프타임 진영 교대, 노골 정정, 해트트릭, 경기 종료까지 16단계를 3초 간격으로 진행합니다. 점수판의 점수·자막·해설이 단계마다 바뀝니다.

## 알려진 한계

- 녹화 영상 처리: 실시간 카메라 대신 녹화 영상을 replay 방식으로 처리해 시연합니다.
- 카메라 1대: 한쪽 골대만 봅니다. 반대편 골은 주최자가 폰의 "수동 골" 버튼으로 넣습니다.
- 득점자 지정: 득점자를 자동으로 알아보지 않습니다. 주최자가 폰에서 별명을 탭하면 이름을 부르는 2차 해설이 나갑니다.
- 골 판정: 빗나간 공·선방·골대 맞은 공을 따로 구분하지 않습니다. 잘못 잡힌 골은 골 후 10초 안에 주최자가 "노골"을 한 번 눌러 정정합니다(AI 감지 + 사람 확인 1탭).

## 출처와 라이선스

함성 효과음 출처: Pixabay "Crowd Cheering" (업로더 u_xg7ssi08yr), Pixabay Content License. 원본 일부를 잘라 페이드를 넣어 사용. https://pixabay.com/sound-effects/people-crowd-cheering-379666/

해설 음성: edge-tts로 생성(`backend/make_audio.py`, 음성 `ko-KR-InJoonNeural`).

감지 모델: Ultralytics YOLO (AGPL-3.0). YOLO11s를 공 감지용으로 파인튜닝한 `detector/weights/ball_s.pt`를 씁니다.

데이터셋: [조가 정리한 표]

## 팀

- 이 — 백엔드(골 판정·점수·상황 분류·실시간 전송)와 해설 음성 생성 (`backend/`, `audio/`)
- 최 — 공 감지 모델과 골 감지 스크립트 (`detector/`)
- 오 — 점수판과 주최자 폰 화면 (`web/`)
- 조 — 실측(감지 정확도·지연), 데이터셋 정리, 고도화·사업화 서류 (`eval/`)
- 재헌 — 팀장: 결정, 서류 합본, 제출
