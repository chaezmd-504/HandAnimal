"""
mapping_optimizer.py  —  오케스트레이터
-----------------------------------------
논문 HandAvatar(CHI 2023) 2단계 최적화 파이프라인.

이 파일은 각 수식 모듈을 순서대로 호출하는 오케스트레이터 역할만 한다.
수식 구현은 각 전용 모듈에 있다:

    constants.py      — HAND_DOFS, 가중치, 손가락 인덱스        (§0)
    compute_c.py      — C 행렬 (ROM 겹침)                       (§3)
    compute_f.py      — F 점수 (RULA, IFA, HE, FA)              (§4)
    compute_s.py      — S 점수 + S̄ 평균 행렬                    (§5)
    compute_q.py      — Q 목적함수                              (§6)
    ilp_solver.py     — ILP Step 1 + F̄ 벡터                    (§7)
    reference_pose.py — Step 2 (g*, p*) + build_result          (§8)
    mapping_engine.py — 런타임 실시간 변환                      (§9)

사용 예:
    from mapping.mapping_optimizer import MappingOptimizer
    opt = MappingOptimizer("data/animal_skeletons/spider.json",
                           "data/hand_poses/poses_10k.npy")
    result = opt.run_bilateral(
        avatar_poses_path="data/animal_skeletons/spider_poses.json",
        poses_sub_path="data/hand_poses/poses_100_comfortable.npy",
    )
    opt.save(result, "data/mappings/spider_mapping.json")
"""

from __future__ import annotations

import json
import os
import warnings
from typing import Any, Optional

import numpy as np

from .constants import N_HAND, _DOF_MINS, _DOF_MAXS
from .compute_c import compute_C_matrix
from .compute_f import compute_F
from .compute_s import compute_S_bar
from .ilp_solver import compute_F_bar, solve_ilp
from .reference_pose import optimize_reference_pose, optimize_reference_pose_sum, optimize_reference_pose_paper, build_result


