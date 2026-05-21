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
import re
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
        self.skeleton_path = skeleton_path   # side_override.json 경로 계산용
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
        """양손 매핑 실행.

        실행 순서:
          1) right ILP 먼저 실행 → 손가락 배정 추출
          2) left ILP 실행 시 대칭 제약(forced_fingers) 적용
             → l_leg_i 와 r_leg_i 가 반드시 같은 손가락에 배정됨

        side_override.json 지원:
          python/data/animal_skeletons/{animal}_side_override.json 이 있으면
          해당 관절의 좌우 배정을 오버라이드한다.
          (Unity ModelReviewWindow 에서 생성)

        beta: S̄ 분산 페널티 (0=논문 원래 수식, >0=wrist 억제 휴리스틱).
        """
        G_sub = self._load_G_sub(poses_sub_path)
        P     = self._load_avatar_poses(avatar_poses_path)

        # ── side_override.json 로드 ──────────────────────────────
        skel_dir      = os.path.dirname(self.skeleton_path)
        animal_name   = self.skeleton["animal_name"]
        override_path = os.path.join(skel_dir, f"{animal_name}_side_override.json")
        side_overrides: dict[str, str] = {}
        if os.path.exists(override_path):
            with open(override_path, encoding="utf-8") as f:
                side_overrides = json.load(f)
            print(f"  [side_override] {len(side_overrides)}개 관절 좌우 오버라이드 적용")

        # ── 좌/우/중앙 분리 ──────────────────────────────────────
        left_joints, right_joints, center_joints = [], [], []
        for j in self.joints:
            jid     = j["id"]
            jid_low = jid.lower()

            # side_override 우선
            override = side_overrides.get(jid)
            if override == "left":
                left_joints.append(j); continue
            elif override == "right":
                right_joints.append(j); continue
            elif override == "center":
                center_joints.append(j); continue

            # 자동 감지: l_ 접두사 우선 (r_leg 에 _l 이 포함되는 오탐 방지)
            if jid_low.startswith("l_") or jid_low.endswith("_l") or "left" in jid_low:
                left_joints.append(j)
            elif jid_low.startswith("r_") or jid_low.endswith("_r") or "right" in jid_low:
                right_joints.append(j)
            else:
                center_joints.append(j)

        mid = len(center_joints) // 2
        left_joints  = left_joints  + center_joints[:mid]
        right_joints = right_joints + center_joints[mid:]

        n_total = len(left_joints) + len(right_joints)
        n_left, n_right = len(left_joints), len(right_joints)
        if abs(n_left - n_right) > n_total / 3:
            warnings.warn(
                f"[양손 균형 위반] 왼손 {n_left}, 오른손 {n_right} "
                f"(허용 차이: {n_total / 3:.1f}). 관절 ID 또는 side_override 확인하세요.",
                stacklevel=2,
            )
        else:
            print(f"  [양손 균형 OK] 왼손 {n_left}, 오른손 {n_right}")

        left_joint_ids  = {j["id"] for j in left_joints}
        right_joint_ids = {j["id"] for j in right_joints}
        left_chains  = [c for c in ([
            [jid for jid in ch if jid in left_joint_ids]  for ch in self.chains
        ]) if len(c) >= 2]
        right_chains = [c for c in ([
            [jid for jid in ch if jid in right_joint_ids] for ch in self.chains
        ]) if len(c) >= 2]

        # ── 체인 루트 손가락 자동 감지 ──────────────────────────
        # 규칙: l_/r_ 접두사 제거 → trailing _NNN 숫자 파싱
        #   leg (no suffix) → idx=0 → index
        #   leg_001         → idx=1 → middle
        #   leg_002         → idx=2 → ring
        #   leg_003         → idx=3 → pinky
        _FINGER_ORDER = ["index", "middle", "ring", "pinky"]

        def _auto_finger(joint_id: str) -> str | None:
            base = joint_id
            for pfx in ("l_", "r_"):
                if joint_id.lower().startswith(pfx):
                    base = joint_id[len(pfx):]
                    break
            m = re.search(r"_(\d+)$", base)
            idx = int(m.group(1)) if m else 0
            return _FINGER_ORDER[idx] if idx < len(_FINGER_ORDER) else None

        # 체인 루트 ID 집합
        chain_root_ids = {ch[0] for ch in self.chains if ch}

        # 자동 감지 결과
        auto_fingers: dict[str, str] = {}
        for ch in self.chains:
            if not ch:
                continue
            root = ch[0]
            f = _auto_finger(root)
            if f:
                auto_fingers[root] = f

        # finger_override.json (Unity ModelReviewWindow 저장본) 로드
        finger_override_path = os.path.join(skel_dir, f"{animal_name}_finger_override.json")
        finger_overrides: dict[str, str] = {}
        if os.path.exists(finger_override_path):
            with open(finger_override_path, encoding="utf-8") as f:
                finger_overrides = json.load(f)
            print(f"  [finger_override] {len(finger_overrides)}개 관절 손가락 오버라이드 적용")

        # 최종 chain-root 손가락 배정: override > auto
        effective_fingers: dict[str, str] = {**auto_fingers, **finger_overrides}
        if effective_fingers:
            print(f"  [chain_fingers] 체인 루트 {len(effective_fingers)}개 손가락 배정:")
            for jid, finger in effective_fingers.items():
                src = "override" if jid in finger_overrides else "auto"
                print(f"    {jid} → {finger} ({src})")

        # ── Step 1: right ILP ────────────────────────────────────
        right_forced: dict[str, str] = {
            jid: finger
            for jid, finger in effective_fingers.items()
            if jid in right_joint_ids
        }
        print(f"[MappingOptimizer] {animal_name} (right) - {n_right}관절")
        right_res = self._run_pipeline(
            G_sub, P, right_joints, right_chains,
            ref_method=ref_method, beta=beta,
            forced_fingers=right_forced or None,
        )

        # right 결과에서 손가락 배정 추출 (bilateral 대칭용)
        right_finger_map: dict[str, str] = {
            jid: info["hand_dof_name"].split("_")[0]
            for jid, info in right_res["mapping"].items()
        }

        # ── left forced_fingers: chain auto > bilateral 대칭 ─────
        left_forced: dict[str, str] = {
            jid: finger
            for jid, finger in effective_fingers.items()
            if jid in left_joint_ids
        }
        for j in left_joints:
            jid = j["id"]
            if jid in left_forced:
                continue
            mirror = None
            if jid.startswith("l_"):
                mirror = "r_" + jid[2:]
            elif jid.endswith("_l"):
                mirror = jid[:-2] + "_r"
            if mirror and mirror in right_finger_map:
                left_forced[jid] = right_finger_map[mirror]

        if left_forced:
            print(f"  [대칭/고정] {len(left_forced)}개 관절 손가락 고정:")
            for jid, finger in left_forced.items():
                src = "auto" if jid in effective_fingers else \
                      (("r_" + jid[2:]) if jid.startswith("l_") else (jid[:-2] + "_r"))
                print(f"    {jid} → {finger}  (← {src})")

        # ── Step 2: left ILP ─────────────────────────────────────
        print(f"[MappingOptimizer] {animal_name} (left) - {n_left}관절")
        left_res = self._run_pipeline(
            G_sub, P, left_joints, left_chains,
            ref_method=ref_method, beta=beta,
            forced_fingers=left_forced or None,
        )

        self._print_summary(right_res)
        self._print_summary(left_res)

        merged_mapping: dict[str, dict] = {}
        for k, v in left_res["mapping"].items():
            merged_mapping[k] = {**v, "hand": "left"}
        for k, v in right_res["mapping"].items():
            merged_mapping[k] = {**v, "hand": "right"}

        q_avg = round((left_res["Q_score_reference"] + right_res["Q_score_reference"]) / 2, 4)

        # ── g* 평균화 ────────────────────────────────────────────
        # left / right Step 2가 독립적으로 g*를 찾으므로 같은 DOF의 값이
        # 달라질 수 있음. 캘리브레이션 시 사용자는 양손을 동시에 같은 포즈로
        # 들어야 하므로, 공통 DOF는 평균값으로 통일한다.
        g_left  = left_res["reference_pose_H"]
        g_right = right_res["reference_pose_H"]
        all_dofs = set(g_left) | set(g_right)
        g_avg = {
            dof: round((g_left.get(dof, g_right.get(dof, 0.0)) +
                         g_right.get(dof, g_left.get(dof, 0.0))) / 2, 2)
            for dof in all_dofs
        }

        # 변경된 DOF 출력
        changed = [
            (dof, g_left.get(dof), g_right.get(dof), g_avg[dof])
            for dof in sorted(all_dofs)
            if dof in g_left and dof in g_right
            and abs(g_left[dof] - g_right[dof]) > 5.0
        ]
        if changed:
            print(f"\n  [g* 평균화] left/right 차이 큰 DOF ({len(changed)}개):")
            for dof, lv, rv, av in changed:
                print(f"    {dof:<16}  left={lv:.1f}°  right={rv:.1f}°  → avg={av:.1f}°")

        return {
            "animal":   self.skeleton["animal_name"],
            "mode":     "bilateral",
            "mapping":  merged_mapping,
            "reference_pose_H": {
                "left":  g_avg,
                "right": g_avg,
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
        forced_fingers: Optional[dict[str, str]] = None,
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
        assignment = solve_ilp(C, S_bar, F_bar, joints, chains, forced_fingers=forced_fingers)
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
