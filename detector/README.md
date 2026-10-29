# detector — 골 감지

영상(녹화 파일 / 폰 스트림)에서 공을 찾아 골문 안에 들어가면 백엔드로 골 신호를 보냅니다.

## 준비
```
pip install ultralytics requests opencv-python
```
모델 파일 `ball_s.pt`는 용량 때문에 git에 없습니다. 아래 링크에서 받아 `detector/weights/ball_s.pt`에 넣으세요.
- 다운로드: DRIVE_LINK
- 모델: YOLO11s를 Roboflow `futsal-scoring-model` + 자체 촬영 영상으로 파인튜닝 (클래스: ball)

## 실행
```
python detector/set_goal.py --video 영상.mp4 --goal-side left      # 골문 4점 지정 (최초 1회)
python detector/run.py --video 영상.mp4 --goal-side left --server http://localhost:8000
```
골문 클릭 순서: 골라인 바닥 → 골라인 위 → 그물 뒤 위 → 그물 뒤 바닥

## 골 신호
`POST /api/events/goal`
```json
{"goal_side": "left", "ts": 12.4, "conf": 0.91, "source": "live"}
```

## 실시간이 안 될 때 (재생 방식)
```
python detector/run.py --video 영상.mp4 --goal-side left --no-realtime --no-show --events-out ev.csv --save overlay.mp4
python detector/replay.py --video overlay.mp4 --events ev.csv --goal-side left --server http://localhost:8000
```
