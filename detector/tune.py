"""
판정 설정 튜닝 — detect_dump.py 결과로 run.py와 똑같은 판정을 설정별로 다시 돌려 채점.
모델을 다시 안 돌려서 설정 수십 개를 몇 초 만에 비교할 수 있음.

  python detector/tune.py --dets eval/results/test_1_dets.csv --goal detector/goal_left.json \
                          --true 12,27,39,58,75,95,107

출력
  1) 실제 골마다 진단: 골문 안에서 공이 잡혔나? (검출 실패 vs 판정 조건 미달 구분)
  2) 설정 조합별 정탐/오탐 표 (정탐 많고 오탐 적은 순)
"""
import argparse
import csv
import itertools
import json
import os
import sys
from types import SimpleNamespace

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import GoalJudge  # run.py와 같은 판정 로직

ap = argparse.ArgumentParser()
ap.add_argument("--dets", required=True)
ap.add_argument("--goal", required=True)
ap.add_argument("--true", required=True, help="실제 골 시각(초), 쉼표로")
ap.add_argument("--tol", type=float, default=2.0)
ap.add_argument("--conf", default="0.25,0.35")
ap.add_argument("--min-in", default="0,0.03,0.07")
ap.add_argument("--gap", default="0.2,0.4")
ap.add_argument("--rearm", default="0.5,1,2,3")
ap.add_argument("--cooldown", default="3")
ap.add_argument("--ball-min", default="0")
ap.add_argument("--ball-max", default="0")
ap.add_argument("--top", type=int, default=15)
a = ap.parse_args()

fl = lambda s: [float(x) for x in s.split(",") if x.strip() != ""]
TRUE = sorted(fl(a.true))

rows = list(csv.DictReader(open(a.dets)))
fw, fh = int(rows[0]["w"]), int(rows[0]["h"])
g = json.load(open(a.goal))
poly = np.array(g["polygon"], np.float32)
gw, gh = g.get("frame_size", [fw, fh])
if (gw, gh) != (fw, fh):
    poly *= np.array([fw / gw, fh / gh], np.float32)

dets = []
for r in rows:
    t = float(r["t"])
    if r["conf"]:
        dets.append((t, (float(r["cx"]), float(r["cy"]), float(r["size"]), float(r["conf"]))))
    else:
        dets.append((t, None))


def inside(b):
    return cv2.pointPolygonTest(poly, (b[0], b[1]), False) >= 0


# ---------- 1) 실제 골 진단
print("=== 실제 골 진단 (골 시각 ±1.5초, conf≥0.25) ===")
for gt in TRUE:
    win = [(t, b) for t, b in dets if abs(t - gt) <= 1.5 and b is not None and b[3] >= 0.25]
    ins = [(t, b) for t, b in win if inside(b)]
    if not win:
        why = "❌ 공 자체가 안 잡힘 → 검출 문제 (블러·가림)"
    elif not ins:
        why = "⚠️ 공은 잡혔는데 골문 밖 → 다각형 범위 문제"
    else:
        span = ins[-1][0] - ins[0][0]
        sizes = [b[2] for _, b in ins]
        why = (f"✅ 골문 안 {len(ins)}프레임 ({span:.2f}초 구간), "
               f"공 크기 {min(sizes):.0f}~{max(sizes):.0f}px → 판정 설정으로 잡을 수 있음")
    print(f"{gt:7.2f}s  {why}")


# ---------- 2) 설정 조합 채점
def score(events):
    used = set()
    for gt in TRUE:
        hit = next((e for e in events if abs(e - gt) <= a.tol and e not in used), None)
        if hit is not None:
            used.add(hit)
    return len(used), len(events) - len(used), [e for e in events if e not in used]


results = []
grid = itertools.product(fl(a.conf), fl(a.min_in), fl(a.gap), fl(a.rearm),
                         fl(a.cooldown), fl(a.ball_min), fl(a.ball_max))
