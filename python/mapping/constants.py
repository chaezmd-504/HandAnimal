"""
constants.py
-------------
HandAnimal 매핑 알고리즘 전체에서 공유하는 상수 정의.

  - HAND_DOFS: 20-DOF 손 관절 정의 (Hume et al. 1990)
  - W_F, W_S, W_C: 목적함수 Q 가중치
  - _FINGER_FLEX_IDXS: IFA 계산용 굴신 DOF 인덱스
  - _FINGER_ALL_IDXS : FA 계산용 전체 DOF 인덱스
  - _NATURAL_RATIOS  : IFA 자연 굴신 비율 (Hume 1990)
  - _DOF_MINS/MAXS/RESTS/RANGES: 정규화 사전 계산 벡터
"""

import numpy as np

# ──────────────────────────────────────────────────────────────
# 20-DOF 손 관절 정의  (수식 §1-1, Hume et al. 1990)
# ──────────────────────────────────────────────────────────────
HAND_DOFS: list[dict] = [
    # 손목 방향 3 DOF (양방향, rest=0)
    {"name": "wrist_flex", "min": -70, "max": 70,  "rest":  0, "finger": "wrist", "segment": 0},
    {"name": "wrist_dev",  "min": -25, "max": 25,  "rest":  0, "finger": "wrist", "segment": 1},
    {"name": "wrist_rot",  "min": -90, "max": 90,  "rest":  0, "finger": "wrist", "segment": 2},
    # 엄지 4 DOF (굴신 3 + 벌림 1)
    {"name": "thumb_cmc",  "min":   0, "max": 60,  "rest": 20, "finger": "thumb", "segment": 0},
    {"name": "thumb_abd",  "min":   0, "max": 70,  "rest": 15, "finger": "thumb", "segment": 1},
    {"name": "thumb_mcp",  "min":   0, "max": 60,  "rest": 20, "finger": "thumb", "segment": 2},
    {"name": "thumb_ip",   "min":   0, "max": 80,  "rest": 15, "finger": "thumb", "segment": 3},
    # 검지 3 DOF
    {"name": "index_mcp",  "min":   0, "max": 90,  "rest": 10, "finger": "index",  "segment": 0},
    {"name": "index_pip",  "min":   0, "max": 110, "rest": 10, "finger": "index",  "segment": 1},
    {"name": "index_dip",  "min":   0, "max": 90,  "rest": 10, "finger": "index",  "segment": 2},
    # 중지 3 DOF
    {"name": "middle_mcp", "min":   0, "max": 90,  "rest": 10, "finger": "middle", "segment": 0},
    {"name": "middle_pip", "min":   0, "max": 110, "rest": 10, "finger": "middle", "segment": 1},
    {"name": "middle_dip", "min":   0, "max": 90,  "rest": 10, "finger": "middle", "segment": 2},
    # 약지 3 DOF
    {"name": "ring_mcp",   "min":   0, "max": 90,  "rest": 10, "finger": "ring",   "segment": 0},
    {"name": "ring_pip",   "min":   0, "max": 110, "rest": 10, "finger": "ring",   "segment": 1},
    {"name": "ring_dip",   "min":   0, "max": 90,  "rest": 10, "finger": "ring",   "segment": 2},
    # 소지 4 DOF (굴신 3 + 손바닥 굽힘 1)
    {"name": "pinky_cmc",  "min":   0, "max": 30,  "rest":  5, "finger": "pinky", "segment": 0},
    {"name": "pinky_mcp",  "min":   0, "max": 80,  "rest": 10, "finger": "pinky", "segment": 1},
    {"name": "pinky_pip",  "min":   0, "max": 100, "rest": 10, "finger": "pinky", "segment": 2},
    {"name": "pinky_dip",  "min":   0, "max": 80,  "rest": 10, "finger": "pinky", "segment": 3},
]
N_HAND = len(HAND_DOFS)  # 20

# ──────────────────────────────────────────────────────────────
# 목적함수 Q 가중치  (수식 §6)
# ──────────────────────────────────────────────────────────────
W_F =  5.0    # F ∈ [0,1] 편안한 포즈일수록 Q 증가
W_S =  0.8    # S는 dissimilarity → 목적함수에서 −S 사용
W_C =  0.01   # C(ROM 겹침) 클수록 Q 증가

# ──────────────────────────────────────────────────────────────
# F 계산용 인덱스 / 비율  (수식 §4-2 IFA, §4-4 FA)
# ──────────────────────────────────────────────────────────────

# IFA: 굴신 DOF만 포함 (thumb_abd, pinky_cmc, wrist 제외)
_FINGER_FLEX_IDXS: dict[str, list[int]] = {
    "thumb":  [3, 5, 6],    # thumb_cmc, thumb_mcp, thumb_ip
    "index":  [7, 8, 9],
    "middle": [10, 11, 12],
    "ring":   [13, 14, 15],
    "pinky":  [17, 18, 19], # pinky_mcp, pip, dip
}

# FA: 손가락별 전체 DOF (thumb_abd, pinky_cmc 포함)
_FINGER_ALL_IDXS: dict[str, list[int]] = {
    "thumb":  [3, 4, 5, 6],
    "index":  [7, 8, 9],
    "middle": [10, 11, 12],
    "ring":   [13, 14, 15],
    "pinky":  [16, 17, 18, 19],
}

# IFA 자연 굴신 비율 (Hume et al. 1990)
_NATURAL_RATIOS: dict[str, list[float]] = {
    "thumb":  [0.35, 0.40, 0.25],  # cmc, mcp, ip
    "index":  [0.31, 0.47, 0.22],
    "middle": [0.31, 0.47, 0.22],
    "ring":   [0.31, 0.47, 0.22],
    "pinky":  [0.31, 0.47, 0.22],
}

# ──────────────────────────────────────────────────────────────
# 정규화용 사전 계산 벡터  (수식 §2)
# ──────────────────────────────────────────────────────────────
_DOF_MINS   = np.array([d["min"]  for d in HAND_DOFS], dtype=float)
_DOF_MAXS   = np.array([d["max"]  for d in HAND_DOFS], dtype=float)
_DOF_RESTS  = np.array([d["rest"] for d in HAND_DOFS], dtype=float)
_DOF_RANGES = _DOF_MAXS - _DOF_MINS + 1e-8  # 영 나눗셈 방지
