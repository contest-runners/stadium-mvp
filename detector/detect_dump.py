"""
공 검출 결과를 프레임마다 한 번만 저장 → tune.py로 판정 설정을 몇 초 만에 바꿔 보며 실험.
(모델은 한 번만 돌리고, 판정 규칙만 바꿔 보는 용도)

  python detector/detect_dump.py --video eval/videos/test_1.mp4 --out eval/results/test_1_dets.csv

CPU면 영상 길이 × (30 / 처리fps) 만큼 걸림. 코랩 GPU에서 돌리고 csv만 받아도 됨.
"""
import argparse
import csv
import os
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))

ap = argparse.ArgumentParser()
ap.add_argument("--video", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--model", default=os.path.join(HERE, "weights", "ball_s.pt"))
ap.add_argument("--imgsz", type=int, default=960)
ap.add_argument("--conf", type=float, default=0.05, help="낮게 저장해 두고 tune.py에서 거름")
ap.add_argument("--cls", type=int, default=None)
ap.add_argument("--device", default=None)
a = ap.parse_args()

import torch
from ultralytics import YOLO

device = a.device or ("cuda" if torch.cuda.is_available()
                      else "mps" if torch.backends.mps.is_available() else "cpu")
model = YOLO(a.model)
cls = a.cls
if cls is None:
    cls = [i for i, n in model.names.items() if "ball" in str(n).lower()][0]

cap = cv2.VideoCapture(a.video)
fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
w, h = int(cap.get(3)), int(cap.get(4))
os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
f = open(a.out, "w", newline="")
wr = csv.writer(f)
wr.writerow(["frame", "t", "cx", "cy", "size", "conf", "w", "h"])

i, t0 = 0, time.time()
while True:
    ok, frame = cap.read()
    if not ok:
        break
    r = model.predict(frame, classes=[cls], conf=a.conf, imgsz=a.imgsz, device=device, verbose=False)[0]
    if len(r.boxes):
        k = int(r.boxes.conf.argmax())
        x1, y1, x2, y2 = r.boxes.xyxy[k].tolist()
        wr.writerow([i, round(i / fps, 3), round((x1 + x2) / 2, 1), round((y1 + y2) / 2, 1),
                     round(max(x2 - x1, y2 - y1), 1), round(float(r.boxes.conf[k]), 3), w, h])
    else:
        wr.writerow([i, round(i / fps, 3), "", "", "", "", w, h])
    i += 1
    if i % 300 == 0:
        el = time.time() - t0
        print(f"{i}/{total} 프레임 | {i / el:.1f}fps | 남은 시간 약 {(total - i) / (i / el) / 60:.1f}분")
f.close()
print(f"저장: {a.out} ({i} 프레임, {device})")
