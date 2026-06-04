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


def _load_bone_axes(poses_dir: str, animal: str) -> dict[str, tuple[str, int]]:
    """bone_map_{animal}.json 에서 axis 읽기. {joint_id: ("x"/"y"/"z", sign)}"""
    bone_map_path = os.path.join(poses_dir, f"bone_map_{animal}.json")
    axis_map: dict[str, tuple[str, int]] = {}
    if not os.path.exists(bone_map_path):
        return axis_map
    with open(bone_map_path, encoding="utf-8") as f:
        bm = json.load(f)
    for jid, info in bm.get("joint_map", {}).items():
        axis_map[jid] = (info.get("axis", "Y").lower(), 1)
    return axis_map

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
        self._bone_axes: dict[str, dict[str, tuple[str, int]]] = {}
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
            if fname.endswith("_mapping.json") and not fname.endswith("_body_mapping.json"):
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
            self._bone_axes[animal_name] = _load_bone_axes(self.poses_dir, animal_name)

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

    def transform_clamped(
        self,
        dof_input: dict,
        skeleton: "Optional[dict]" = None,
    ) -> dict[str, float]:
        """transform_bilateral 후 skeleton ROM으로 클리핑."""
        raw = self.transform_bilateral(dof_input)
        if skeleton is None:
            return raw
        joint_rom = {j["id"]: (j["min_angle"], j["max_angle"])
                     for j in skeleton.get("joints", [])}
        result = {}
        for jid, val in raw.items():
            if isinstance(val, dict):
                result[jid] = val  # keyframe은 xyz dict — 클리핑 불필요
            else:
                lo, hi = joint_rom.get(jid, (-360, 360))
                result[jid] = float(max(lo, min(hi, val)))
        return result

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
        new_triggers = [
            self._compute_trigger(pose, mapping_data, mode, mapping)
            for pose in data["animal_poses"]
        ]
        data["triggers"] = new_triggers

        # anim_groups 안의 trigger 레퍼런스도 갱신
        for anim, group in data["anim_groups"].items():
            data["anim_groups"][anim] = [
                (new_triggers[pose_idx], pose_idx)
                for _, pose_idx in group
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

        axes = self._bone_axes.get(self.current_animal, {})
        for joint_id in all_joints:
            bx = by = bz = 0.0
            for w, pose in zip(weights, animal_poses):
                v = pose.get(joint_id, {"x": 0.0, "y": 0.0, "z": 0.0})
                if isinstance(v, dict):
                    bx += float(w) * float(v.get("x", 0.0))
                    by += float(w) * float(v.get("y", 0.0))
                    bz += float(w) * float(v.get("z", 0.0))
                else:
                    # float → 올바른 축에 배치
                    ax, sign = axes.get(joint_id, ("z", 1))
                    val = float(w) * float(v) * sign
                    if ax == "x":   bx += val
                    elif ax == "y": by += val
                    else:           bz += val
            result[joint_id] = {"x": round(bx, 2), "y": round(by, 2), "z": round(bz, 2)}

        return result

    # ──────────────────────────────────────────────────────────
    # Sequential / 동적 블렌딩 API
    # ──────────────────────────────────────────────────────────

    def get_walk_rom(self, anim_name: str = "Walk") -> dict[str, tuple[float, float]]:
        """
        anim_name 키프레임에서 관절별 (min, max) 각도 추출.
        blend 모드에서 Walk ROM을 직접 매핑 범위로 사용하기 위함.

        Returns
        -------
        {joint_id: (min_angle, max_angle)}
        """
        data = self._cache.get(self.current_animal)
        if data is None:
            return {}
        group        = data["anim_groups"].get(anim_name, [])
        animal_poses = data["animal_poses"]

        rom_vals: dict[str, list[float]] = {}
        for _, pose_idx in group:
            pose = animal_poses[pose_idx]
            for jid, val in pose.items():
                if jid.startswith("_"):
                    continue
                if isinstance(val, dict):
                    # xyz dict → 가장 큰 절댓값 축의 부호 보존 값
                    fval = max(
                        (val.get("x", 0.0), val.get("y", 0.0), val.get("z", 0.0)),
                        key=abs,
                    )
                else:
                    fval = float(val)
                rom_vals.setdefault(jid, []).append(fval)

        return {
            jid: (min(vals), max(vals))
            for jid, vals in rom_vals.items()
            if len(vals) >= 2
        }

    def anim_names(self) -> list[str]:
        """현재 동물의 anim 이름 목록."""
        data = self._cache.get(self.current_animal)
        return list(data["anim_groups"].keys()) if data else []

    def anim_frame_count(self, anim_name: str) -> int:
        """해당 anim의 프레임 수."""
        data = self._cache.get(self.current_animal)
        if data is None:
            return 0
        return len(data["anim_groups"].get(anim_name, []))

    def min_distance_to_anim(
        self,
        h_left: np.ndarray,
        h_right: np.ndarray,
        anim_name: str,
    ) -> float:
        """현재 손 포즈 → 해당 anim 프레임들과의 최소 L2 거리."""
        data = self._cache.get(self.current_animal)
        if data is None:
            return float("inf")
        group = data["anim_groups"].get(anim_name, [])
        if not group:
            return float("inf")
        mode      = data["mode"]
        left_idx  = data.get("mapped_left_idx")
        right_idx = data.get("mapped_right_idx")
        return min(
            self._distance(h_left, h_right, trig, mode, left_idx, right_idx)
            for trig, _ in group
        )

    def peak_distance_to_anim(
        self,
        h_left: np.ndarray,
        h_right: np.ndarray,
        anim_name: str,
    ) -> float:
        """
        현재 손 포즈 → 해당 anim의 '피크 프레임' 트리거 포즈와의 L2 거리.

        피크 프레임: 관절 각도 합산이 가장 큰 프레임 (가장 특징적인 포즈).
        frame 0처럼 rest pose(모두 0)에 가까운 프레임을 제외하기 위함.

        min_distance_to_anim 대신 이 값으로 트리거를 판단하면
        "해당 애니메이션의 핵심 동작 포즈와 얼마나 비슷한가"를 측정할 수 있다.
        """
        data = self._cache.get(self.current_animal)
        if data is None:
            return float("inf")
        group        = data["anim_groups"].get(anim_name, [])
        animal_poses = data["animal_poses"]
        if not group:
            return float("inf")

        mode      = data["mode"]
        left_idx  = data.get("mapped_left_idx")
        right_idx = data.get("mapped_right_idx")

        # 각 프레임의 관절 각도 절댓값 합계 계산 → 가장 큰 프레임이 피크
        def _pose_magnitude(pose_idx: int) -> float:
            pose = animal_poses[pose_idx]
            total = 0.0
            for jid, val in pose.items():
                if jid.startswith("_"):
                    continue
                if isinstance(val, dict):
                    total += sum(abs(v) for v in val.values())
                else:
                    total += abs(float(val))
            return total

        peak_idx = max(range(len(group)), key=lambda i: _pose_magnitude(group[i][1]))
        peak_trig, _ = group[peak_idx]
        return self._distance(h_left, h_right, peak_trig, mode, left_idx, right_idx)

    def get_sequential_pose(self, anim_name: str, cursor: float) -> dict[str, dict]:
        """
        anim_name 시퀀스의 cursor 위치 포즈 반환.
        cursor: float [0.0, frame_count-1] — floor/ceil 사이 선형 보간.
        """
        data = self._cache.get(self.current_animal)
        if data is None:
            return {}
        group        = data["anim_groups"].get(anim_name, [])
        animal_poses = data["animal_poses"]
        axes         = self._bone_axes.get(self.current_animal, {})
        n = len(group)
        if n == 0:
            return {}

        cursor = max(0.0, min(float(cursor), n - 1))
        lo_idx = int(cursor)
        hi_idx = min(lo_idx + 1, n - 1)
        t      = cursor - lo_idx

        pose_lo = animal_poses[group[lo_idx][1]]
        pose_hi = animal_poses[group[hi_idx][1]]

        all_joints = (
            {k for k in pose_lo if not k.startswith("_")}
            | {k for k in pose_hi if not k.startswith("_")}
        )

        def to_xyz(pose: dict, jid: str) -> tuple[float, float, float]:
            v = pose.get(jid, {"x": 0.0, "y": 0.0, "z": 0.0})
            if isinstance(v, dict):
                return v.get("x", 0.0), v.get("y", 0.0), v.get("z", 0.0)
            ax, sign = axes.get(jid, ("z", 1))
            fv = float(v) * sign
            if ax == "x":   return fv,  0.0, 0.0
            elif ax == "y": return 0.0, fv,  0.0
            else:           return 0.0, 0.0, fv

        def _short(a: float, b: float) -> float:
            """Euler 최단 경로 보간 (±180° 경계 처리)."""
            d = (b - a + 180.0) % 360.0 - 180.0
            return a + d * t

        result: dict[str, dict] = {}
        for jid in all_joints:
            lx, ly, lz = to_xyz(pose_lo, jid)
            hx, hy, hz = to_xyz(pose_hi, jid)
            result[jid] = {
                "x": round(_short(lx, hx), 2),
                "y": round(_short(ly, hy), 2),
                "z": round(_short(lz, hz), 2),
            }
        return result

    @staticmethod
    def _distance(
        h_left: np.ndarray,
        h_right: np.ndarray,
        trigger: dict[str, np.ndarray],
        mode: str,
        left_idx: Optional[np.ndarray] = None,
        right_idx: Optional[np.ndarray] = None,
    ) -> float:
        """현재 손 포즈와 키프레임 트리거 포즈 사이의 L2 거리.
        left_idx/right_idx: 비교에 사용할 DOF 인덱스 (None이면 전체)."""
        if mode == "bilateral":
            lh = h_left[left_idx]   if left_idx  is not None else h_left
            lt = trigger["left"][left_idx]  if left_idx  is not None else trigger["left"]
            rh = h_right[right_idx] if right_idx is not None else h_right
            rt = trigger["right"][right_idx] if right_idx is not None else trigger["right"]
            return float(np.linalg.norm(lh - lt)) + float(np.linalg.norm(rh - rt))
        else:
            rh = h_right[right_idx] if right_idx is not None else h_right
            rt = trigger["right"][right_idx] if right_idx is not None else trigger["right"]
            return float(np.linalg.norm(rh - rt))

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

        # 매핑된 DOF 인덱스만 추출 (노이즈 있는 unmapped DOF 제외)
        mapped_left_idx  = sorted({_DOF_IDX[info["hand_dof_name"]]
                                    for info in mapping.values()
                                    if info.get("hand", "right") == "left"
                                    and info["hand_dof_name"] in _DOF_IDX})
        mapped_right_idx = sorted({_DOF_IDX[info["hand_dof_name"]]
                                    for info in mapping.values()
                                    if info.get("hand", "right") == "right"
                                    and info["hand_dof_name"] in _DOF_IDX})
        if not mapped_left_idx:   # unilateral 등 left 없을 때
            mapped_left_idx = list(range(_N_DOF))
        if not mapped_right_idx:
            mapped_right_idx = list(range(_N_DOF))

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

        # anim별 그룹핑 — {anim_name: [(trigger, pose_idx)]} (frame 순 정렬)
        anim_groups: dict[str, list[tuple[dict, int]]] = {}
        for i, (pose, trigger) in enumerate(zip(animal_poses, triggers)):
            anim = pose.get("_anim", "?")
            if anim not in anim_groups:
                anim_groups[anim] = []
            anim_groups[anim].append((trigger, i))
        for anim in anim_groups:
            anim_groups[anim].sort(key=lambda x: animal_poses[x[1]].get("_frame", 0))

        print(f"  [{animal}] anim 그룹: { {k: len(v) for k, v in anim_groups.items()} }")

        return {
            "animal_poses":     animal_poses,
            "triggers":         triggers,
            "mode":             mode,
            "mapping_data":     mapping_data,    # 캘리브레이션용 캐시
            "anim_groups":      anim_groups,     # sequential 블렌딩용
            "mapped_left_idx":  np.array(mapped_left_idx,  dtype=int),
            "mapped_right_idx": np.array(mapped_right_idx, dtype=int),
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
