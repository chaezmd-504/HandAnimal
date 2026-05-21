"""
benchmark_method.py
--------------------
기준 포즈 선택 방식(Step 2)에 따른 매핑 품질 비교.

  single : (g*, p*) = argmax_{g,p} Q_M(g, p)         ← 현재 구현
  sum    : g* = argmax_g Σ_{p∈P} Q_M(g, p)           ← 논문 방식
           p* = argmax_p Q_M(g*, p)

K=100 고정, spider bilateral 기준으로 두 방법을 비교한다.
K 영향도 함께 보려면 benchmark_k.py 참고.

실행:
    conda activate capstone_env
    python scripts/benchmark_method.py
"""

import os
import sys
import time

import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PYTHON_DIR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from mapping.mapping_optimizer import MappingOptimizer

ANIMAL        = "spider"
POSES_10K     = os.path.join(PYTHON_DIR, "data", "hand_poses", "poses_10k.npy")
SKELETON_PATH = os.path.join(PYTHON_DIR, "data", "animal_skeletons", f"{ANIMAL}.json")
AVATAR_POSES  = os.path.join(PYTHON_DIR, "data", "animal_skeletons", f"{ANIMAL}_poses.json")

# K별 subsample 파일 경로
K_VALUES   = [100, 1000]
SUB_PATHS  = {
    k: os.path.join(PYTHON_DIR, "data", "hand_poses", f"poses_{k}_comfortable.npy")
    for k in K_VALUES
}
METHODS    = ["single", "sum", "paper"]
METHOD_LABELS = {
    "single": "단일 argmax (현재)",
    "sum":    "Σ_p Q 합산",
    "paper":  "논문 Eq.11 (역산)",
}


def run_one(sub_path: str, method: str) -> tuple[float, float]:
    """(elapsed_sec, Q_score) 반환."""
    avatar_arg = AVATAR_POSES if os.path.exists(AVATAR_POSES) else None
    opt = MappingOptimizer(SKELETON_PATH, POSES_10K)
    t0 = time.perf_counter()
    result = opt.run_bilateral(
        avatar_poses_path=avatar_arg,
        poses_sub_path=sub_path,
        ref_method=method,
    )
    elapsed = time.perf_counter() - t0
    return elapsed, result.get("Q_score_reference", float("nan"))


def main():
    # 사전 조건 확인
    for path, label in [(POSES_10K, "poses_10k.npy"), (SKELETON_PATH, "spider.json")]:
        if not os.path.exists(path):
            print(f"[ERROR] 필수 파일 없음: {path} ({label} 먼저 생성하세요)")
            sys.exit(1)

    for k, path in SUB_PATHS.items():
        if not os.path.exists(path):
            print(f"[ERROR] {path} 없음 — benchmark_k.py 를 먼저 실행하세요.")
            sys.exit(1)

    if not os.path.exists(AVATAR_POSES):
        print(f"[WARN] spider_poses.json 없음 — ROM 기반 플레이스홀더 사용")

    print("=" * 65)
    print("  benchmark_method.py  -  Step 2 기준 포즈 선택 방식 비교")
    print(f"  동물: {ANIMAL}")
    print("=" * 65)

    # ── 실행 ─────────────────────────────────────────────────────
    results = []
    for k in K_VALUES:
        for method in METHODS:
            print(f"\n{'─'*55}")
            print(f"  K={k:,}  /  method={method} ({METHOD_LABELS[method]})")
            print(f"{'─'*55}")
            elapsed, q = run_one(SUB_PATHS[k], method)
            results.append({"k": k, "method": method, "elapsed": elapsed, "q": q})
            print(f"  -> 완료: {elapsed:.1f}초, Q = {q:.4f}")

    # ── 비교 테이블 ───────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  {'K':>6}  {'방식':^16}  {'시간(초)':>8}  {'Q score':>9}  {'Q 차이':>9}")
    print(f"  {'─'*6}  {'─'*16}  {'─'*8}  {'─'*9}  {'─'*9}")

    # K별로 single을 기준으로 차이 계산
    for k in K_VALUES:
        k_rows = [r for r in results if r["k"] == k]
        base_q = next(r["q"] for r in k_rows if r["method"] == "single")
        for r in k_rows:
            diff = r["q"] - base_q
            diff_str = f"+{diff:.4f}" if diff >= 0 else f"{diff:.4f}"
            base_mark = "  <- 기준" if r["method"] == "single" else ""
            label = METHOD_LABELS[r["method"]]
            print(f"  {r['k']:>6,}  {label:^16}  {r['elapsed']:>8.1f}  {r['q']:>9.4f}  {diff_str:>9}{base_mark}")
        print()

    print(f"{'='*65}")

    # ── 해석 ─────────────────────────────────────────────────────
    print("\n[해석]")
    print("  single: 특정 아바타 포즈 1개에 가장 잘 맞는 (g*,p*) 선택")
    print("  sum   : P 전체 포즈 평균 Q가 최대인 g* 선택 (단순 합산, 역산 없음)")
    print("  paper : 논문 Eq.11 -- 각 후보 (g,p)에서 모든 p' 역산 후 Q 합산")
    print()
    print("  Q score 차이 해석:")
    for k in K_VALUES:
        k_rows = [r for r in results if r["k"] == k]
        q_single = next((r["q"] for r in k_rows if r["method"] == "single"), float("nan"))
        q_sum    = next((r["q"] for r in k_rows if r["method"] == "sum"),    float("nan"))
        q_paper  = next((r["q"] for r in k_rows if r["method"] == "paper"),  float("nan"))
        scores   = {"single": q_single, "sum": q_sum, "paper": q_paper}
        winner   = max(scores, key=scores.get)
        print(f"    K={k:,}: 최고 방식 = {METHOD_LABELS[winner]} (Q={scores[winner]:.4f})")
        for m, q in scores.items():
            diff = q - q_single
            sign = "+" if diff >= 0 else ""
            print(f"      {METHOD_LABELS[m]:20s}: Q={q:.4f}  ({sign}{diff:.4f} vs single)")

    print()


if __name__ == "__main__":
    main()