class MappingOptimizer:
    """손 DOF → 동물 관절 매핑 최적화기.

    논문 2단계 파이프라인:
      Step 1 — ILP 로 매핑 M 최적화           (ilp_solver.py)
      Step 2 — G_sub × P 순회로 (g*, p*) 최적화  (reference_pose.py)
    """

    def __init__(self, skeleton_path: str, poses_path: str):
        with open(skeleton_path, encoding="utf-8") as f:
            self.skeleton: dict = json.load(f)

        self.joints: list[dict]        = self.skeleton["joints"]
        self.chains: list[list[str]]   = self.skeleton.get("chains", [])
        self.n_animal: int             = len(self.joints)
        self._joint_idx: dict[str, int] = {j["id"]: i for i, j in enumerate(self.joints)}

        if os.path.exists(poses_path):
            self.poses: np.ndarray = np.load(poses_path)
            if self.poses.shape[1] != N_HAND:
                raise ValueError(
                    f"poses shape {self.poses.shape} 와 HAND_DOFS({N_HAND}) 불일치"
                )
        else:
            warnings.warn(
                f"포즈 파일 없음: {poses_path}. 합성 데이터 500개로 대체합니다. "
                "먼저 generate_hand_poses.py 를 실행하세요.",
                stacklevel=2,
            )
            rng = np.random.default_rng(0)
            self.poses = _DOF_MINS + rng.beta(2, 2, (500, N_HAND)) * (_DOF_MAXS - _DOF_MINS)

        self.n_poses: int = self.poses.shape[0]

    # ──────────────────────────────────────────────────────────
    # 공개 API
    # ──────────────────────────────────────────────────────────

    def run(
        self,
        avatar_poses_path: Optional[str] = None,
        poses_sub_path: Optional[str] = None,
        ref_method: str = "single",
        beta: float = 0.0,
    ) -> dict[str, Any]:
        """단일 손 최적화 실행.

        beta: S̄ 분산 페널티 (0=논문 원래 수식, >0=wrist 억제 휴리스틱).
              동물마다 필요 여부가 다르므로 generate_mappings.py에서 지정.
        """
        print(f"[MappingOptimizer] {self.skeleton['animal_name']} 최적화 시작 (단일)")

        G_sub = self._load_G_sub(poses_sub_path)
        P     = self._load_avatar_poses(avatar_poses_path)
        print(f"  동물 관절: {self.n_animal}, G_sub: {len(G_sub)}, P: {len(P)}")

        result = self._run_pipeline(G_sub, P, self.joints, self.chains, ref_method=ref_method, beta=beta)
        result["mode"] = "unilateral"
        self._print_summary(result)
        return result

    def run_bilateral(
        self,
        avatar_poses_path: Optional[str] = None,
        poses_sub_path: Optional[str] = None,
        ref_method: str = "single",
        beta: float = 0.0,
    ) -> dict[str, Any]:
        """양손 매핑 실행. 관절 ID 의 _L/_R 패턴으로 좌우 분리 후 독립 ILP.

        beta: S̄ 분산 페널티 (0=논문 원래 수식, >0=wrist 억제 휴리스틱).
        """
        G_sub = self._load_G_sub(poses_sub_path)
        P     = self._load_avatar_poses(avatar_poses_path)

        left_joints, right_joints, center_joints = [], [], []
        for j in self.joints:
            jid = j["id"].lower()
            if "_l" in jid or jid.startswith("l_") or "left" in jid:
                left_joints.append(j)
            elif "_r" in jid or jid.startswith("r_") or "right" in jid:
                right_joints.append(j)
            else:
                center_joints.append(j)

        mid = len(center_joints) // 2
        left_joints  = left_joints  + center_joints[:mid]
        right_joints = right_joints + center_joints[mid:]

        # 제약 4: 양손 균형 경고 (하드 제약 미구현)
        n_total = len(left_joints) + len(right_joints)
        n_left, n_right = len(left_joints), len(right_joints)
        if abs(n_left - n_right) > n_total / 3:
            warnings.warn(
                f"[양손 균형 위반] 왼손 {n_left}, 오른손 {n_right} "
                f"(허용 차이: {n_total / 3:.1f}). 관절 ID _L/_R 확인하세요.",
                stacklevel=2,
            )
        else:
            print(f"  [양손 균형 OK] 왼손 {n_left}, 오른손 {n_right}")

        subset_ids   = {j["id"] for j in left_joints + right_joints}
        left_chains  = [[jid for jid in c if jid in {j["id"] for j in left_joints}]
                        for c in self.chains]
        right_chains = [[jid for jid in c if jid in {j["id"] for j in right_joints}]
                        for c in self.chains]
        left_chains  = [c for c in left_chains  if len(c) >= 2]
        right_chains = [c for c in right_chains if len(c) >= 2]

        print(f"[MappingOptimizer] {self.skeleton['animal_name']} (left) - {n_left}관절")
        left_res  = self._run_pipeline(G_sub, P, left_joints,  left_chains,  ref_method=ref_method, beta=beta)
        print(f"[MappingOptimizer] {self.skeleton['animal_name']} (right) - {n_right}관절")
        right_res = self._run_pipeline(G_sub, P, right_joints, right_chains, ref_method=ref_method, beta=beta)
        self._print_summary(left_res)
        self._print_summary(right_res)

        merged_mapping: dict[str, dict] = {}
        for k, v in left_res["mapping"].items():
            merged_mapping[k] = {**v, "hand": "left"}
        for k, v in right_res["mapping"].items():
            merged_mapping[k] = {**v, "hand": "right"}

        q_avg = round((left_res["Q_score_reference"] + right_res["Q_score_reference"]) / 2, 4)

        return {
            "animal":   self.skeleton["animal_name"],
            "mode":     "bilateral",
            "mapping":  merged_mapping,
            "reference_pose_H": {
                "left":  left_res["reference_pose_H"],
                "right": right_res["reference_pose_H"],
            },
            "reference_pose_A": {
                **left_res["reference_pose_A"],
                **right_res["reference_pose_A"],
            },
            "Q_score_reference": q_avg,
            "bilateral_balance": {
                "n_left":   n_left,
                "n_right":  n_right,
                "balanced": abs(n_left - n_right) <= n_total / 3,
            },
        }

    def save(self, result: dict, out_path: str):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[OK] 매핑 저장: {out_path}")

    # ──────────────────────────────────────────────────────────
    # 내부: 파이프라인 실행 (수식 모듈 순서대로 호출)
    # ──────────────────────────────────────────────────────────

    def _run_pipeline(
        self,
        G_sub: np.ndarray,
        P: list[dict],
        joints: list[dict],
        chains: list[list[str]],
        ref_method: str = "single",
        beta: float = 0.0,
    ) -> dict[str, Any]:
        """
        Step 1 → Step 2 → build_result 순서로 파이프라인 실행.

        §3  compute_C_matrix  → C
        §5  compute_S_bar     → S̄
        §7  compute_F_bar     → F̄
        §7  solve_ilp         → assignment
        §8  optimize_reference_pose(_sum) → (g*, p*, Q_best)
        §8  build_result      → result dict

        ref_method: "single" (기본, 단일 argmax) | "sum" | "paper"
        beta:       S̄ 분산 페널티 (0=논문 원래 수식, >0=wrist 억제 휴리스틱)
        """
        print("  [§3] C (제어 점수) 계산...")
        C = compute_C_matrix(joints)

        print("  [§5] S̄ (평균 구조 유사성) 계산...")
        S_bar = compute_S_bar(G_sub, P, joints, beta=beta)

        print("  [§7] F̄ (per-DOF 편안함) 계산...")
        F_bar = compute_F_bar(G_sub)

        print("  [§7] ILP 풀기...")
        assignment = solve_ilp(C, S_bar, F_bar, joints, chains)
        self._verify_assignment(assignment, joints)

        print(f"  [§8] 기준 포즈 쌍 최적화 (method={ref_method})...")
        if ref_method == "sum":
            g_star, p_star, q_best = optimize_reference_pose_sum(assignment, G_sub, P, C, joints)
        elif ref_method == "paper":
            g_star, p_star, q_best = optimize_reference_pose_paper(assignment, G_sub, P, C, joints)
        else:
            g_star, p_star, q_best = optimize_reference_pose(assignment, G_sub, P, C, joints)

        return build_result(
            assignment, g_star, p_star, q_best,
            C, S_bar, F_bar, joints,
            self.skeleton["animal_name"],
        )

    # ──────────────────────────────────────────────────────────
    # 내부: 데이터 로드
    # ──────────────────────────────────────────────────────────

    def _load_G_sub(self, poses_sub_path: Optional[str]) -> np.ndarray:
        if poses_sub_path and os.path.exists(poses_sub_path):
            G_sub = np.load(poses_sub_path)
            print(f"  [G_sub] {G_sub.shape[0]}개 ← {poses_sub_path}")
            return G_sub
        return self._subsample_poses(100)

    def _subsample_poses(self, k: int = 100) -> np.ndarray:
        """F 상위 k개 서브샘플링. poses_100_comfortable.npy 없을 때 폴백."""
        if self.n_poses <= k:
            return self.poses
        print(f"  [G 서브샘플링] {self.n_poses}개 → 상위 {k}개 선택 중...")
        F_all = np.array([compute_F(self.poses[i]) for i in range(self.n_poses)])
        top_idx = np.argsort(F_all)[-k:]
        return self.poses[top_idx]

    def _load_avatar_poses(self, path: Optional[str]) -> list[dict]:
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                P = json.load(f)
            print(f"  [P 로드] {len(P)}개 아바타 포즈 ← {path}")
            return P
        warnings.warn(
            f"아바타 포즈 파일 없음: {path}. "
            "ROM 기반 플레이스홀더 포즈로 대체합니다.",
            stacklevel=3,
        )
        return [
            {aj["id"]: float(aj["min_angle"] + f * (aj["max_angle"] - aj["min_angle"]))
             for aj in self.joints}
            for f in [0.0, 0.5, 1.0]
        ]

    # ──────────────────────────────────────────────────────────
    # 내부: 검증 / 출력
    # ──────────────────────────────────────────────────────────

    def _verify_assignment(self, assignment: np.ndarray, joints: list[dict]):
        used   = list(assignment)
        unique = set(used)
        if len(used) != len(unique):
            warnings.warn(
                f"[검증 경고] 손 DOF 중복 사용: {len(used) - len(unique)}개",
                stacklevel=3,
            )
        else:
            print(f"  [검증 OK] 중복 없음 — {len(unique)}개 DOF 사용")

    def _print_summary(self, result: dict):
        animal = result["animal"]
        q_ref  = result.get("Q_score_reference", "?")
        print(f"\n  === {animal} 매핑 결과 (Q_ref={q_ref}) ===")
        print(f"  {'동물 관절':28s}  {'손 DOF':15s}  {'scale':6s}  Q")
        print(f"  {'-'*65}")
        for aj_id, info in result["mapping"].items():
            print(
                f"  {aj_id:28s}  {info['hand_dof_name']:15s}  "
                f"{info['scale_factor']:6.3f}  {info['Q_score']:+.3f}"
            )
        print()
