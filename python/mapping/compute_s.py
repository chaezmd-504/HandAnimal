"""
compute_s.py  —  §5 구조적 유사성 점수 S
------------------------------------------
논문 수식:
    S^{p_i g_k}_{j_x^a, j_y^h} = |t_{j_x^a} − t_{j_y^h}|

⚠️ S 는 dissimilarity — 값이 작을수록 두 포즈가 구조적으로 유사.
   ILP maximize 목적함수에서 반드시 −S 형태로 사용해야 함.

두 가지 함수:
    compute_S       — 단일 (g, p) 쌍의 S 합산값  (Step 2 / Q 계산용)
    compute_S_bar   — G_sub × P 전체 평균 행렬   (Step 1 ILP 선형화용)

[선택 항] 자연 사용 분산 항 (beta, 논문에 없는 휴리스틱):
    S̄[i,j] += beta × (1 − var_G_norm[j])

    - var_G(DOF_j): G_sub에서 손 DOF j 의 정규화된 분산
      → 자주 쓰이는 DOF(손가락)는 높음, 손목은 낮음
    - 효과: 손목처럼 G_sub에서 잘 안 움직이는 DOF에 추가 페널티 부여

    beta=0 (기본): 논문 원래 수식 그대로. wrist 포함 모든 DOF 공평하게 경쟁.
    beta>0        : wrist 배정 억제 휴리스틱. 동물마다 필요 여부 다름.
                    (예: 거미 — 다리에 손목 배정 방지 → beta=5.0 권장)
                    (예: 코끼리 — 손목으로 코/머리 제어할 수도 있음 → beta=0 권장)

    generate_mappings.py의 BETA_MAP에서 동물별로 지정.
"""

import numpy as np

from .constants import HAND_DOFS, N_HAND

# 손 DOF 정규화용 사전 계산
_H_MINS   = np.array([float(d["min"]) for d in HAND_DOFS])
_H_RANGES = np.array([float(d["max"]) - float(d["min"]) + 1e-8 for d in HAND_DOFS])


def _norm_avatar(val: float, a_min: float, a_range: float) -> float:
    """아바타 관절 각도를 [0, 1] 로 정규화."""
    return (val - a_min) / a_range


def compute_S(
    g: np.ndarray,
    p_dict: dict,
    assignment: np.ndarray,
    joints: list[dict],
) -> float:
    """
    단일 포즈 쌍 (g, p) 에 대한 구조적 불일치도 S.

    수식: S(g, p, M) = Σ_i |p̂[joint_i] − ĝ[M(i)]|
      - p̂: 아바타 각도를 해당 관절 ROM 으로 정규화 [0, 1]
      - ĝ: 손 DOF 각도를 해당 DOF ROM 으로 정규화 [0, 1]

    정규화로 각도 스케일 차이 제거 → S ∈ [0, n_joints]

    반환: float — 작을수록 유사
    """
    g_norm = (g - _H_MINS) / _H_RANGES          # 손 전체 정규화 (벡터)

    total = 0.0
    for i, aj in enumerate(joints):
        j       = int(assignment[i])
        a_min   = float(aj["min_angle"])
        a_range = float(aj["max_angle"]) - a_min + 1e-8
        t_a_n   = _norm_avatar(float(p_dict.get(aj["id"], 0.0)), a_min, a_range)
        t_h_n   = float(g_norm[j])
        total  += abs(t_a_n - t_h_n)
    return total


def compute_S_bar(
    G_sub: np.ndarray,
    P: list[dict],
    joints: list[dict],
    beta: float = 5.0,
) -> np.ndarray:
    """
    G_sub × P 모든 포즈 쌍의 정규화된 |p̂ − ĝ| 평균 행렬. Step 1 ILP 선형화용.

    수식 (논문): S̄[i, j] = avg|p̂[i] − ĝ[j]|
    beta>0 시:   S̄[i, j] += beta × (1 − var_G_norm[j])  (논문 외 휴리스틱)

    반환: np.ndarray, shape (n_animal, N_HAND), 값 ∈ [0, 1+]
    """
    n_animal = len(joints)
    S_bar    = np.zeros((n_animal, N_HAND))
    n_pairs  = len(G_sub) * len(P)
    if n_pairs == 0:
        return S_bar

    # 아바타 관절 정규화 상수 사전 계산
    a_mins   = np.array([float(aj["min_angle"]) for aj in joints])
    a_ranges = np.array([float(aj["max_angle"]) - float(aj["min_angle"]) + 1e-8
                         for aj in joints])

    # 손 DOF 정규화된 G_sub 사전 계산  (k × N_HAND)
    G_norm = (G_sub - _H_MINS) / _H_RANGES

    # --- 각도 차이 항 ---
    for g_norm in G_norm:
        for p_dict in P:
            for i, aj in enumerate(joints):
                t_a_n = (float(p_dict.get(aj["id"], 0.0)) - a_mins[i]) / a_ranges[i]
                S_bar[i] += np.abs(t_a_n - g_norm)   # 브로드캐스트: (N_HAND,)

    S_bar /= float(n_pairs)

    # --- 자연 사용 분산 항 ---
    # var_G[j]: G_sub에서 DOF j 의 정규화 분산 (손가락 높음, 손목 낮음)
    # 아이디어: 자연스러운 손 사용에서 잘 안 쓰이는 DOF(손목)는 어떤 관절에든 페널티
    #   S_bar[i,j] += beta × (1 − var_G_norm[j])
    # → 손목: var 낮음 → 페널티 큼 / 손가락: var 높음 → 페널티 작음
    # 아바타 애니메이션 데이터 품질에 독립적으로 동작
    var_G = np.var(G_norm, axis=0)          # (N_HAND,)
    var_G_norm = var_G / (var_G.max() + 1e-8)  # [0, 1] 정규화
    S_bar += beta * (1.0 - var_G_norm[None, :])  # 브로드캐스트: (n_animal, N_HAND)

    return S_bar
