"""
compute_q.py  —  §6 목적함수 Q
---------------------------------
논문 수식:
    Q = w_f × F(g) + w_s × (−S(g, p, M)) + w_c × C(M)
    가중치: w_f = −5, w_s = 0.8, w_c = −0.01

부호 방향:
    W_F × F        : 불편할수록(F 낮음) Q 감소 → 불편한 포즈 기피
    W_S × (−S)     : 유사할수록(S 작음) Q 증가 → 유사한 포즈 선호
    W_C × C        : ROM 겹침 클수록 Q 소폭 감소 → 정규화 역할
"""

import numpy as np

from .constants import W_F, W_S, W_C
from .compute_f import compute_F
from .compute_s import compute_S


def compute_Q(
    g: np.ndarray,
    p_dict: dict,
    assignment: np.ndarray,
    C: np.ndarray,
    joints: list[dict],
) -> float:
    """
    단일 (g, p, M) 조합의 목적함수 Q 값.

    수식: Q = W_F·F(g) + W_S·(−S(g,p,M)) + W_C·C(M)
    g        : 20-DOF 손 포즈 벡터 (degrees)
    p_dict   : {joint_id: angle_deg} 아바타 포즈
    assignment: 아바타 관절 → 손 DOF 인덱스 벡터
    C        : (n_animal × N_HAND) 제어 점수 행렬
    joints   : skeleton JSON 관절 목록
    반환: float

    Examples
    --------
    >>> # Q(편안한 포즈) >= Q(불편한 포즈)  — W_F 부호 검증
    >>> # Q(유사한 포즈) >= Q(다른 포즈)    — W_S 부호 검증
    """
    f = compute_F(g)
    s = compute_S(g, p_dict, assignment, joints)
    c = float(sum(C[i, int(assignment[i])] for i in range(len(joints))))
    return float(W_F * f + W_S * (-s) + W_C * c)
