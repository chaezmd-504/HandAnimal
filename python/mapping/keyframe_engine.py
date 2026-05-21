"""
keyframe_engine.py
-------------------
키프레임 블렌딩 기반 매핑 엔진.

기존 MappingEngine: hand_dof → scale → animal_joint  (연속, 직결 → wobbly)
이 엔진:           hand_pose → 키프레임 거리 → softmax 가중치 → 키프레임 블렌딩

원리
----
오프라인 준비 (set_animal 호출 시):
  각 동물 키프레임 P_i에 대해 기존 매핑의 역변환으로 손 트리거 포즈 G_i 를 계산.
    기존 변환:  a = a_ref + (h - h_ref) * scale
    역변환:     h = h_ref + (a - a_ref) / scale

런타임 (transform_bilateral 호출 시):
  1. 현재 손 포즈 → 각 G_i와의 L2 거리 계산
  2. 소프트맥스 가중치 w_i = softmax(-distance_i * temperature)
  3. 최종 포즈 = Σ w_i * P_i

temperature 파라미터:
  높을수록 가장 가까운 키프레임으로 빠르게 수렴 (snappy).
  낮을수록 여러 키프레임을 부드럽게 블렌딩.
  권장 범위: 4.0 ~ 15.0
"""

from __future__ import annotations

import json
import os
from typing import Optional

import numpy as np

# mapping_engine.py 와 동일한 순서 — hand_tracker.compute_dof_angles() 반환 키
_HAND_DOF_NAMES = [
    "wrist_flex", "wrist_dev", "wrist_rot",
    "thumb_cmc", "thumb_abd", "thumb_mcp", "thumb_ip",
    "index_mcp", "index_pip", "index_dip",
    "middle_mcp", "middle_pip", "middle_dip",
    "ring_mcp", "ring_pip", "ring_dip",
    "pinky_cmc", "pinky_mcp", "pinky_pip", "pinky_dip",
]
_N_DOF = len(_HAND_DOF_NAMES)
_DOF_IDX = {name: i for i, name in enumerate(_HAND_DOF_NAMES)}


def _dof_dict_to_vec(dof_dict: dict[str, float]) -> np.ndarray:
    return np.array([float(dof_dict.get(d, 0.0)) for d in _HAND_DOF_NAMES])


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - x.max()  # 수치 안정성
    e = np.exp(x)
    return e / e.sum()


