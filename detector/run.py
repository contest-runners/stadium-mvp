"""
골 감지 스크립트 — 영상(파일/폰 스트림/웹캠)에서 공을 찾아 골이면 백엔드로 신호를 보낸다.

예시
  # 녹화 영상, 실제 속도로 재생하며 감지 (시연용)
  python detector/run.py --video eval/videos/match1.mp4 --goal-side left --server http://localhost:8000

  # 폰 스트림 (IP Webcam 등)
  python detector/run.py --video http://192.168.0.5:8080/video --goal-side left --server http://localhost:8000

  # 실측용: 프레임 하나도 안 건너뛰고 끝까지 + 골 시각 저장 (서버 없이)
  python detector/run.py --video match1.mp4 --goal-side left --no-realtime --no-show --events-out eval/results/match1_events.csv

판정 규칙 (시간 기준이라 fps·처리 속도가 달라도 같은 기준)
  - 공 중심이 골문 다각형 안에서 --min-in 초 이상 잡히면 골
  - 그 사이 --gap 초까지의 검출 누락은 봐줌, 다각형 밖에서 잡히면 리셋
  - 골 후에는 공이 다각형 밖에 --rearm 초 있어야 다음 골 인정 (그물 속 공 중복 방지)
  - 골 후 최소 --cooldown 초는 무조건 쉼

골 신호: POST {server}/api/events/goal
  {"goal_side": "left", "ts": 12.4, "conf": 0.91, "source": "live"}
"""
import argparse
import csv
import json
import os
import queue
import threading
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- 인자
def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="영상 파일, 스트림 URL, 또는 웹캠 번호(0, 1...)")
    ap.add_argument("--goal-side", default="left", choices=["left", "right"])
    ap.add_argument("--server", default=None, help="백엔드 주소. 없으면 신호 안 보내고 화면에만 표시")
    ap.add_argument("--goal", default=None, help="골문 다각형 json (기본: detector/goal_<side>.json)")
    ap.add_argument("--model", default=os.path.join(HERE, "weights", "ball_s.pt"))
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--conf", type=float, default=0.6)
    ap.add_argument("--cls", type=int, default=None, help="공 클래스 번호 (기본: 이름에 ball 들어간 것)")
    ap.add_argument("--device", default=None, help="cuda / mps / cpu (기본 자동)")
    # 판정
    ap.add_argument("--min-in", type=float, default=0.03, help="골문 안에 이 시간(초) 이상 잡히면 골")
    ap.add_argument("--gap", type=float, default=0.2, help="허용할 검출 누락 시간(초)")
    ap.add_argument("--rearm", type=float, default=3.0, help="골 후 공이 밖에 있어야 하는 시간(초)")
    ap.add_argument("--cooldown", type=float, default=3.0, help="골 후 최소 대기(초)")
    ap.add_argument("--ball-min", type=float, default=0, help="골 인정 공 크기 하한(px). 0이면 안 씀")
    ap.add_argument("--ball-max", type=float, default=0, help="골 인정 공 크기 상한(px). 0이면 안 씀")
    # 재생·출력
    ap.add_argument("--no-realtime", action="store_true",
                    help="파일을 실제 속도에 맞추지 않고 모든 프레임 처리 (실측용)")
    ap.add_argument("--start", type=float, default=0.0, help="파일 시작 초")
    ap.add_argument("--dur", type=float, default=None, help="처리할 길이(초)")
    ap.add_argument("--no-show", action="store_true", help="창 안 띄움")
    ap.add_argument("--display-width", type=int, default=1280)
    ap.add_argument("--save", default=None, help="오버레이 영상 저장 경로 (.mp4)")
    ap.add_argument("--events-out", default=None, help="골 시각 csv 저장 경로 (replay.py 입력)")
    ap.add_argument("--source", default="live")
    return ap.parse_args()


# ---------------------------------------------------------------- 입력
def is_stream(v):
    return v.isdigit() or v.startswith(("http://", "https://", "rtsp://"))


