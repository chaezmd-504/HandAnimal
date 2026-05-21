"""
benchmark_k.py
--------------
G_sub 크기(K)에 따른 매핑 품질(Q score)과 실행 시간을 비교한다.

K = 100, 1000, 10000 세 가지 설정으로 spider 매핑을 실행하고
결과를 테이블로 출력한다.

실행:
    conda activate capstone_env
    python scripts/benchmark_k.py

사전 조건:
    python scripts/generate_hand_poses.py   # poses_10k.npy
    data/animal_skeletons/spider.json
    data/animal_skeletons/spider_poses.json
"""

import os
import sys
import time

# Windows cp949 환경에서 한글/특수문자 출력 보장
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PYTHON_DIR)

from mapping.mapping_optimizer import MappingOptimizer
from scripts.subsample_hand_poses import subsample

# ── 설정 ──────────────────────────────────────────────────────
K_VALUES      = [100, 1000, 10000]
ANIMAL        = "spider"  # 대표 동물 1종으로 비교
POSES_10K     = os.path.join(PYTHON_DIR, "data", "hand_poses", "poses_10k.npy")
SKELETON_PATH = os.path.join(PYTHON_DIR, "data", "animal_skeletons", f"{ANIMAL}.json")
AVATAR_POSES  = os.path.join(PYTHON_DIR, "data", "animal_skeletons", f"{ANIMAL}_poses.json")
DATA_DIR      = os.path.join(PYTHON_DIR, "data", "hand_poses")


def get_or_create_subsample(k: int) -> str:
    """K에 해당하는 subsample 파일 경로를 반환. 없으면 생성."""
    path = os.path.join(DATA_DIR, f"poses_{k}_comfortable.npy")
    if os.path.exists(path):
        print(f"[캐시] poses_{k}_comfortable.npy 이미 존재, 재사용합니다.")
    else:
        print(f"\n[생성] poses_{k}_comfortable.npy 생성 중...")
        subsample(k)
    return path


def run_mapping(sub_path: str) -> tuple[float, float]:
    """
    spider bilateral 매핑을 실행하고 (elapsed_sec, Q_score)를 반환한다.
    """
    avatar_poses_arg = AVATAR_POSES if os.path.exists(AVATAR_POSES) else None

    opt = MappingOptimizer(SKELETON_PATH, POSES_10K)

    t0 = time.perf_counter()
    result = opt.run_bilateral(
        avatar_poses_path=avatar_poses_arg,
        poses_sub_path=sub_path,
    )
    elapsed = time.perf_counter() - t0

    q_score = result.get("Q_score_reference", float("nan"))
    return elapsed, q_score


def main():
    # 사전 조건 확인
    for path, label in [(POSES_10K, "poses_10k.npy"), (SKELETON_PATH, "spider.json")]:
        if not os.path.exists(path):
            print(f"[ERROR] 필수 파일 없음: {path}")
            print(f"        ({label} 먼저 생성하세요)")
            sys.exit(1)

    if not os.path.exists(AVATAR_POSES):
        print(f"[WARN] spider_poses.json 없음 — ROM 기반 플레이스홀더 포즈 사용")

    print("=" * 60)
    print(f"  benchmark_k.py  -  G_sub 크기별 매핑 품질/시간 비교")
    print(f"  동물: {ANIMAL}  |  K = {K_VALUES}")
    print("=" * 60)

    # ── Step 1. 각 K의 subsample 파일 준비 ───────────────────
    sub_paths: dict[int, str] = {}
    for k in K_VALUES:
        sub_paths[k] = get_or_create_subsample(k)

    # ── Step 2. 각 K로 매핑 실행 ─────────────────────────────
    results: list[dict] = []
    for k in K_VALUES:
        print(f"\n{'─'*50}")
        print(f"  K = {k:,} 실행 중...")
        print(f"{'─'*50}")
        elapsed, q = run_mapping(sub_paths[k])
        results.append({"k": k, "elapsed": elapsed, "q_score": q})
        print(f"  → 완료: {elapsed:.1f}초, Q = {q:.4f}")

    # ── Step 3. 비교 테이블 출력 ─────────────────────────────
    base_q = results[0]["q_score"]   # K=100 기준

    print(f"\n{'='*60}")
    print(f"  {'K':>8}  {'시간(초)':>10}  {'Q score':>10}  {'Q 개선':>10}")
    print(f"  {'─'*8}  {'─'*10}  {'─'*10}  {'─'*10}")
    for r in results:
        q_diff = r["q_score"] - base_q
        q_diff_str = f"+{q_diff:.4f}" if q_diff >= 0 else f"{q_diff:.4f}"
        baseline_marker = "  ← 기준" if r["k"] == 100 else ""
        print(f"  {r['k']:>8,}  {r['elapsed']:>10.1f}  {r['q_score']:>10.4f}  {q_diff_str:>10}{baseline_marker}")
    print(f"{'='*60}")

    # ── Step 4. 코멘트 ────────────────────────────────────────
    best = max(results, key=lambda x: x["q_score"])
    fastest = min(results, key=lambda x: x["elapsed"])
    print(f"\n  최고 Q score: K = {best['k']:,}  (Q = {best['q_score']:.4f})")
    print(f"  최단 시간:   K = {fastest['k']:,}  ({fastest['elapsed']:.1f}초)")

    time_100  = results[0]["elapsed"]
    time_1000 = results[1]["elapsed"]
    time_10k  = results[2]["elapsed"]
    print(f"\n  시간 배율 (K=100 대비):")
    print(f"    K=1000  : ×{time_1000/time_100:.1f}")
    print(f"    K=10000 : ×{time_10k/time_100:.1f}")

    q_100  = results[0]["q_score"]
    q_1000 = results[1]["q_score"]
    q_10k  = results[2]["q_score"]
    if q_100 != 0:
        print(f"\n  Q 개선율 (K=100 대비):")
        print(f"    K=1000  : {(q_1000-q_100)/abs(q_100)*100:+.1f}%")
        print(f"    K=10000 : {(q_10k-q_100)/abs(q_100)*100:+.1f}%")

    print()


if __name__ == "__main__":
    main()
