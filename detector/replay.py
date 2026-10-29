"""
재생 스크립트 — 노트북이 느려 실시간 감지가 안 될 때의 대안.
run.py --no-realtime --events-out 으로 미리 뽑은 골 시각을, 영상 재생에 맞춰 백엔드로 보낸다.

  python detector/run.py --video match1.mp4 --goal-side left --no-realtime --no-show \
                         --events-out eval/results/match1_events.csv --save eval/results/match1_overlay.mp4
  python detector/replay.py --video eval/results/match1_overlay.mp4 --events eval/results/match1_events.csv \
                            --goal-side left --server http://localhost:8000

--video에 오버레이 영상(--save 결과)을 넣으면 박스·다각형이 그려진 화면이 그대로 재생된다.
"""
import argparse
import csv
import time

import cv2
import requests

ap = argparse.ArgumentParser()
ap.add_argument("--video", required=True)
ap.add_argument("--events", required=True)
ap.add_argument("--goal-side", default="left", choices=["left", "right"])
ap.add_argument("--server", required=True)
ap.add_argument("--offset", type=float, default=0.0, help="영상 0초가 원본 몇 초인지 (run.py --start 값)")
ap.add_argument("--display-width", type=int, default=1280)
a = ap.parse_args()

events = [(float(r["ts"]), float(r["conf"])) for r in csv.DictReader(open(a.events))]
cap = cv2.VideoCapture(a.video)
fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
i, sent, t0 = 0, 0, time.time()
print(f"골 {len(events)}개 예약: {[e[0] for e in events]}")

while True:
    ok, frame = cap.read()
    if not ok:
        break
    t = a.offset + i / fps
    while sent < len(events) and events[sent][0] <= t:
        ts, conf = events[sent]
        p = {"goal_side": a.goal_side, "ts": ts, "conf": conf, "source": "replay"}
        try:
            r = requests.post(a.server.rstrip("/") + "/api/events/goal", json=p, timeout=1)
            print(f"GOAL  t={ts:7.2f}s → {r.status_code}")
        except Exception as e:
            print(f"GOAL  t={ts:7.2f}s → 전송 실패 {e}")
        sent += 1
    h, w = frame.shape[:2]
    dw = min(a.display_width, w)
    cv2.imshow("stadium replay (q: quit)", cv2.resize(frame, (dw, int(h * dw / w))))
    i += 1
    wait = i / fps - (time.time() - t0)          # 실제 속도로 재생
    if cv2.waitKey(max(1, int(wait * 1000))) & 0xFF == ord("q"):
        break
cap.release()
cv2.destroyAllWindows()
