"""
mapping_engine.py
------------------
런타임 매핑 엔진.
매핑 JSON (M, H, A)을 로드해서 실시간으로
손 각도 → 동물 관절 각도로 변환한다.

[수정 2026-04] _finger_angles_to_dof_vector()
  - 기존: 손가락 1개 각도를 MCP/PIP/DIP 모두에 동일하게 넣음
    → 다리 3관절이 항상 같이 움직이는 문제
  - 수정: 손가락 1개 각도(= 전체 굴신량)를 생리학적 비율로 MCP/PIP/DIP 분리
    MCP : PIP : DIP = 1 : 1.5 : 0.67 (실측 기반 근사)
    합산이 전체 각도와 일치하도록 정규화

단일 손 모드:
    engine = MappingEngine("data/mappings")
    engine.set_animal("spider")
    result = engine.transform(finger_angles)

양손 모드 (bilateral JSON):
    result = engine.transform_bilateral({"left": left_angles, "right": right_angles})
    # 한 손이 미감지면 None 또는 빈 dict 전달 → 기준 포즈 유지
"""

from __future__ import annotations

import json
import os
from typing import Optional

import numpy as np

# hand_tracker 에서 나오는 5개 손가락 각도 이름
FINGER_NAMES = ["thumb", "index", "middle", "ring", "pinky"]

# MappingOptimizer 의 HAND_DOFS 와 동일한 구조
_HAND_DOF_NAMES = [
    "thumb_cmc", "thumb_mcp", "thumb_ip",
    "index_mcp", "index_pip", "index_dip",
    "middle_mcp", "middle_pip", "middle_dip",
    "ring_mcp",   "ring_pip",  "ring_dip",
    "pinky_mcp",  "pinky_pip", "pinky_dip",
]
_FINGER_TO_DOFS: dict[str, list[int]] = {
    "thumb":  [0, 1, 2],
    "index":  [3, 4, 5],
    "middle": [6, 7, 8],
    "ring":   [9, 10, 11],
    "pinky":  [12, 13, 14],
}

# ── 핵심 수정 ──────────────────────────────────────────────────
# 손가락 전체 굴신 각도를 MCP/PIP/DIP 비율로 분배.
# 비율 근거: Hume et al.(1990) 기능적 ROM 실측 데이터
#   MCP: 전체의 ~31%, PIP: ~47%, DIP: ~22%  (합 = 1.0)
# thumb은 CMC/MCP/IP 구조가 달라 별도 비율 사용
_SEGMENT_RATIOS: dict[str, list[float]] = {
    "thumb":  [0.35, 0.40, 0.25],   # CMC, MCP, IP
    "index":  [0.31, 0.47, 0.22],   # MCP, PIP, DIP
    "middle": [0.31, 0.47, 0.22],
    "ring":   [0.31, 0.47, 0.22],
    "pinky":  [0.31, 0.47, 0.22],
}

# 각 DOF의 개별 ROM 한계 (mapping_optimizer.py HAND_DOFS와 동일)
_DOF_ROM: list[tuple[float, float]] = [
    (0, 60), (0, 60), (0, 80),    # thumb cmc/mcp/ip
    (0, 90), (0, 110), (0, 90),   # index mcp/pip/dip
    (0, 90), (0, 110), (0, 90),   # middle
    (0, 90), (0, 110), (0, 90),   # ring
    (0, 80), (0, 100), (0, 80),   # pinky
]


def _finger_angles_to_dof_vector(finger_angles: dict[str, float]) -> np.ndarray:
    """
    hand_tracker의 5-finger 굴신 각도를 15-DOF 벡터로 변환.

    [수정] MCP/PIP/DIP를 생리학적 비율로 분리 추정.
    손가락 전체 굴신량(finger_angles["index"] 등)이
    MediaPipe에서 나오는 3관절 합산 각도이므로,
    비율로 나눠 각 DOF에 배분한다.

    완전히 굽힌 손가락(180°) 기준:
      index_mcp ≈ 55°, index_pip ≈ 85°, index_dip ≈ 40° (합 ≈ 180°)
    """
    vec = np.zeros(15)
    for finger, dof_indices in _FINGER_TO_DOFS.items():
        total_angle = float(finger_angles.get(finger, 0.0))
        ratios = _SEGMENT_RATIOS[finger]
        for k, idx in enumerate(dof_indices):
            raw = total_angle * ratios[k]
            lo, hi = _DOF_ROM[idx]
            vec[idx] = float(np.clip(raw, lo, hi))
    return vec


