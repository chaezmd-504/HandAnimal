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

# ⚠️ 이 목록은 mapping_optimizer.py HAND_DOFS 와 순서·이름이 동일해야 한다.
# hand_tracker.compute_dof_angles() 가 이 이름들로 DOF를 반환한다.
_HAND_DOF_NAMES = [
    "wrist_flex", "wrist_dev", "wrist_rot",
    "thumb_cmc", "thumb_abd", "thumb_mcp", "thumb_ip",
    "index_mcp", "index_pip", "index_dip",
    "middle_mcp", "middle_pip", "middle_dip",
    "ring_mcp", "ring_pip", "ring_dip",
    "pinky_cmc", "pinky_mcp", "pinky_pip", "pinky_dip",
]


class MappingEngine:
    """
    동물 전환 및 실시간 관절 변환 엔진.

    Attributes:
        mappings_dir: 매핑 JSON 파일들이 있는 폴더 경로
        current_animal: 현재 선택된 동물 이름
    """

    ANIMALS = ["spider", "butterfly", "fish"]

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
        self._print_guide()


    # ──────────────────────────────────────────────────────────
    # 실시간 변환
    # ──────────────────────────────────────────────────────────

    def _is_bilateral(self) -> bool:
        return self._cache[self.current_animal].get("mode") == "bilateral"

    def _compute_joint(
        self,
        info: dict,
        dof_dict: dict[str, float],
        ref_H: dict,
        ref_A: dict,
        joint_id: str,
    ) -> float:
        """DOF 이름으로 직접 조회 (비율 변환 불필요)."""
        dof_name  = info["hand_dof_name"]
        scale     = info["scale_factor"]

        h_current = float(dof_dict.get(dof_name, 0.0))
        h_ref     = float(ref_H.get(dof_name, h_current))
        delta_h   = h_current - h_ref

        # 데드존: 15° 이하 변화는 무시 (손가락 해부학적 결합 억제)
        DEAD_ZONE = 15.0
        if abs(delta_h) < DEAD_ZONE:
            delta_h = 0.0
        else:
            delta_h -= DEAD_ZONE * (1.0 if delta_h > 0 else -1.0)

        a_ref = float(ref_A.get(joint_id, 0.0))
        return round(float(a_ref + delta_h * scale), 2)

    def transform(self, dof_dict: dict[str, float]) -> dict[str, float]:
        """단일 손 20-DOF dict → 동물 관절 각도 dict."""
        if self.current_animal is None:
            raise RuntimeError("먼저 set_animal() 을 호출하세요.")

        if self._is_bilateral():
            return self.transform_bilateral({"left": dof_dict, "right": dof_dict})

        data  = self._cache[self.current_animal]
        ref_H = data["reference_pose_H"]
        ref_A = data["reference_pose_A"]

        return {
            joint_id: self._compute_joint(info, dof_dict, ref_H, ref_A, joint_id)
            for joint_id, info in data["mapping"].items()
        }

    def transform_bilateral(
        self,
        hands_dofs: dict[str, Optional[dict[str, float]]],
    ) -> dict[str, float]:
        """양손 20-DOF dict → 동물 관절 각도 dict.
        한 손이 미감지(None 또는 빈 dict)이면 해당 관절은 기준 포즈 유지.
        """
        if self.current_animal is None:
            raise RuntimeError("먼저 set_animal() 을 호출하세요.")

        data = self._cache[self.current_animal]

        if not self._is_bilateral():
            dof_dict = hands_dofs.get("right") or hands_dofs.get("left") or {}
            return self.transform(dof_dict)

        ref_A   = data["reference_pose_A"]
        result: dict[str, float] = {}

        for joint_id, info in data["mapping"].items():
            hand_side = info.get("hand", "right")
            dof_dict  = hands_dofs.get(hand_side) or {}
            ref_H     = data["reference_pose_H"][hand_side]

            if not dof_dict:
                result[joint_id] = round(float(ref_A.get(joint_id, 0.0)), 2)
            else:
                result[joint_id] = self._compute_joint(
                    info, dof_dict, ref_H, ref_A, joint_id
                )

        return result

    def _print_guide(self):
        """시작 시 기준 손 자세 + 손가락→다리 매핑 출력."""
        data = self._cache[self.current_animal]
        mode = data.get("mode", "unilateral")
        mapping = data["mapping"]
        ref_H   = data["reference_pose_H"]

        # 손가락별 대표 DOF (한 관절만 표시)
        FINGER_REPR = {
            "thumb_abd": "엄지(abd)", "thumb_ip": "엄지(ip)", "thumb_cmc": "엄지(cmc)",
            "index_mcp": "검지(mcp)", "index_pip": "검지(pip)", "index_dip": "검지(dip)",
            "middle_mcp": "중지(mcp)", "middle_pip": "중지(pip)", "middle_dip": "중지(dip)",
            "ring_mcp": "약지(mcp)", "ring_pip": "약지(pip)",
            "pinky_pip": "소지(pip)", "pinky_mcp": "소지(mcp)", "pinky_cmc": "소지(cmc)",
            "wrist_flex": "손목(flex)", "wrist_dev": "손목(dev)",
        }

        print("\n" + "━"*55)
        print(f"  {self.current_animal.upper()} 매핑 가이드  (mode: {mode})")
        print("━"*55)

        # 기준 손 자세
        if mode == "bilateral":
            for side in ("right", "left"):
                ref = ref_H.get(side, {})
                key_dofs = ["thumb_abd", "index_mcp", "middle_mcp", "pinky_pip"]
                vals = "  ".join(f"{FINGER_REPR.get(d,d)}={ref.get(d,0):.0f}°" for d in key_dofs)
                print(f"  기준 손 자세 ({side}): {vals}")
        else:
            ref = ref_H
            key_dofs = ["thumb_abd", "index_mcp", "middle_mcp", "pinky_pip"]
            vals = "  ".join(f"{FINGER_REPR.get(d,d)}={ref.get(d,0):.0f}°" for d in key_dofs)
            print(f"  기준 손 자세: {vals}")

        print(f"  → 이 자세에서 손가락 움직이면 다리가 반응 (데드존: 15°)")
        print()

        # 손가락→다리 매핑 표
        print("  손가락 → 다리 매핑")
        print(f"  {'다리':<20} {'손':>6}  {'DOF':<20} {'scale':>6}")
        print("  " + "-"*50)

        # leg 이름 기준 정렬
        for joint_id in sorted(mapping.keys()):
            info = mapping[joint_id]
            dof  = info["hand_dof_name"]
            hand = info.get("hand", "right")
            scale = info["scale_factor"]
            finger = FINGER_REPR.get(dof, dof)
            print(f"  {joint_id:<20} {hand:>6}  {finger:<20} {scale:>6.3f}")

        print("━"*55 + "\n")

    def calibrate(self, hands_dofs: dict[str, dict[str, float]]):
        """현재 손 포즈를 기준 포즈로 설정 (c 키 캘리브레이션).

        이후 모든 변환에서 delta_h = h_current - calibrated_ref 로 계산됨.
        → 캘리브레이션 시점 포즈에서 모든 관절이 ref_A (기준 포즈)에 위치.
        """
        data = self._cache[self.current_animal]
        if self._is_bilateral():
            for side in ("left", "right"):
                if hands_dofs.get(side):
                    data["reference_pose_H"][side].update(hands_dofs[side])
                    print(f"[MappingEngine] 캘리브레이션 완료 ({side})")
        else:
            dof_dict = hands_dofs.get("right") or hands_dofs.get("left") or {}
            if dof_dict:
                data["reference_pose_H"].update(dof_dict)
                print("[MappingEngine] 캘리브레이션 완료")

    def transform_clamped(
        self,
        dof_input: dict,
        skeleton: Optional[dict] = None,
    ) -> dict[str, float]:
        """transform 또는 transform_bilateral 후 skeleton ROM으로 클리핑."""
        first_val = next(iter(dof_input.values()), None)
        if isinstance(first_val, dict):
            raw = self.transform_bilateral(dof_input)
        else:
            raw = self.transform(dof_input)

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

    # 20-DOF dict로 테스트 (모든 키를 0으로 초기화, 필요한 것만 설정)
    def make_dof(overrides: dict) -> dict[str, float]:
        base = {name: 0.0 for name in _HAND_DOF_NAMES}
        base.update(overrides)
        return base

    left_open  = make_dof({})
    right_open = make_dof({})
    left_bend  = make_dof({"index_mcp": 80.0, "index_pip": 100.0, "index_dip": 80.0,
                            "middle_mcp": 80.0, "middle_pip": 100.0, "ring_mcp": 80.0,
                            "pinky_mcp": 70.0})
    right_bend = dict(left_bend)

    print("\n[테스트 1] 손 펼침 → 관절 값")
    result_open = engine.transform_bilateral({"left": left_open, "right": right_open})
    for jid, val in result_open.items():
        print(f"  {jid:25s}: {val:.2f}°")

    print("\n[테스트 2] 손 굽힘 → 관절 값")
    result_bend = engine.transform_bilateral({"left": left_bend, "right": right_bend})
    for jid, val in result_bend.items():
        print(f"  {jid:25s}: {val:.2f}°")

    print("\n[테스트 3] 손목 방향 → 관절 값")
    wrist_test = make_dof({"wrist_flex": 45.0, "wrist_dev": -15.0, "wrist_rot": 30.0})
    result_wrist = engine.transform_bilateral({"left": wrist_test, "right": wrist_test})
    for jid, val in result_wrist.items():
        print(f"  {jid:25s}: {val:.2f}°")
