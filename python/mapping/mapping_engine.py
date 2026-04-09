"""
mapping_engine.py
------------------
런타임 매핑 엔진.
매핑 JSON (M, H, A)을 로드해서 실시간으로
손 각도 → 동물 관절 각도로 변환한다.

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

# MappingOptimizer 의 HAND_DOFS 와 동일한 구조 (이름만 필요)
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


def _finger_angles_to_dof_vector(finger_angles: dict[str, float]) -> np.ndarray:
    """
    hand_tracker 의 5-finger 각도 딕셔너리를 15-DOF 벡터로 변환한다.
    같은 손가락의 MCP/PIP/DIP 는 동일 각도 값으로 채운다.
    (추후 각 관절을 독립적으로 추적하면 이 함수를 개선하면 된다)
    """
    vec = np.zeros(15)
    for finger, dof_indices in _FINGER_TO_DOFS.items():
        angle = finger_angles.get(finger, 0.0)
        for idx in dof_indices:
            vec[idx] = angle
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
        self._cache: dict[str, dict] = {}       # 로드된 매핑 캐시
        self.current_animal: Optional[str] = None
        self._animal_index = 0

    # ──────────────────────────────────────────────────────────
    # 동물 선택
    # ──────────────────────────────────────────────────────────

    def set_animal(self, animal_name: str):
        """동물 선택. 처음 호출 시 매핑 파일을 로드한다."""
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
        """다음 동물로 전환하고 이름을 반환한다."""
        self._animal_index = (self._animal_index + 1) % len(self.ANIMALS)
        name = self.ANIMALS[self._animal_index]
        self.set_animal(name)
        return name

    def prev_animal(self) -> str:
        """이전 동물로 전환하고 이름을 반환한다."""
        self._animal_index = (self._animal_index - 1) % len(self.ANIMALS)
        name = self.ANIMALS[self._animal_index]
        self.set_animal(name)
        return name

    # ──────────────────────────────────────────────────────────
    # 실시간 변환
    # ──────────────────────────────────────────────────────────

    def _is_bilateral(self) -> bool:
        """현재 로드된 매핑이 양손 모드인지 확인한다."""
        return self._cache[self.current_animal].get("mode") == "bilateral"

    def _compute_joint(
        self,
        info: dict,
        finger_angles: dict[str, float],
        ref_H: dict,
        ref_A: dict,
        joint_id: str,
    ) -> float:
        """관절 하나의 목표 각도를 계산한다."""
        dof_name  = info["hand_dof_name"]
        dof_idx   = info["hand_dof_idx"]
        scale     = info["scale_factor"]

        current_dof = _finger_angles_to_dof_vector(finger_angles)
        h_current   = current_dof[dof_idx]
        h_ref       = ref_H.get(dof_name, h_current)
        delta_h     = h_current - h_ref

        a_ref    = ref_A.get(joint_id, 0.0)
        return round(float(a_ref + delta_h * scale), 2)

    def transform(self, finger_angles: dict[str, float]) -> dict[str, float]:
        """
        단일 손 모드 변환.
        bilateral JSON이 로드된 경우 양손 모두 동일한 각도를 사용한다.

        Args:
            finger_angles: {"thumb": 30.0, "index": 72.3, ...}
        Returns:
            {"leg_R1_base": 12.4, ...}
        """
        if self.current_animal is None:
            raise RuntimeError("먼저 set_animal() 을 호출하세요.")

        if self._is_bilateral():
            # bilateral JSON이지만 단일 손으로 호출된 경우 → 양손 동일 각도 사용
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
        """
        양손 모드 변환.

        Args:
            hands_angles: {
                "left":  {"thumb": ..., "index": ..., ...},  # 미감지 시 None 또는 {}
                "right": {"thumb": ..., "index": ..., ...},
            }
        Returns:
            {"leg_R1_base": 12.4, "leg_L1_base": 8.2, ...}

        - 한 손이 None/빈 dict면 해당 손 관절을 기준 포즈(ref_A)로 유지한다.
        - 단일 손 JSON이 로드된 경우엔 "right" 값으로 transform() 에 위임한다.
        """
        if self.current_animal is None:
            raise RuntimeError("먼저 set_animal() 을 호출하세요.")

        data = self._cache[self.current_animal]

        if not self._is_bilateral():
            # 단일 손 JSON: right 우선, 없으면 left 사용
            angles = hands_angles.get("right") or hands_angles.get("left") or {}
            return self.transform(angles)

        ref_A   = data["reference_pose_A"]
        result: dict[str, float] = {}

        for joint_id, info in data["mapping"].items():
            hand_side     = info.get("hand", "right")
            finger_angles = hands_angles.get(hand_side) or {}
            ref_H         = data["reference_pose_H"][hand_side]

            if not finger_angles:
                # 해당 손 미감지 → 기준 포즈 유지
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
        """
        transform() 또는 transform_bilateral() + ROM 클리핑 버전.
        단일 손이면 dict[str, float], 양손이면 {"left": ..., "right": ...} 전달.
        skeleton 이 주어지면 각 관절의 min/max 로 클리핑한다.
        """
        # 양손 입력 감지: 값이 dict이면 bilateral
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

    left_angles = {
        "thumb": 20.0, "index": 65.0, "middle": 60.0, "ring": 55.0, "pinky": 40.0,
    }
    right_angles = {
        "thumb": 35.0, "index": 80.0, "middle": 75.0, "ring": 70.0, "pinky": 50.0,
    }

    print("\n[테스트 1] 양손 transform_bilateral()")
    result = engine.transform_bilateral({"left": left_angles, "right": right_angles})
    left_joints  = {k: v for k, v in result.items() if "_l" in k.lower()}
    right_joints = {k: v for k, v in result.items() if "_r" in k.lower()}
    print("  왼손 → 왼쪽 관절:")
    for jid, val in left_joints.items():
        print(f"    {jid:25s}: {val:.2f}°")
    print("  오른손 → 오른쪽 관절:")
    for jid, val in right_joints.items():
        print(f"    {jid:25s}: {val:.2f}°")

    print("\n[테스트 2] 오른손만 감지된 경우 (left=None)")
    result2 = engine.transform_bilateral({"left": None, "right": right_angles})
    print("  왼쪽 관절은 기준 포즈 유지, 오른쪽만 변환됨")
    for jid, val in result2.items():
        print(f"    {jid:25s}: {val:.2f}°")
