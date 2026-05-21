"""
ilp_solver.py  —  §7 Step 1: ILP 매핑 M 최적화
-------------------------------------------------
논문 목표:
    M = argmax Σ_{g∈G} Σ_{p∈P} Q(g, p, M)

결정 변수: u_ij ∈ {0, 1}  (아바타 관절 i → 손 DOF j 매핑 여부)

ILP 목적함수 (선형화):
    max Σ_{i,j} u_ij × (W_S·(−S̄[i,j]) + W_C·C[i,j] + W_F·F̄[j])

4가지 제약:
    (1) Σ_j u_ij = 1          각 아바타 관절은 정확히 1개 손 DOF
    (2) Σ_i u_ij ≤ 1          각 손 DOF는 최대 1번 사용
    (3a) chain 동일 손가락    연속 관절 쌍은 반드시 같은 손가락 내 DOF에 배정 (하드)
    (3b) chain 근위→원위 순서 chain[k]의 segment < chain[k+1]의 segment (하드)
    (4)  forced_fingers        bilateral 대칭: 지정 관절을 특정 손가락으로 강제

    * (3a)(3b)는 이전 소프트 chain_bonus를 대체하는 하드 제약.

솔버: PuLP + CBC (논문: Gurobi)
"""

import warnings

import numpy as np

from .constants import HAND_DOFS, N_HAND, W_F, W_S, W_C


# ──────────────────────────────────────────────────────────────
# F̄ 벡터 — Step 1 ILP 전용 per-DOF 편안함 기여 근사
# ──────────────────────────────────────────────────────────────

def compute_F_bar(G_sub: np.ndarray) -> np.ndarray:
    """
    ILP 선형화를 위한 per-DOF 평균 편안함 기여 벡터.

    compute_F(g) 전체를 DOF j에 귀속시키기 어려우므로,
    DOF j 의 휴식 이탈도와 과신전 비율로 근사한다.

    G_sub  : (k × 20) 편안함 상위 손 포즈 집합
    반환   : F_bar (N_HAND,) — DOF별 편안함 기여 (높을수록 편안)
    """
    F_bar = np.zeros(N_HAND)
    for j, hd in enumerate(HAND_DOFS):
        rest    = float(hd["rest"])
        h_max   = float(hd["max"])
        h_min   = float(hd["min"])
        h_range = h_max - h_min + 1e-8

        deviation = float(np.mean(np.abs(G_sub[:, j] - rest))) / h_range
        he_j      = float(np.mean(G_sub[:, j] > 0.9 * h_max))

        F_bar[j] = 1.0 - (0.7 * deviation + 0.3 * he_j)
    return F_bar


# ──────────────────────────────────────────────────────────────
# 손가락 그룹 / 세그먼트 순서 — 모듈 초기화 시 1회 계산
# ──────────────────────────────────────────────────────────────

# finger_name → [dof_idx, ...]   (constants.py HAND_DOFS 순서 보장)
_FINGER_GROUPS: dict[str, list[int]] = {}
for _j, _d in enumerate(HAND_DOFS):
    _FINGER_GROUPS.setdefault(_d["finger"], []).append(_j)

# 각 DOF 의 근위→원위 세그먼트 순위 (constants.py 의 "segment" 필드)
_SEG_OF: list[int] = [d["segment"] for d in HAND_DOFS]


# ──────────────────────────────────────────────────────────────
# ILP 솔버
# ──────────────────────────────────────────────────────────────