class KeyframeMappingEngine:
    """
    동물 키프레임 블렌딩 매핑 엔진.
    MappingEngine 과 동일한 public API를 가지므로 main.py 에서 교체만 하면 된다.

    Parameters
    ----------
    mappings_dir : str
        {animal}_mapping.json 파일들이 있는 폴더
    poses_dir : str
        {animal}_poses.json 파일들이 있는 폴더
    temperature : float
        소프트맥스 온도. 높을수록 스냅, 낮을수록 부드러운 블렌딩 (기본 8.0)
    """

    def __init__(
        self,
        mappings_dir: str,
        poses_dir: str,
        temperature: float = 8.0,
    ):
        self.mappings_dir  = mappings_dir
        self.poses_dir     = poses_dir
        self.temperature   = temperature

        self._cache: dict[str, dict] = {}
        self.current_animal: Optional[str] = None
        self._animal_index = 0
        self._last_blend_info: list[tuple[float, str, int]] = []  # (weight, anim, frame)

    @property
    def ANIMALS(self) -> list[str]:
        """mappings_dir 에 있는 *_mapping.json 파일을 동적으로 스캔해 반환."""
        if not os.path.isdir(self.mappings_dir):
            return []
        names = []
        for fname in sorted(os.listdir(self.mappings_dir)):
            if fname.endswith("_mapping.json"):
                names.append(fname[: -len("_mapping.json")])
        return names

    # ──────────────────────────────────────────────────────────
    # 동물 선택 (MappingEngine 호환 API)
    # ──────────────────────────────────────────────────────────

    def set_animal(self, animal_name: str):
        if animal_name not in self.ANIMALS:
            raise ValueError(f"알 수 없는 동물: {animal_name}. 가능: {self.ANIMALS}")

        if animal_name not in self._cache:
            self._cache[animal_name] = self._build_keyframes(animal_name)

        self.current_animal = animal_name
        self._animal_index  = self.ANIMALS.index(animal_name)
        print(f"[KeyframeMappingEngine] 동물 전환: {animal_name}  "
              f"(키프레임 {len(self._cache[animal_name]['animal_poses'])}개, "
              f"temperature={self.temperature})")


    # ──────────────────────────────────────────────────────────
    # 런타임 변환 (MappingEngine 호환 API)
    # ──────────────────────────────────────────────────────────

    def transform(self, dof_dict: dict[str, float]) -> dict[str, dict]:
        """단일 손 20-DOF dict → 동물 관절 각도 dict ({"x","y","z"} per joint)."""
        return self._blend({"right": dof_dict, "left": dof_dict})

    def transform_bilateral(
        self,
        hands_dofs: dict[str, Optional[dict[str, float]]],
    ) -> dict[str, dict]:
        """양손 20-DOF dict → 동물 관절 각도 dict ({"x","y","z"} per joint)."""
        return self._blend(hands_dofs)

    def calibrate(self, hands_dofs: dict[str, dict[str, float]]):
        """현재 손 포즈를 기준 포즈로 설정 (c 키 캘리브레이션).

        파일 I/O 없이 캐시된 mapping_data만 수정 → 프레임 끊김 없음.
        """
        data = self._cache.get(self.current_animal)
        if data is None:
            return

        mapping_data = data["mapping_data"]
        mode         = data["mode"]

        if mode == "bilateral":
            for side in ("left", "right"):
                if hands_dofs.get(side):
                    mapping_data["reference_pose_H"][side].update(hands_dofs[side])
                    print(f"[KeyframeMappingEngine] 캘리브레이션 완료 ({side})")
        else:
            dof_dict = hands_dofs.get("right") or hands_dofs.get("left") or {}
            if dof_dict:
                mapping_data["reference_pose_H"].update(dof_dict)
                print("[KeyframeMappingEngine] 캘리브레이션 완료")

        # 트리거 재계산 (19 keyframes × 20 DOF — 수 ms 이내)
        mapping      = mapping_data["mapping"]
        data["triggers"] = [
            self._compute_trigger(pose, mapping_data, mode, mapping)
            for pose in data["animal_poses"]
        ]
        print("[KeyframeMappingEngine] 트리거 재계산 완료")

    # ──────────────────────────────────────────────────────────
    # 내부: 키프레임 블렌딩
    # ──────────────────────────────────────────────────────────

    def _blend(
        self,
        hands_dofs: dict[str, Optional[dict[str, float]]],
    ) -> dict[str, float]:
        data         = self._cache[self.current_animal]
        animal_poses = data["animal_poses"]   # list[dict]
        triggers     = data["triggers"]       # list[{"left": np.ndarray, "right": np.ndarray}]
        mode         = data["mode"]

        left_dofs  = hands_dofs.get("left")  or {}
        right_dofs = hands_dofs.get("right") or {}
        h_left  = _dof_dict_to_vec(left_dofs)
        h_right = _dof_dict_to_vec(right_dofs)

        # 각 키프레임과의 거리 → 소프트맥스 가중치
        distances = np.array([
            self._distance(h_left, h_right, trig, mode)
            for trig in triggers
        ])
        weights = _softmax(-distances * self.temperature)

        # 블렌드 정보 저장 (상위 가중치 키프레임)
        self._last_blend_info = sorted(
            [
                (float(w), pose.get("_anim", "?"), pose.get("_frame", 0))
                for w, pose in zip(weights, animal_poses)
            ],
            key=lambda x: x[0],
            reverse=True,
        )

        # 가중 평균 블렌딩 (축별)
        result: dict[str, dict] = {}
        all_joints: set[str] = set()
        for pose in animal_poses:
            all_joints.update(k for k in pose.keys() if not k.startswith("_"))

        for joint_id in all_joints:
            bx = by = bz = 0.0
            for w, pose in zip(weights, animal_poses):
                v = pose.get(joint_id, {"x": 0.0, "y": 0.0, "z": 0.0})
                if isinstance(v, dict):
                    bx += float(w) * float(v.get("x", 0.0))
                    by += float(w) * float(v.get("y", 0.0))
                    bz += float(w) * float(v.get("z", 0.0))
                else:
                    # 구형 단일 float 포즈 호환
                    bx += float(w) * float(v)
            result[joint_id] = {"x": round(bx, 2), "y": round(by, 2), "z": round(bz, 2)}

        return result

    @staticmethod
    def _distance(
        h_left: np.ndarray,
        h_right: np.ndarray,
        trigger: dict[str, np.ndarray],
        mode: str,
    ) -> float:
        """현재 손 포즈와 키프레임 트리거 포즈 사이의 L2 거리."""
        if mode == "bilateral":
            d_left  = float(np.linalg.norm(h_left  - trigger["left"]))
            d_right = float(np.linalg.norm(h_right - trigger["right"]))
            return d_left + d_right
        else:
            return float(np.linalg.norm(h_right - trigger["right"]))

    # ──────────────────────────────────────────────────────────
    # 내부: 키프레임 준비 (set_animal 시 1회 실행)
    # ──────────────────────────────────────────────────────────

    def _build_keyframes(self, animal: str) -> dict:
        """
        매핑 JSON + 동물 포즈 JSON 로드 → 각 키프레임의 손 트리거 포즈 계산.
        """
        # 매핑 로드
        mapping_path = os.path.join(self.mappings_dir, f"{animal}_mapping.json")
        if not os.path.exists(mapping_path):
            raise FileNotFoundError(
                f"매핑 파일 없음: {mapping_path}. "
                "generate_mappings.py 를 먼저 실행하세요."
            )
        with open(mapping_path, encoding="utf-8") as f:
            mapping_data = json.load(f)

        # 동물 포즈 로드
        poses_path = os.path.join(self.poses_dir, f"{animal}_poses.json")
        if not os.path.exists(poses_path):
            raise FileNotFoundError(
                f"동물 포즈 파일 없음: {poses_path}. "
                "extract_avatar_poses.py 를 먼저 실행하세요."
            )
        with open(poses_path, encoding="utf-8") as f:
            animal_poses: list[dict] = json.load(f)

        mode    = mapping_data.get("mode", "unilateral")
        mapping = mapping_data["mapping"]

        # 각 동물 키프레임 → 손 트리거 포즈 (역변환)
        triggers = [
            self._compute_trigger(pose, mapping_data, mode, mapping)
            for pose in animal_poses
        ]

        print(f"  [{animal}] 키프레임 {len(animal_poses)}개 준비완료 "
              f"(mode={mode})")
        for i, (pose, trig) in enumerate(zip(animal_poses, triggers)):
            anim  = pose.get("_anim", "?")
            frame = pose.get("_frame", "?")
            print(f"    P{i+1}: [{anim} frame={frame}]  "
                  f"trigger_right_norm={np.linalg.norm(trig['right']):.1f}")

        return {
            "animal_poses": animal_poses,
            "triggers":     triggers,
            "mode":         mode,
            "mapping_data": mapping_data,   # 캘리브레이션용 캐시
        }

    @staticmethod
    def _compute_trigger(
        animal_pose: dict[str, float],
        mapping_data: dict,
        mode: str,
        mapping: dict,
    ) -> dict[str, np.ndarray]:
        """
        동물 포즈 P_i → 손 트리거 포즈 G_i (역변환).

        기존 변환:  a = a_ref + (h - h_ref) * scale
        역변환:     h = h_ref + (a - a_ref) / scale
        """
        if mode == "bilateral":
            ref_H_all = mapping_data["reference_pose_H"]  # {"left": {...}, "right": {...}}
            ref_A     = mapping_data["reference_pose_A"]

            trigger_left  = np.array([ref_H_all["left"].get(d, 0.0)  for d in _HAND_DOF_NAMES])
            trigger_right = np.array([ref_H_all["right"].get(d, 0.0) for d in _HAND_DOF_NAMES])

            for joint_id, info in mapping.items():
                a_ref = float(ref_A.get(joint_id, 0.0))
                raw   = animal_pose.get(joint_id, a_ref)
                if isinstance(raw, dict):
                    rx, ry, rz = raw.get("x", 0.0), raw.get("y", 0.0), raw.get("z", 0.0)
                    a_val = max((rx, ry, rz), key=abs)
                else:
                    a_val = float(raw)
                scale = float(info["scale_factor"])
                dof   = info["hand_dof_name"]
                hand  = info.get("hand", "right")

                if abs(scale) < 1e-6 or dof not in _DOF_IDX:
                    continue

                ref_h = float(ref_H_all[hand].get(dof, 0.0))
                h_val = ref_h + (a_val - a_ref) / scale
                idx   = _DOF_IDX[dof]

                if hand == "left":
                    trigger_left[idx]  = h_val
                else:
                    trigger_right[idx] = h_val

            return {"left": trigger_left, "right": trigger_right}

        else:  # unilateral
            ref_H = mapping_data["reference_pose_H"]  # flat dict
            ref_A = mapping_data["reference_pose_A"]

            trigger = np.array([float(ref_H.get(d, 0.0)) for d in _HAND_DOF_NAMES])

            for joint_id, info in mapping.items():
                a_ref = float(ref_A.get(joint_id, 0.0))
                raw   = animal_pose.get(joint_id, a_ref)
                if isinstance(raw, dict):
                    rx, ry, rz = raw.get("x", 0.0), raw.get("y", 0.0), raw.get("z", 0.0)
                    a_val = max((rx, ry, rz), key=abs)
                else:
                    a_val = float(raw)
                scale = float(info["scale_factor"])
                dof   = info["hand_dof_name"]

                if abs(scale) < 1e-6 or dof not in _DOF_IDX:
                    continue

                ref_h = float(ref_H.get(dof, 0.0))
                h_val = ref_h + (a_val - a_ref) / scale
                trigger[_DOF_IDX[dof]] = h_val

            return {"right": trigger}
