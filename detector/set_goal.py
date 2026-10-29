"""
골문 다각형 지정 도구
  python detector/set_goal.py --video eval/videos/match1.mp4 --goal-side left [--t 3]
  python detector/set_goal.py --video http://폰IP:8080/video --goal-side left   (스트림도 됨)

마우스 좌클릭으로 꼭짓점 4개 이상 (골 '안쪽 공간'을 감싸게):
  ① 가까운 포스트 바닥 → ② 가까운 포스트 위(화면 밖이면 화면 끝)
  ③ 그물 뒤쪽 위       → ④ 그물 뒤쪽 바닥
  골문 입구 면만 찍으면 공이 골라인을 넘은 뒤 금방 다각형을 빠져나가 놓치기 쉬움

  z: 마지막 점 취소   s: 저장 후 종료   q: 저장 없이 종료
저장: detector/goal_<side>.json
"""
import argparse
import json
import os

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

ap = argparse.ArgumentParser()
ap.add_argument("--video", required=True)
ap.add_argument("--goal-side", default="left", choices=["left", "right"])
ap.add_argument("--t", type=float, default=0.0, help="파일일 때 이 시점(초) 프레임 사용")
ap.add_argument("--out", default=None)
ap.add_argument("--display-width", type=int, default=1280)
a = ap.parse_args()

src = int(a.video) if a.video.isdigit() else a.video
cap = cv2.VideoCapture(src)
if isinstance(src, str) and not src.startswith(("http", "rtsp")) and a.t:
    cap.set(cv2.CAP_PROP_POS_MSEC, a.t * 1000)
ok, frame = cap.read()
cap.release()
if not ok:
    raise SystemExit("프레임을 못 읽음 (경로·코덱 확인)")

h, w = frame.shape[:2]
scale = min(1.0, a.display_width / w)          # 화면이 작아도 클릭 좌표는 원본 기준으로 저장
pts = []
WIN = "goal: click points / z undo / s save / q quit"
GUIDE = ["1 near post bottom", "2 near post top", "3 net back top", "4 net back bottom"]


def on_mouse(event, x, y, flags, _):
    if event == cv2.EVENT_LBUTTONDOWN:
        pts.append([int(x / scale), int(y / scale)])


cv2.namedWindow(WIN)
cv2.setMouseCallback(WIN, on_mouse)
while True:
    view = frame.copy()
    if pts:
        p = np.array(pts, np.int32)
        cv2.polylines(view, [p], len(pts) > 2, (0, 255, 0), 2)
        for i, (x, y) in enumerate(pts):
            cv2.circle(view, (x, y), 6, (0, 0, 255), -1)
            cv2.putText(view, str(i + 1), (x + 8, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
    msg = GUIDE[len(pts)] if len(pts) < 4 else "s: save"
    cv2.putText(view, msg, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
    cv2.imshow(WIN, cv2.resize(view, (int(w * scale), int(h * scale))))
    k = cv2.waitKey(20) & 0xFF
    if k == ord("z") and pts:
        pts.pop()
    elif k == ord("s"):
        if len(pts) < 3:
            print("점 3개 이상 필요")
            continue
        out = a.out or os.path.join(HERE, f"goal_{a.goal_side}.json")
        json.dump({"polygon": pts, "frame_size": [w, h]}, open(out, "w"))
        print(f"저장: {out} ({len(pts)}점) {pts}")
        break
    elif k == ord("q"):
        break
cv2.destroyAllWindows()