def solve_ilp(
    C: np.ndarray,
    S_bar: np.ndarray,
    F_bar: np.ndarray,
    joints: list[dict],
    chains: list[list[str]],
    forced_fingers: dict[str, str] | None = None,
) -> np.ndarray:
    """
    ILP 로 최적 매핑 M 결정.

    C      : (n_animal × N_HAND) 제어 점수 행렬
    S_bar  : (n_animal × N_HAND) 평균 구조 유사성 행렬 (dissimilarity)
    F_bar  : (N_HAND,) per-DOF 편안함 기여 벡터
    joints : skeleton JSON 관절 목록
    chains : 연속 관절 체인 목록 [["base", "mid", "tip"], ...]
    forced_fingers : {joint_id: finger_name} — bilateral 대칭 강제
                     예) {"l_leg": "ring"} → l_leg 는 반드시 ring 손가락 내 DOF

    반환   : assignment (n_animal,) — 각 관절에 배정된 손 DOF 인덱스
    """
    try:
        import pulp
    except ImportError:
        raise ImportError("PuLP 가 없습니다. `pip install pulp` 를 실행하세요.")

    n_a = len(joints)
    n_h = N_HAND
    joint_idx = {j["id"]: i for i, j in enumerate(joints)}

    # 목적함수 계수 행렬
    # S_bar 는 dissimilarity → −S_bar 로 변환하여 유사성으로 전환
    Q_mat = W_S * (-S_bar) + W_C * C                     # (n_a, n_h)
    for j in range(n_h):
        Q_mat[:, j] += W_F * F_bar[j]

    prob = pulp.LpProblem("HandAnimalMapping", pulp.LpMaximize)
    x = [
        [pulp.LpVariable(f"x_{i}_{j}", cat="Binary") for j in range(n_h)]
        for i in range(n_a)
    ]

    # 목적함수
    prob += pulp.lpSum(
        Q_mat[i, j] * x[i][j]
        for i in range(n_a)
        for j in range(n_h)
    )

    # ── 제약 1: 각 아바타 관절은 정확히 1개 손 DOF ────────────
    for i in range(n_a):
        prob += pulp.lpSum(x[i]) == 1

    # ── 제약 2: 각 손 DOF는 최대 1번 사용 ─────────────────────
    for j in range(n_h):
        prob += pulp.lpSum(x[i][j] for i in range(n_a)) <= 1

    # ── 제약 3 (하드): 체인 내 연속 관절 쌍 ────────────────────
    #   (3a) 같은 손가락 강제
    #   (3b) 근위→원위 순서 강제  (seg[chain[k]] < seg[chain[k+1]])
    for chain in chains:
        for k in range(len(chain) - 1):
            a_id, b_id = chain[k], chain[k + 1]
            if a_id not in joint_idx or b_id not in joint_idx:
                continue
            i1 = joint_idx[a_id]
            i2 = joint_idx[b_id]

            # (3a) 같은 손가락: 모든 손가락 f 에 대해
            #      Σ_{j∈f} x[i1][j] == Σ_{j∈f} x[i2][j]
            for f_idxs in _FINGER_GROUPS.values():
                prob += (
                    pulp.lpSum(x[i1][j] for j in f_idxs) ==
                    pulp.lpSum(x[i2][j] for j in f_idxs)
                )

            # (3b) 근위→원위:  seg(i1) ≤ seg(i2) - 1
            prob += (
                pulp.lpSum(_SEG_OF[j] * x[i1][j] for j in range(n_h)) <=
                pulp.lpSum(_SEG_OF[j] * x[i2][j] for j in range(n_h)) - 1
            )

    # ── 제약 4: bilateral 대칭 강제 ────────────────────────────
    #   forced_fingers[joint_id] = finger_name
    #   → 해당 관절은 반드시 finger_name 손가락 내 DOF에 배정
    if forced_fingers:
        for joint_id, finger_name in forced_fingers.items():
            if joint_id not in joint_idx:
                continue
            i = joint_idx[joint_id]
            f_idxs = _FINGER_GROUPS.get(finger_name, [])
            if not f_idxs:
                warnings.warn(
                    f"[ILP] forced_fingers 손가락 '{finger_name}' 을 찾을 수 없음 (관절: {joint_id})",
                    stacklevel=2,
                )
                continue
            prob += pulp.lpSum(x[i][j] for j in f_idxs) == 1

    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=60)
    prob.solve(solver)

    status = pulp.LpStatus[prob.status]
    if status not in ("Optimal", "Not Solved"):
        warnings.warn(f"ILP 상태: {status}", stacklevel=2)

    assignment = np.zeros(n_a, dtype=int)
    for i in range(n_a):
        for j in range(n_h):
            v = pulp.value(x[i][j])
            if v is not None and v > 0.5:
                assignment[i] = j
                break

    return assignment