class FileSource:
    """녹화 파일. realtime이면 벽시계에 맞춰 밀린 프레임을 건너뛴다(시연과 같은 조건)."""

    def __init__(self, path, realtime, start, dur):
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise SystemExit(f"영상을 못 엶: {path}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        if start:
            self.cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        self.start, self.dur, self.realtime = start, dur, realtime
        self.idx = 0          # start 기준 프레임 번호
        self.t0 = None
        self.skipped = 0

    def read(self):
        if self.realtime:
            now = time.time()
            if self.t0 is None:
                self.t0 = now
            target = int((now - self.t0) * self.fps)
            # 처리가 밀렸으면 따라잡을 때까지 디코딩만 하고 버림
            while self.idx < target:
                if not self.cap.grab():
                    return None, None
                self.idx += 1
                self.skipped += 1
            # 처리가 빠르면 실제 속도가 될 때까지 기다림
            wait = self.idx / self.fps - (time.time() - self.t0)
            if wait > 0:
                time.sleep(wait)
        ok, frame = self.cap.read()
        if not ok:
            return None, None
        t = self.start + self.idx / self.fps
        self.idx += 1
        if self.dur and t >= self.start + self.dur:
            return None, None
        return frame, t

    def release(self):
        self.cap.release()


class StreamSource:
    """폰 스트림·웹캠. 버퍼에 쌓인 옛 프레임 대신 항상 최신 프레임만 넘긴다."""

    def __init__(self, src):
        self.cap = cv2.VideoCapture(int(src) if src.isdigit() else src)
        if not self.cap.isOpened():
            raise SystemExit(f"스트림을 못 엶: {src}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.skipped = 0
        self.lock = threading.Lock()
        self.frame, self.alive = None, True
        self.t0 = time.time()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self.alive:
            ok, f = self.cap.read()
            if not ok:
                self.alive = False
                break
            with self.lock:
                self.frame = f

    def read(self):
        while self.alive:
            with self.lock:
                f, self.frame = self.frame, None
            if f is not None:
                return f, time.time() - self.t0
            time.sleep(0.002)
        return None, None

    def release(self):
        self.alive = False
        self.cap.release()


# ---------------------------------------------------------------- 신호 전송
class Sender:
    """골 신호를 별도 스레드에서 보낸다 (네트워크 때문에 감지가 멈추지 않게)."""

    def __init__(self, server):
        self.server = server.rstrip("/") if server else None
        self.q = queue.Queue()
        if self.server:
            import requests
            self.requests = requests
            threading.Thread(target=self._loop, daemon=True).start()

    def send(self, payload):
        if self.server:
            self.q.put(payload)

    def _loop(self):
        while True:
            p = self.q.get()
            try:
                r = self.requests.post(self.server + "/api/events/goal", json=p, timeout=1)
                print(f"  → 서버 응답 {r.status_code}")
            except Exception as e:
                print(f"  → 전송 실패: {e}")


# ---------------------------------------------------------------- 판정
class GoalJudge:
    def __init__(self, poly, a):
        self.poly = poly
        self.a = a
        self.in_since = None       # 골문 안에서 처음 잡힌 시각
        self.last_seen_in = None   # 골문 안에서 마지막으로 잡힌 시각
        self.out_since = None
        self.armed = True
        self.last_goal = -1e9
        self.best_conf = 0.0

    def size_ok(self, size):
        if self.a.ball_min and size < self.a.ball_min:
            return False
        if self.a.ball_max and size > self.a.ball_max:
            return False
        return True

    def update(self, t, ball):
        """ball = (cx, cy, size, conf) 또는 None. 골이면 conf 반환."""
        a = self.a
        if ball is not None:
            cx, cy, size, conf = ball
            inside = cv2.pointPolygonTest(self.poly, (float(cx), float(cy)), False) >= 0 and self.size_ok(size)
            if inside:
                self.out_since = None
                if self.in_since is None:
                    self.in_since, self.best_conf = t, 0.0
                self.last_seen_in = t
                self.best_conf = max(self.best_conf, conf)
            else:
                self.in_since = None
                if self.out_since is None:
                    self.out_since = t
                if t - self.out_since >= a.rearm:
                    self.armed = True
        else:
            if self.last_seen_in is not None and t - self.last_seen_in > a.gap:
                self.in_since = None

        if (self.in_since is not None and self.last_seen_in - self.in_since >= a.min_in
                and self.armed and t - self.last_goal >= a.cooldown):
            self.last_goal = t
            self.armed = False
            self.in_since = None
            return self.best_conf
        return None

    def progress(self):
        if self.in_since is None or self.last_seen_in is None:
            return 0.0
        return min(1.0, (self.last_seen_in - self.in_since) / max(self.a.min_in, 1e-6))


# ---------------------------------------------------------------- 메인
def main():
    a = parse_args()
    goal_path = a.goal or os.path.join(HERE, f"goal_{a.goal_side}.json")
    if not os.path.exists(goal_path):
        raise SystemExit(f"골문 파일 없음: {goal_path}\n→ python detector/set_goal.py --video ... --goal-side {a.goal_side}")
    g = json.load(open(goal_path))
    poly = np.array(g["polygon"], np.float32)

    import torch
    from ultralytics import YOLO
    device = a.device or ("cuda" if torch.cuda.is_available()
                          else "mps" if torch.backends.mps.is_available() else "cpu")
    model = YOLO(a.model)
    names = model.names
    ball_cls = a.cls
    if ball_cls is None:
        cand = [i for i, n in names.items() if "ball" in str(n).lower()]
        if not cand:
            raise SystemExit(f"공 클래스를 못 찾음 → --cls 지정. 클래스: {names}")
        ball_cls = cand[0]
    print(f"모델 {os.path.basename(a.model)} | 공 클래스 {ball_cls}({names[ball_cls]}) | {device} | imgsz {a.imgsz}")

    src = (StreamSource(a.video) if is_stream(a.video)
           else FileSource(a.video, realtime=not a.no_realtime, start=a.start, dur=a.dur))

    # 골문 파일 해상도와 영상 해상도가 다르면 좌표 맞춰 줌
    first, t_first = src.read()
    if first is None:
        raise SystemExit("첫 프레임을 못 읽음")
    h, w = first.shape[:2]
    fw, fh = g.get("frame_size", [w, h])
    if (fw, fh) != (w, h):
        poly *= np.array([w / fw, h / fh], np.float32)
        print(f"골문 좌표를 {fw}x{fh} → {w}x{h}로 맞춤")
    poly_i = poly.astype(np.int32)

    judge = GoalJudge(poly, a)
    sender = Sender(a.server)
    writer = None
    if a.save:
        writer = cv2.VideoWriter(a.save, cv2.VideoWriter_fourcc(*"mp4v"), src.fps, (w, h))
    events, n_frames, n_det = [], 0, 0
    banner_until = -1
    proc_t, proc_n = time.time(), 0
    proc_fps = 0.0

    frame, t = first, t_first
    try:
        while frame is not None:
            r = model.predict(frame, classes=[ball_cls], conf=a.conf, imgsz=a.imgsz,
                              device=device, verbose=False)[0]
            ball = None
            if len(r.boxes):
                k = int(r.boxes.conf.argmax())
                x1, y1, x2, y2 = r.boxes.xyxy[k].tolist()
                ball = ((x1 + x2) / 2, (y1 + y2) / 2, max(x2 - x1, y2 - y1), float(r.boxes.conf[k]))
                n_det += 1
            n_frames += 1

            c = judge.update(t, ball)
            if c is not None:
                payload = {"goal_side": a.goal_side, "ts": round(t, 2), "conf": round(c, 3), "source": a.source}
                events.append(payload)
                banner_until = t + 2
                print(f"GOAL  t={t:7.2f}s  conf={c:.2f}")
                sender.send(payload)

            # 처리 속도
            proc_n += 1
            if time.time() - proc_t >= 1.0:
                proc_fps = proc_n / (time.time() - proc_t)
                proc_t, proc_n = time.time(), 0

            if not a.no_show or writer:
                vis = frame
                cv2.polylines(vis, [poly_i], True, (0, 255, 0), 2)
                if ball is not None:
                    cx, cy, size, conf = ball
                    inside = cv2.pointPolygonTest(poly, (float(cx), float(cy)), False) >= 0
                    col = (0, 0, 255) if inside else (255, 200, 0)
                    hs = int(size / 2)
                    cv2.rectangle(vis, (int(cx) - hs, int(cy) - hs), (int(cx) + hs, int(cy) + hs), col, 2)
                    cv2.putText(vis, f"{conf:.2f}", (int(cx) - hs, int(cy) - hs - 6),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
                if t < banner_until:
                    cv2.putText(vis, "GOAL!", (40, 100), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 255), 8)
                bar = int(200 * judge.progress())
                cv2.rectangle(vis, (20, h - 60), (220, h - 45), (255, 255, 255), 1)
                cv2.rectangle(vis, (20, h - 60), (20 + bar, h - 45), (0, 0, 255), -1)
                cv2.putText(vis, f"t={t:6.2f}s  goals={len(events)}  armed={judge.armed}  proc={proc_fps:4.1f}fps",
                            (20, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                if writer:
                    writer.write(vis)
                if not a.no_show:
                    dw = min(a.display_width, w)
                    cv2.imshow("stadium detector (q: quit)", cv2.resize(vis, (dw, int(h * dw / w))))
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break

            frame, t = src.read()
    except KeyboardInterrupt:
        pass
    finally:
        src.release()
        if writer:
            writer.release()
        if not a.no_show:
            cv2.destroyAllWindows()

    if a.events_out:
        os.makedirs(os.path.dirname(os.path.abspath(a.events_out)), exist_ok=True)
        with open(a.events_out, "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["ts", "conf"])
            for e in events:
                wr.writerow([e["ts"], e["conf"]])
        print(f"골 시각 저장: {a.events_out}")

    print(f"\n처리 프레임 {n_frames} (건너뜀 {src.skipped}) | 공 검출 {n_det / max(n_frames, 1):.1%} | 골 {len(events)}회")
    for e in events:
        print(f"  {e['ts']:7.2f}s  conf={e['conf']}")


if __name__ == "__main__":
    main()
