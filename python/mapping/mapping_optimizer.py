"""
mapping_optimizer.py
---------------------
논문 5단계 프로세스 구현:
  목적함수 Q = w_f × F  +  w_s × S  +  w_c × C
  가중치: w_f=-5, w_s=0.8, w_c=-0.01

  ILP (PuLP + CBC 솔버)로 최적 매핑 M을 결정하고,
  기준 자세 쌍 (H, A)까지 반환한다.

사용 예:
    from mapping.mapping_optimizer import MappingOptimizer
    opt = MappingOptimizer("data/animal_skeletons/spider.json",
                           "data/hand_poses/poses_10k.npy")
    result = opt.run()
    opt.save(result, "data/mappings/spider_mapping.json")
"""

from __future__ import annotations

import json
import os
import warnings
from typing import Any

import numpy as np

# ──────────────────────────────────────────────────────────────
# 손 DOF 정의 (generate_hand_poses.py 와 일치해야 함)
# ──────────────────────────────────────────────────────────────
HAND_DOFS: list[dict] = [
    {"name": "thumb_cmc",  "min": 0,  "max": 60,  "rest": 20, "finger": "thumb",  "segment": 0},
    {"name": "thumb_mcp",  "min": 0,  "max": 60,  "rest": 20, "finger": "thumb",  "segment": 1},
    {"name": "thumb_ip",   "min": 0,  "max": 80,  "rest": 15, "finger": "thumb",  "segment": 2},
    {"name": "index_mcp",  "min": 0,  "max": 90,  "rest": 10, "finger": "index",  "segment": 0},
    {"name": "index_pip",  "min": 0,  "max": 110, "rest": 10, "finger": "index",  "segment": 1},
    {"name": "index_dip",  "min": 0,  "max": 90,  "rest": 10, "finger": "index",  "segment": 2},
    {"name": "middle_mcp", "min": 0,  "max": 90,  "rest": 10, "finger": "middle", "segment": 0},
    {"name": "middle_pip", "min": 0,  "max": 110, "rest": 10, "finger": "middle", "segment": 1},
    {"name": "middle_dip", "min": 0,  "max": 90,  "rest": 10, "finger": "middle", "segment": 2},
    {"name": "ring_mcp",   "min": 0,  "max": 90,  "rest": 10, "finger": "ring",   "segment": 0},
    {"name": "ring_pip",   "min": 0,  "max": 110, "rest": 10, "finger": "ring",   "segment": 1},
    {"name": "ring_dip",   "min": 0,  "max": 90,  "rest": 10, "finger": "ring",   "segment": 2},
    {"name": "pinky_mcp",  "min": 0,  "max": 80,  "rest": 10, "finger": "pinky",  "segment": 0},
    {"name": "pinky_pip",  "min": 0,  "max": 100, "rest": 10, "finger": "pinky",  "segment": 1},
    {"name": "pinky_dip",  "min": 0,  "max": 80,  "rest": 10, "finger": "pinky",  "segment": 2},
]
N_HAND = len(HAND_DOFS)

# 논문 가중치
W_F = -5.0
W_S =  0.8
W_C = -0.01


