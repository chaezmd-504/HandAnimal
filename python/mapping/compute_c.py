"""
compute_c.py  —  §3 제어 점수 C
---------------------------------
논문 수식:
    C_{j_i^a, j_k^h} = |RoM_{j_i^a}^a ∩ RoM_{j_k^h}^h|
    = max(0, min(a_max, h_max) − max(a_min, h_min))

아바타 관절 i 와 손 DOF j 의 ROM이 겹치는 구간 길이.
포즈와 무관한 정적값 → 1회만 계산. 반환 shape: (N × 20).

목적함수에서의 역할:  W_C × C = −0.01 × C  (정규화 역할)
"""

import numpy as np

from .constants import HAND_DOFS, N_HAND


def compute_C_matrix(joints: list[dict]) -> np.ndarray:
    """
    C[i, j] = |RoM_avatar^i ∩ RoM_hand^j| — ROM 겹치는 구간 길이.

    Parameters
    ----------
    joints : list[dict]
        skeleton JSON의 관절 목록. 각 dict에 "min_angle", "max_angle" 필드.

    Returns
    -------
    C : np.ndarray, shape (n_animal, N_HAND)
        C[i, j] = 아바타 관절 i 와 손 DOF j 의 ROM 겹침 길이 (degree).

    Examples
    --------
    >>> joints = [{"id": "leg", "min_angle": -60, "max_angle": 60}]
    >>> C = compute_C_matrix(joints)
    >>> C[0, 1]  # wrist_dev (-25~25) vs leg (-60~60) → 50
    50.0
    """
    n_animal = len(joints)
    C = np.zeros((n_animal, N_HAND))

    for i, aj in enumerate(joints):
        a_min = float(aj["min_angle"])
        a_max = float(aj["max_angle"])
        for j, hd in enumerate(HAND_DOFS):
            h_min = float(hd["min"])
            h_max = float(hd["max"])
            overlap_lo = max(a_min, h_min)
            overlap_hi = min(a_max, h_max)
            C[i, j] = max(0.0, overlap_hi - overlap_lo)

    return C
