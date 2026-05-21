"""
subsample_hand_poses.py
------------------------
poses_10k.npy에서 편안함 점수(F) 상위 K개를 선택하여
poses_{K}_comfortable.npy로 저장한다.

논문 근거:
  G 전체(10,000개)와 P(10~20개)의 조합 200,000번 계산은 수 분 소요.
  편안함이 낮은 포즈는 기준 포즈 후보에서 탈락하므로,
  F 상위 K개만 추려 계산량을 절감.

실행:
    conda activate capstone_env
    python scripts/subsample_hand_poses.py              # 기본 top_k=100
    python scripts/subsample_hand_poses.py --top_k 1000
    python scripts/subsample_hand_poses.py --top_k 10000

사전 조건:
    python scripts/generate_hand_poses.py  # poses_10k.npy 필요
"""

import argparse
import os
import sys

import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PYTHON_DIR)

from mapping.constants import HAND_DOFS, N_HAND
from mapping.compute_f import compute_F

POSES_PATH   = os.path.join(PYTHON_DIR, "data", "hand_poses", "poses_10k.npy")
NAMES_PATH   = os.path.join(PYTHON_DIR, "data", "hand_poses", "joint_names.txt")


def subsample(top_k: int) -> str:
    """poses_10k.npy에서 F 상위 top_k개를 선택해 저장하고 저장 경로를 반환한다."""
    out_path = os.path.join(PYTHON_DIR, "data", "hand_poses", f"poses_{top_k}_comfortable.npy")

    # ── 1. 10k 포즈 로드 ─────────────────────────────────────
    if not os.path.exists(POSES_PATH):
        print(f"[ERROR] 포즈 파일 없음: {POSES_PATH}")
        print("        generate_hand_poses.py 를 먼저 실행하세요.")
        sys.exit(1)

    poses = np.load(POSES_PATH)
    n = poses.shape[0]
    print(f"[로드] {n}개 포즈 ← {POSES_PATH}  shape={poses.shape}")

    if n < top_k:
        print(f"[WARN] 포즈 수({n})가 top_k({top_k})보다 적어 전체 저장합니다.")
        np.save(out_path, poses)
        print(f"[OK] 저장: {out_path}")
        return out_path

    # ── 2. 전체 10k 포즈에 대해 F 계산 ──────────────────────
    print(f"[F 계산] {n}개 포즈의 편안함 점수 계산 중...")
    F_all = np.zeros(n)
    for i in range(n):
        F_all[i] = compute_F(poses[i])
        if (i + 1) % 1000 == 0:
            print(f"  {i+1}/{n} 완료, F min={F_all[:i+1].min():.3f} max={F_all[:i+1].max():.3f}")

    # ── 4. 상위 top_k 선택 ───────────────────────────────────
    top_idx = np.argsort(F_all)[-top_k:][::-1]   # 내림차순 정렬
    G_sub   = poses[top_idx]

    f_selected = F_all[top_idx]
    print(f"\n[서브샘플 결과]")
    print(f"  선택 수:  {top_k}")
    print(f"  F 범위:   {f_selected.min():.4f} ~ {f_selected.max():.4f}")
    print(f"  F 평균:   {f_selected.mean():.4f}")
    print(f"  (전체 평균 F: {F_all.mean():.4f}  → 상위 집합이 더 높아야 함)")
    if top_k < n:  # 전체 선택 시(k==n)는 검증 불필요
        assert f_selected.min() >= F_all.mean() - 0.05, \
            "선택된 포즈의 최소 F가 전체 평균보다 너무 낮습니다."

    # ── 5. 저장 ──────────────────────────────────────────────
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    np.save(out_path, G_sub)
    print(f"\n[OK] 저장: {out_path}  shape={G_sub.shape}")

    # DOF 이름 파일도 확인
    if os.path.exists(NAMES_PATH):
        with open(NAMES_PATH) as f:
            names = f.read().strip().split("\n")
        print("\n[DOF 이름 확인]")
        for name, col_mean in zip(names, G_sub.mean(axis=0)):
            print(f"  {name:15s}: 평균 {col_mean:.1f}°")

    return out_path


def main():
    parser = argparse.ArgumentParser(description="poses_10k.npy에서 F 상위 K개 서브샘플 생성")
    parser.add_argument("--top_k", type=int, default=100,
                        help="선택할 편안한 포즈 수 (기본: 100)")
    args = parser.parse_args()
    subsample(args.top_k)


if __name__ == "__main__":
    main()