class MappingEngine:
    """
    동물 전환 및 실시간 관절 변환 엔진.

    Attributes:
        mappings_dir: 매핑 JSON 파일들이 있는 폴더 경로
        current_animal: 현재 선택된 동물 이름
    """

    ANIMALS = ["spider", "butterfly", "fish", "octopus", "snake"]

    def __init__(self, mappings_dir: str):
        self.mappings_dir  = mappings_dir
        self._cache: dict[str, dict] = {}
        self.current_animal: Optional[str] = None
        self._animal_index = 0

    # ──────────────────────────────────────────────────────────
    # 동물 선택
    # ──────────────────────────────────────────────────────────

    def set_animal(self, animal_name: str):
        if animal_name not in self.ANIMALS:
            raise ValueError(f"알 수 없는 동물: {animal_name}. 가능: {self.ANIMALS}")

        if animal_name not in self._cache:
            path = os.path.join(self.mappings_dir, f"{animal_name}_mapping.json")
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"매핑 파일 없음: {path}. "
                    "generate_mappings.py 를 먼저 실행하세요."
                )
            with open(path, encoding="utf-8") as f:
                self._cache[animal_name] = json.load(f)

        self.current_animal = animal_name
        self._animal_index  = self.ANIMALS.index(animal_name)
        print(f"[MappingEngine] 동물 전환: {animal_name}")

    def next_animal(self) -> str:
        self._animal_index = (self._animal_index + 1) % len(self.ANIMALS)
        name = self.ANIMALS[self._animal_index]
        self.set_animal(name)
        return name

    def prev_animal(self) -> str:
        self._animal_index = (self._animal_index - 1) % len(self.ANIMALS)
        name = self.ANIMALS[self._animal_index]
        self.set_animal(name)
        return name

    # ──────────────────────────────────────────────────────────
    # 실시간 변환
    # ──────────────────────────────────────────────────────────

    def _is_bilateral(self) -> bool:
        return self._cache[self.current_animal].get("mode") == "bilateral"

    def _compute_joint(
        self,
        info: dict,
        finger_angles: dict[str, float],
        ref_H: dict,
        ref_A: dict,
        joint_id: str,
    ) -> float:
        dof_name  = info["hand_dof_name"]
        dof_idx   = info["hand_dof_idx"]
        scale     = info["scale_factor"]

        current_dof = _finger_angles_to_dof_vector(finger_angles)
        h_current   = current_dof[dof_idx]
        h_ref       = ref_H.get(dof_name, h_current)
        delta_h     = h_current - h_ref

        a_ref = ref_A.get(joint_id, 0.0)
        return round(float(a_ref + delta_h * scale), 2)

    def transform(self, finger_angles: dict[str, float]) -> dict[str, float]:
        if self.current_animal is None:
            raise RuntimeError("먼저 set_animal() 을 호출하세요.")

        if self._is_bilateral():
            return self.transform_bilateral({"left": finger_angles, "right": finger_angles})

        data  = self._cache[self.current_animal]
        ref_H = data["reference_pose_H"]
        ref_A = data["reference_pose_A"]

        return {
            joint_id: self._compute_joint(info, finger_angles, ref_H, ref_A, joint_id)
            for joint_id, info in data["mapping"].items()
        }

    def transform_bilateral(
        self,
        hands_angles: dict[str, Optional[dict[str, float]]],
    ) -> dict[str, float]:
        if self.current_animal is None:
            raise RuntimeError("먼저 set_animal() 을 호출하세요.")

        data = self._cache[self.current_animal]

        if not self._is_bilateral():
            angles = hands_angles.get("right") or hands_angles.get("left") or {}
            return self.transform(angles)

        ref_A   = data["reference_pose_A"]
        result: dict[str, float] = {}

        for joint_id, info in data["mapping"].items():
            hand_side     = info.get("hand", "right")
            finger_angles = hands_angles.get(hand_side) or {}
            ref_H         = data["reference_pose_H"][hand_side]

            if not finger_angles:
                result[joint_id] = round(float(ref_A.get(joint_id, 0.0)), 2)
            else:
                result[joint_id] = self._compute_joint(
                    info, finger_angles, ref_H, ref_A, joint_id
                )

        return result

    def transform_clamped(
        self,
        finger_angles_or_hands: dict,
        skeleton: Optional[dict] = None,
    ) -> dict[str, float]:
        first_val = next(iter(finger_angles_or_hands.values()), None)
        if isinstance(first_val, dict):
            raw = self.transform_bilateral(finger_angles_or_hands)
        else:
            raw = self.transform(finger_angles_or_hands)

        if skeleton is None:
            return raw

        joint_rom = {j["id"]: (j["min_angle"], j["max_angle"])
                     for j in skeleton.get("joints", [])}
        return {
            jid: float(np.clip(val, *joint_rom.get(jid, (-360, 360))))
            for jid, val in raw.items()
        }


# ──────────────────────────────────────────────────────────────
# 콘솔 출력 테스트
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    PYTHON_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mappings_dir = os.path.join(PYTHON_DIR, "data", "mappings")

    engine = MappingEngine(mappings_dir)

    try:
        engine.set_animal("spider")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    # 검지를 완전히 편 상태 (0°) vs 완전히 굽힌 상태 (180°)
    left_open  = {"thumb": 0.0, "index": 0.0, "middle": 0.0, "ring": 0.0, "pinky": 0.0}
    left_bend  = {"thumb": 120.0, "index": 180.0, "middle": 180.0, "ring": 160.0, "pinky": 140.0}
    right_open = {"thumb": 0.0, "index": 0.0, "middle": 0.0, "ring": 0.0, "pinky": 0.0}
    right_bend = {"thumb": 120.0, "index": 180.0, "middle": 180.0, "ring": 160.0, "pinky": 140.0}

    print("\n[테스트 1] 손 펼침 → 다리 관절 값")
    result_open = engine.transform_bilateral({"left": left_open, "right": right_open})
    for jid, val in result_open.items():
        print(f"  {jid:25s}: {val:.2f}°")

    print("\n[테스트 2] 손 완전 굽힘 → 다리 관절 값")
    result_bend = engine.transform_bilateral({"left": left_bend, "right": right_bend})
    for jid, val in result_bend.items():
        print(f"  {jid:25s}: {val:.2f}°")

    print("\n[테스트 3] MCP/PIP/DIP 분리 확인 (검지)")
    vec_open = _finger_angles_to_dof_vector({"index": 0.0})
    vec_bend = _finger_angles_to_dof_vector({"index": 180.0})
    dof_names = ["index_mcp", "index_pip", "index_dip"]
    for name, i in zip(dof_names, [3, 4, 5]):
        print(f"  {name}: 펼침={vec_open[i]:.1f}°  굽힘={vec_bend[i]:.1f}°")
    # 기대값: mcp≈55.8°, pip≈84.6°, dip≈39.6°