class MappingOptimizer:
    """손 DOF → 동물 관절 매핑 최적화기."""

    def __init__(self, skeleton_path: str, poses_path: str):
        with open(skeleton_path, encoding="utf-8") as f:
            self.skeleton: dict = json.load(f)

        self.joints: list[dict] = self.skeleton["joints"]
        self.chains: list[list[str]] = self.skeleton.get("chains", [])
        self.n_animal = len(self.joints)
        self._joint_idx: dict[str, int] = {j["id"]: i for i, j in enumerate(self.joints)}

        if os.path.exists(poses_path):
            self.poses: np.ndarray = np.load(poses_path)   # (N, 15)
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
            mins = np.array([d["min"] for d in HAND_DOFS], dtype=float)
            maxs = np.array([d["max"] for d in HAND_DOFS], dtype=float)
            self.poses = mins + rng.beta(2, 2, (500, N_HAND)) * (maxs - mins)

        self.n_poses = self.poses.shape[0]

        # 정규화된 포즈 (0~1)
        mins = np.array([d["min"] for d in HAND_DOFS], dtype=float)
        maxs = np.array([d["max"] for d in HAND_DOFS], dtype=float)
        self._poses_norm: np.ndarray = (self.poses - mins) / (maxs - mins + 1e-8)

    # ──────────────────────────────────────────────────────────
    # 2-2. 목적함수 Q 구성 요소
    # ──────────────────────────────────────────────────────────

    def compute_C_matrix(self) -> np.ndarray:
        """
        C[i, j] = 제어 가능성 점수
        손 DOF j의 ROM 과 동물 관절 i의 ROM 이 겹치는 비율.
        두 구간의 교집합 크기 / 합집합 크기 (Jaccard index)
        """
        C = np.zeros((self.n_animal, N_HAND))
        for i, aj in enumerate(self.joints):
            a_min, a_max = aj["min_angle"], aj["max_angle"]
            a_range = a_max - a_min
            for j, hd in enumerate(HAND_DOFS):
                h_min, h_max = float(hd["min"]), float(hd["max"])
                h_range = h_max - h_min

                inter_lo = max(a_min, h_min)
                inter_hi = min(a_max, h_max)
                intersection = max(0.0, inter_hi - inter_lo)
                union = a_range + h_range - intersection

                C[i, j] = intersection / union if union > 1e-8 else 0.0
        return C

    def compute_S_matrix(self) -> np.ndarray:
        """
        S[i, j] = 구조적 유사성 점수
        동물 관절 i의 ROM 과 손 DOF j의 분포 유사도.
        10k 포즈에서 손 DOF j 값을 동물 관절 i 의 ROM 으로 선형 매핑 후,
        매핑 잔차(angular distance)의 역수를 사용한다.
        """
        S = np.zeros((self.n_animal, N_HAND))
        for i, aj in enumerate(self.joints):
            a_min, a_max = aj["min_angle"], aj["max_angle"]
            for j, hd in enumerate(HAND_DOFS):
                h_min, h_max = float(hd["min"]), float(hd["max"])
                h_range = h_max - h_min

                # 손 DOF j 포즈를 동물 관절 i 범위로 선형 변환
                h_norm = (self.poses[:, j] - h_min) / (h_range + 1e-8)  # 0~1
                a_pred = a_min + h_norm * (a_max - a_min)

                # ROM 중간값과의 평균 편차 (낮을수록 좋음)
                a_mid   = (a_min + a_max) / 2.0
                a_range = a_max - a_min + 1e-8
                mean_dev = float(np.mean(np.abs(a_pred - a_mid))) / a_range  # 0~0.5

                S[i, j] = 1.0 - 2.0 * mean_dev  # -1 ~ 1
        return S

    def compute_F_vector(self) -> np.ndarray:
        """
        F[j] = 손 DOF j 를 사용할 때의 편안함 점수 (높을수록 편안함).
        휴식 자세(rest) 에서 평균 이탈도를 기반으로 계산.
        RULA 지표 단순화: 손목 과굴절 여부는 thumb_ip 각도로 근사.
        """
        F = np.zeros(N_HAND)
        for j, hd in enumerate(HAND_DOFS):
            rest = float(hd["rest"])
            h_max = float(hd["max"])
            h_range = h_max - float(hd["min"]) + 1e-8

            # 휴식 자세 이탈도 (0~1)
            deviation = float(np.mean(np.abs(self.poses[:, j] - rest))) / h_range

            # 손가락 공동 활성화 (IFA): 같은 손가락의 다른 관절과 상관관계
            finger = hd["finger"]
            same_finger = [k for k, d in enumerate(HAND_DOFS) if d["finger"] == finger and k != j]
            if same_finger:
                corrs = [float(np.corrcoef(self.poses[:, j], self.poses[:, k])[0, 1])
                         for k in same_finger]
                ifa = float(np.mean(np.abs(corrs)))
            else:
                ifa = 0.0

            # 과신전 (HE): ROM 상위 10% 사용 비율
            he = float(np.mean(self.poses[:, j] > 0.9 * h_max))

            F[j] = 1.0 - (0.5 * deviation + 0.3 * ifa + 0.2 * he)
        return F

    # ──────────────────────────────────────────────────────────
    # 2-3. ILP 설정 및 풀기
    # ──────────────────────────────────────────────────────────

    def _solve_ilp(
        self,
        C: np.ndarray,
        S: np.ndarray,
        F: np.ndarray,
    ) -> np.ndarray:
        """
        ILP 풀기.
        변수: x[i,j] ∈ {0,1}  (동물 관절 i ↔ 손 DOF j)
        목적: maximize  Σ x[i,j] * (W_S*S[i,j] + W_C*C[i,j] + W_F*F[j]) +
                        chain_bonus(x)
        제약: (1) Σ_j x[i,j] = 1   — 각 동물 관절은 정확히 하나의 손 DOF
              (2) Σ_i x[i,j] ≤ 1   — 각 손 DOF는 최대 1번 사용
              (3) 체인 내 관절은 같은 손가락에 매핑 (소프트, 보너스로 처리)
        반환: assignment (n_animal,) — 각 동물 관절에 배정된 손 DOF 인덱스
        """
        try:
            import pulp
        except ImportError:
            raise ImportError(
                "PuLP가 설치되어 있지 않습니다. `pip install pulp` 를 실행하세요."
            )

        n_a = self.n_animal
        n_h = N_HAND

        # 관절당 Q 기여값 행렬
        Q = W_S * S + W_C * C
        for j in range(n_h):
            Q[:, j] += W_F * F[j]

        # 체인 내 같은 손가락 매핑 보너스 행렬 (B)
        # B[i, j] = 동일 체인의 관절들이 같은 손가락으로 매핑될 때 추가 점수
        CHAIN_BONUS = 5.0
        finger_of = np.array([d["finger"] for d in HAND_DOFS])

        prob = pulp.LpProblem("HandAnimalMapping", pulp.LpMaximize)

        # 변수 생성
        x = [[pulp.LpVariable(f"x_{i}_{j}", cat="Binary")
              for j in range(n_h)] for i in range(n_a)]

        # 목적함수: Σ Q[i,j] * x[i,j]
        obj_terms = [Q[i, j] * x[i][j] for i in range(n_a) for j in range(n_h)]

        # 체인 내 같은 손가락 사용 보너스 (연속 관절 쌍)
        for chain in self.chains:
            for k in range(len(chain) - 1):
                i1 = self._joint_idx[chain[k]]
                i2 = self._joint_idx[chain[k + 1]]
                for j1 in range(n_h):
                    for j2 in range(n_h):
                        if finger_of[j1] == finger_of[j2] and j1 != j2:
                            # 두 관절이 같은 손가락의 다른 세그먼트에 매핑되면 보너스
                            bonus_var = pulp.LpVariable(
                                f"b_{i1}_{j1}_{i2}_{j2}", cat="Binary"
                            )
                            prob += bonus_var <= x[i1][j1]
                            prob += bonus_var <= x[i2][j2]
                            obj_terms.append(CHAIN_BONUS * bonus_var)

        prob += pulp.lpSum(obj_terms)

        # 제약 (1): 각 동물 관절은 정확히 1개 손 DOF에 매핑
        for i in range(n_a):
            prob += pulp.lpSum(x[i]) == 1

        # 제약 (2): 각 손 DOF는 최대 1번 사용
        for j in range(n_h):
            prob += pulp.lpSum(x[i][j] for i in range(n_a)) <= 1

        # 풀기 (메시지 억제)
        solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=60)
        prob.solve(solver)

        if pulp.LpStatus[prob.status] not in ("Optimal", "Not Solved"):
            warnings.warn(f"ILP 상태: {pulp.LpStatus[prob.status]}", stacklevel=3)

        # 결과 추출
        assignment = np.zeros(n_a, dtype=int)
        for i in range(n_a):
            for j in range(n_h):
                if pulp.value(x[i][j]) is not None and pulp.value(x[i][j]) > 0.5:
                    assignment[i] = j
                    break

        return assignment

    # ──────────────────────────────────────────────────────────
    # 2-4. 기준 자세 쌍 (H, A) 결정
    # ──────────────────────────────────────────────────────────

    def _find_reference_pose(self, assignment: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        매핑 M 을 기반으로 최적 기준 손 포즈 H 와
        그에 대응하는 아바타 기본 포즈 A 를 계산한다.

        H: 매핑된 손 DOF 들의 ROM 중간값 (나머지는 휴식 자세)
        A: H 를 각 아바타 관절 ROM 으로 선형 변환
        """
        H = np.array([float(d["rest"]) for d in HAND_DOFS])
        for i, aj in enumerate(self.joints):
            j = int(assignment[i])
            hd = HAND_DOFS[j]
            # 매핑된 DOF 는 ROM 중간값으로 설정
            H[j] = (float(hd["min"]) + float(hd["max"])) / 2.0

        # 아바타 포즈 A: H 를 동물 ROM 으로 선형 변환
        A = np.zeros(self.n_animal)
        for i, aj in enumerate(self.joints):
            j = int(assignment[i])
            hd = HAND_DOFS[j]
            h_min, h_max = float(hd["min"]), float(hd["max"])
            h_range = h_max - h_min + 1e-8
            h_norm = (H[j] - h_min) / h_range
            A[i] = aj["min_angle"] + h_norm * (aj["max_angle"] - aj["min_angle"])

        return H, A

    # ──────────────────────────────────────────────────────────
    # 공개 API
    # ──────────────────────────────────────────────────────────

    def run(self) -> dict[str, Any]:
        """최적화 실행. 결과 딕셔너리 반환."""
        print(f"[MappingOptimizer] {self.skeleton['animal_name']} 최적화 시작")
        print(f"  동물 관절 수: {self.n_animal}, 손 DOF 수: {N_HAND}, 포즈 수: {self.n_poses}")

        print("  C (제어 점수) 계산 중...")
        C = self.compute_C_matrix()
        print("  S (구조 점수) 계산 중...")
        S = self.compute_S_matrix()
        print("  F (편안함 점수) 계산 중...")
        F = self.compute_F_vector()

        print("  ILP 풀기 중...")
        assignment = self._solve_ilp(C, S, F)

        print("  기준 자세 (H, A) 결정 중...")
        H, A = self._find_reference_pose(assignment)

        # 매핑 딕셔너리 구성
        mapping: dict[str, dict] = {}
        for i, aj in enumerate(self.joints):
            j = int(assignment[i])
            hd = HAND_DOFS[j]
            a_range = aj["max_angle"] - aj["min_angle"] + 1e-8
            h_range = float(hd["max"]) - float(hd["min"]) + 1e-8
            scale   = a_range / h_range

            q_val = float(W_S * S[i, j] + W_C * C[i, j] + W_F * F[j])
            mapping[aj["id"]] = {
                "hand_dof_idx":  j,
                "hand_dof_name": hd["name"],
                "scale_factor":  round(scale, 4),
                "Q_score":       round(q_val, 4),
            }

        result = {
            "animal":   self.skeleton["animal_name"],
            "mapping":  mapping,
            "reference_pose_H": {
                HAND_DOFS[j]["name"]: round(float(H[j]), 2) for j in range(N_HAND)
            },
            "reference_pose_A": {
                self.joints[i]["id"]: round(float(A[i]), 2)
                for i in range(self.n_animal)
            },
        }

        self._print_summary(result, C, S, F, assignment)
        return result

    def _print_summary(self, result, C, S, F, assignment):
        print(f"\n  === {result['animal']} 매핑 결과 ===")
        print(f"  {'동물 관절':25s}  {'손 DOF':15s}  {'scale':6s}  Q")
        print(f"  {'-'*60}")
        for aj_id, info in result["mapping"].items():
            print(
                f"  {aj_id:25s}  {info['hand_dof_name']:15s}  "
                f"{info['scale_factor']:6.3f}  {info['Q_score']:+.3f}"
            )
        print()

    def run_bilateral(self) -> dict[str, Any]:
        """
        양손 매핑 실행.

        관절 ID에 '_L'/'_R' (또는 'left'/'right') 패턴이 있으면 자동으로
        왼손/오른손 그룹으로 분리한다. 패턴이 없으면 절반씩 나눈다.

        양손 균형 제약 (논문 식 5):
            |n_left - n_right| ≤ n_total / 3
        위반 시 경고만 출력하고 계속 진행한다.

        반환 딕셔너리 구조:
            mapping[joint_id]["hand"]  → "left" | "right"
            reference_pose_H["left"]   → 왼손 기준 포즈
            reference_pose_H["right"]  → 오른손 기준 포즈
            bilateral_balance          → 균형 정보
        """
        # ── 관절 분류 ──────────────────────────────────────────
        left_joints, right_joints, center_joints = [], [], []
        for j in self.joints:
            jid = j["id"].lower()
            if "_l" in jid or jid.startswith("l_") or "left" in jid:
                left_joints.append(j)
            elif "_r" in jid or jid.startswith("r_") or "right" in jid:
                right_joints.append(j)
            else:
                center_joints.append(j)

        # 좌우 구분 없는 관절은 절반씩 분배
        mid = len(center_joints) // 2
        left_joints  = left_joints  + center_joints[:mid]
        right_joints = right_joints + center_joints[mid:]

        # ── 양손 균형 제약 확인 (식 5) ────────────────────────
        n_total = len(left_joints) + len(right_joints)
        n_left  = len(left_joints)
        n_right = len(right_joints)
        balanced = abs(n_left - n_right) <= n_total / 3
        if not balanced:
            warnings.warn(
                f"[양손 균형 위반] 왼손 {n_left}관절, 오른손 {n_right}관절 "
                f"(허용 최대 차이: {n_total / 3:.1f}). "
                "관절 ID에 _L/_R 패턴이 있는지 확인하세요.",
                stacklevel=2,
            )
        else:
            print(f"  [양손 균형 OK] 왼손 {n_left}관절, 오른손 {n_right}관절")

        # ── 각 그룹을 독립적으로 최적화 ──────────────────────
        left_result  = self._run_joint_subset(left_joints,  "left")
        right_result = self._run_joint_subset(right_joints, "right")

        # ── 결과 병합 ─────────────────────────────────────────
        merged_mapping: dict[str, dict] = {}
        for k, v in left_result["mapping"].items():
            merged_mapping[k] = {**v, "hand": "left"}
        for k, v in right_result["mapping"].items():
            merged_mapping[k] = {**v, "hand": "right"}

        return {
            "animal":   self.skeleton["animal_name"],
            "mode":     "bilateral",
            "mapping":  merged_mapping,
            "reference_pose_H": {
                "left":  left_result["reference_pose_H"],
                "right": right_result["reference_pose_H"],
            },
            "reference_pose_A": {
                **left_result["reference_pose_A"],
                **right_result["reference_pose_A"],
            },
            "bilateral_balance": {
                "n_left":    n_left,
                "n_right":   n_right,
                "balanced":  balanced,
            },
        }

    def _run_joint_subset(self, joints_subset: list[dict], hand_side: str) -> dict[str, Any]:
        """
        주어진 관절 서브셋에 대해서만 최적화를 실행한다.
        내부적으로 self.joints를 임시 교체하고 복원한다.
        """
        print(f"[MappingOptimizer] {self.skeleton['animal_name']} ({hand_side}) 최적화 시작 "
              f"— {len(joints_subset)}관절")

        # 인스턴스 변수 임시 교체
        saved_joints    = self.joints
        saved_n_animal  = self.n_animal
        saved_joint_idx = self._joint_idx
        saved_chains    = self.chains

        self.joints     = joints_subset
        self.n_animal   = len(joints_subset)
        self._joint_idx = {j["id"]: i for i, j in enumerate(joints_subset)}
        subset_ids      = {j["id"] for j in joints_subset}
        self.chains     = [
            [jid for jid in chain if jid in subset_ids]
            for chain in saved_chains
        ]
        self.chains = [c for c in self.chains if len(c) >= 2]

        try:
            C = self.compute_C_matrix()
            S = self.compute_S_matrix()
            F = self.compute_F_vector()
            assignment = self._solve_ilp(C, S, F)
            H, A = self._find_reference_pose(assignment)

            mapping: dict[str, dict] = {}
            for i, aj in enumerate(self.joints):
                j = int(assignment[i])
                hd = HAND_DOFS[j]
                a_range = aj["max_angle"] - aj["min_angle"] + 1e-8
                h_range = float(hd["max"]) - float(hd["min"]) + 1e-8
                scale   = a_range / h_range
                q_val   = float(W_S * S[i, j] + W_C * C[i, j] + W_F * F[j])
                mapping[aj["id"]] = {
                    "hand_dof_idx":  j,
                    "hand_dof_name": hd["name"],
                    "scale_factor":  round(scale, 4),
                    "Q_score":       round(q_val, 4),
                }

            return {
                "animal":   self.skeleton["animal_name"],
                "mapping":  mapping,
                "reference_pose_H": {
                    HAND_DOFS[j]["name"]: round(float(H[j]), 2) for j in range(N_HAND)
                },
                "reference_pose_A": {
                    self.joints[i]["id"]: round(float(A[i]), 2)
                    for i in range(self.n_animal)
                },
            }
        finally:
            # 반드시 원상복구
            self.joints     = saved_joints
            self.n_animal   = saved_n_animal
            self._joint_idx = saved_joint_idx
            self.chains     = saved_chains

    def save(self, result: dict, out_path: str):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[OK] 매핑 저장: {out_path}")

    def unit_test(self):
        """단위 테스트: 임의 매핑 입력 시 Q 값이 올바르게 나오는지 확인."""
        print("\n[단위 테스트] Q 계산 검증")
        C = self.compute_C_matrix()
        S = self.compute_S_matrix()
        F = self.compute_F_vector()

        # 임의 매핑 (0번 관절 → 0번 손 DOF)
        test_i, test_j = 0, 3  # 동물 관절 0, 손 DOF 3 (index_mcp)
        q = W_F * F[test_j] + W_S * S[test_i, test_j] + W_C * C[test_i, test_j]

        print(f"  동물관절[{test_i}]={self.joints[test_i]['id']} ↔ 손DOF[{test_j}]={HAND_DOFS[test_j]['name']}")
        print(f"  C={C[test_i,test_j]:.4f}  S={S[test_i,test_j]:.4f}  F={F[test_j]:.4f}")
        print(f"  Q = {W_F}×{F[test_j]:.4f} + {W_S}×{S[test_i,test_j]:.4f} + {W_C}×{C[test_i,test_j]:.4f} = {q:.4f}")
        assert not np.isnan(q), "Q 값이 NaN"
        print("  [PASS] Q 값 정상\n")
