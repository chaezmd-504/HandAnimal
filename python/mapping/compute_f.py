"""
compute_f.py  —  §4 편안함 점수 F
------------------------------------
논문 수식:
    F(g) = 1 − discomfort(g)
    discomfort = 0.4·RULA + 0.3·IFA + 0.2·HE + 0.1·FA

구성 요소:
    RULA  — 손목 DOF 중립 이탈도 근사        (§4-1)
    IFA   — 손가락 관절 굴신 비율 점수        (§4-2)
    HE    — 과신전 패널티                     (§4-3)
    FA    — 손가락 벌림 균형 점수             (§4-4)

정규화 §2:
    g_hat = (g − ROM_min) / (ROM_max − ROM_min)  ∈ [0, 1]
"""

import numpy as np

from .constants import (
    _DOF_MINS, _DOF_MAXS, _DOF_RANGES, _DOF_RESTS,
    _FINGER_FLEX_IDXS, _FINGER_ALL_IDXS, _NATURAL_RATIOS,
)


# ──────────────────────────────────────────────────────────────
# §4-1  RULA — 손목 부하 점수
# ──────────────────────────────────────────────────────────────

def compute_rula(g_n: np.ndarray) -> float:
    """
    손목 3 DOF(idx 0~2: flex, dev, rot)의 중립 이탈도.

    논문 수식: RULA = 2 × mean(|ĝ_i − 0.5|)  for i ∈ {flex, dev, rot}
    - 0.5: ROM 정중앙 = 중립 자세
    - 2×: [0, 0.5] 범위를 [0, 1]로 스케일
    g_n : 정규화된 20-DOF 벡터 (0~1)
    반환: float ∈ [0, 1]

    ⚠️ RULA 완전 구현 불가 (논문: 3D 손목 위치·비틀림 기반).
       RGB 카메라 z축 한계로 손목 3 DOF 이탈도로 근사.
    """
    return float(2.0 * np.mean(np.abs(g_n[:3] - 0.5)))


# ──────────────────────────────────────────────────────────────
# §4-2  IFA — 손가락 관절 굴신 비율 점수
# ──────────────────────────────────────────────────────────────

def compute_ifa(g_n: np.ndarray) -> float:
    """
    각 손가락의 MCP:PIP:DIP 굴신 비율이 Hume(1990) 자연 비율에서 벗어난 정도.

    논문 수식:
        S_f = Σ ĝ_k  (손가락 f 의 굴신 DOF 합)
        r_f^k = ĝ_k / S_f
        IFA_f = mean(|r_f^k − n_f^k|)
        IFA = (1/0.67) × (1/5) × Σ IFA_f  ∈ [0, 1]
    - (1/0.67): IFA_f 이론적 최대값이 0.67이므로 [0,1] 정규화
    g_n : 정규화된 20-DOF 벡터
    반환: float ∈ [0, 1]
    """
    ifa_errs: list[float] = []
    for fname, idxs in _FINGER_FLEX_IDXS.items():
        vals = g_n[idxs]
        total = float(np.sum(vals))
        if total < 1e-6:
            ifa_errs.append(0.0)
            continue
        actual_ratio   = vals / total
        expected_ratio = np.array(_NATURAL_RATIOS[fname])
        ifa_errs.append(float(np.mean(np.abs(actual_ratio - expected_ratio))))
    return float(np.clip(np.mean(ifa_errs) / 0.67, 0.0, 1.0))


# ──────────────────────────────────────────────────────────────
# §4-3  HE — 과신전 패널티
# ──────────────────────────────────────────────────────────────

def compute_he(g_n: np.ndarray) -> float:
    """
    ROM 상위 10%(ĝ > 0.9)를 초과한 DOF 비율.

    수식: HE = (1/20) Σ 1[ĝ_i > 0.9]
    g_n : 정규화된 20-DOF 벡터
    반환: float ∈ [0, 1]
    """
    return float(np.mean(g_n > 0.9))


# ──────────────────────────────────────────────────────────────
# §4-4  FA — 손가락 벌림 균형 점수
# ──────────────────────────────────────────────────────────────

def compute_fa(g_n: np.ndarray) -> float:
    """
    인접 손가락 쌍의 평균 굴신량 차이.

    수식:
        ḡ_f = mean(ĝ_k)  for k in joints(f)
        FA = mean(|ḡ_k − ḡ_{k+1}|)  for k ∈ {0,1,2,3}
    g_n : 정규화된 20-DOF 벡터
    반환: float ∈ [0, 1]
    """
    finger_means = [
        float(np.mean(g_n[_FINGER_ALL_IDXS[f]]))
        for f in ["thumb", "index", "middle", "ring", "pinky"]
    ]
    return float(np.mean([abs(finger_means[k] - finger_means[k + 1]) for k in range(4)]))


# ──────────────────────────────────────────────────────────────
# §4  F 통합
# ──────────────────────────────────────────────────────────────

def compute_F(g: np.ndarray) -> float:
    """
    단일 손 포즈 g 의 편안함 점수.

    수식: F(g) = 1 − (0.4·RULA + 0.3·IFA + 0.2·HE + 0.1·FA)
    g : 20-DOF 각도 벡터 (degrees), shape (20,)
    반환: float ∈ [0, 1]  — 높을수록 편안함
    """
    g_n = (g - _DOF_MINS) / _DOF_RANGES    # §2 정규화

    rula = compute_rula(g_n)
    ifa  = compute_ifa(g_n)
    he   = compute_he(g_n)
    fa   = compute_fa(g_n)

    discomfort = 0.4 * rula + 0.3 * ifa + 0.2 * he + 0.1 * fa
    return float(np.clip(1.0 - discomfort, 0.0, 1.0))