for conf, mi, gap, rearm, cd, bmin, bmax in grid:
    args = SimpleNamespace(min_in=mi, gap=gap, rearm=rearm, cooldown=cd, ball_min=bmin, ball_max=bmax)
    j = GoalJudge(poly, args)
    ev = []
    for t, b in dets:
        bb = b if (b is not None and b[3] >= conf) else None
        if j.update(t, bb) is not None:
            ev.append(t)
    tp, fp, fps_ = score(ev)
    results.append(dict(conf=conf, min_in=mi, gap=gap, rearm=rearm, cooldown=cd,
                        ball_min=bmin, ball_max=bmax, tp=tp, fp=fp,
                        fp_times=" ".join(f"{x:.1f}" for x in fps_)))

results.sort(key=lambda r: (-r["tp"], r["fp"], -r["min_in"], -r["rearm"]))
print(f"\n=== 설정별 결과 (실제 골 {len(TRUE)}개, 상위 {a.top}개) ===")
hdr = f"{'conf':>5} {'min_in':>6} {'gap':>4} {'rearm':>5} {'cd':>3} {'bmin':>4} {'bmax':>4} | {'정탐':>5} {'오탐':>3}  오탐 시각"
print(hdr)
print("-" * len(hdr))
for r in results[:a.top]:
    print(f"{r['conf']:5.2f} {r['min_in']:6.2f} {r['gap']:4.1f} {r['rearm']:5.1f} {r['cooldown']:3.0f} "
          f"{r['ball_min']:4.0f} {r['ball_max']:4.0f} | {r['tp']:>2}/{len(TRUE):<2} {r['fp']:>3}  {r['fp_times']}")

# 정탐을 조금 포기하면 오탐이 얼마나 줄어드나 (오탐 수별 최선)
print("\n=== 오탐 수별 최선 (정탐 vs 오탐 트레이드오프) ===")
seen = {}
for r in sorted(results, key=lambda r: (r["fp"], -r["tp"])):
    if r["fp"] not in seen or r["tp"] > seen[r["fp"]]["tp"]:
        seen[r["fp"]] = r
prev_tp = -1
for fp_n in sorted(seen):
    r = seen[fp_n]
    if r["tp"] <= prev_tp:
        continue
    prev_tp = r["tp"]
    opt = (f"--conf {r['conf']} --min-in {r['min_in']} --gap {r['gap']} --rearm {r['rearm']} --cooldown {r['cooldown']:.0f}"
           + (f" --ball-min {r['ball_min']:.0f}" if r['ball_min'] else "")
           + (f" --ball-max {r['ball_max']:.0f}" if r['ball_max'] else ""))
    print(f"  오탐 {fp_n:>2} → 정탐 {r['tp']}/{len(TRUE)}   {opt}")

# 1위 설정의 오탐 장면: 그 순간 골문 안에서 잡힌 공의 크기·신뢰도
best = results[0]
if best["fp_times"]:
    print("\n=== 1위 설정의 오탐 장면 (직전 0.5초, 골문 안 검출) → 크기 필터·conf 참고 ===")
    for ft in map(float, best["fp_times"].split()):
        ins = [b for t, b in dets if ft - 0.5 <= t <= ft + 0.05 and b is not None
               and b[3] >= best["conf"] and inside(b)]
        if ins:
            print(f"  {ft:7.1f}s  {len(ins)}프레임, 크기 {min(b[2] for b in ins):.0f}~{max(b[2] for b in ins):.0f}px, "
                  f"conf {min(b[3] for b in ins):.2f}~{max(b[3] for b in ins):.2f}, "
                  f"위치 ({ins[-1][0]:.0f}, {ins[-1][1]:.0f})")
print(f"\n추천 → run.py에 붙일 옵션: --conf {best['conf']} --min-in {best['min_in']} --gap {best['gap']} "
      f"--rearm {best['rearm']} --cooldown {best['cooldown']:.0f}"
      + (f" --ball-min {best['ball_min']:.0f}" if best['ball_min'] else "")
      + (f" --ball-max {best['ball_max']:.0f}" if best['ball_max'] else ""))
