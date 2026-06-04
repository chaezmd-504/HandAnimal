"""
locomotion_mapper.py
--------------------
손 관절 → 이동 파라미터 (방향 + 속도) 변환 모듈.

관절 회전 매핑(MappingEngine / KeyframeMappingEngine)과 완전히 분리되어
병렬로 동작한다. main.py 에서 --locomotion 플래그로 활성화.

설정 파일:
    python/data/mappings/locomotion_config.json
    동물별 base_anim, speed_scale 등을 편집한다.
    base_anim 이름은 해당 동물 poses.json 의 _anim 값과 일치해야 한다.
    시작 시 로그에 사용 가능한 anim 목록이 출력된다.

방향 (yaw_delta):
    오른손 wrist_dev (좌우 꺾기) 기준 포즈 대비 변화량 → degrees/frame
    양수 = 시계 방향(우회전), 음수 = 반시계(좌회전)
    ※ wrist_dev 는 이 모듈이 독점한다 — 관절 매핑에서 반드시 제외.

속도 (speed):
    현재 손 포즈가 base_anim 시퀀스(Walk 등)에서 얼마나 빠르게 이동하는지.
    base_anim 포즈를 할 때만 speed 올라감.
    Attack/Death 같은 다른 동작은 Walk 키프레임과 거리가 멀어 speed 거의 0.

    계산:
      1. 현재 손 포즈 → base_anim 각 키프레임과의 L2 거리
      2. 가장 가까운 키프레임 위치 → Walk cursor (연속 값)
      3. cursor 변화량 × proximity_weight (Walk와 얼마나 비슷한가)
      4. EMA 평활화 → speed

cursor:
    base_anim 재생 위치 (0 ~ frame_count-1).
    KeyframeMappingEngine.get_sequential_pose(base_anim, cursor) 에 직접 전달 가능.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    from mapping.keyframe_engine import KeyframeMappingEngine

# hand_tracker.compute_dof_angles() 와 동일한 DOF 순서
_HAND_DOF_NAMES: list[str] = [
    "wrist_flex", "wrist_dev", "wrist_rot",
    "thumb_cmc",  "thumb_abd",  "thumb_mcp", "thumb_ip",
    "index_mcp",  "index_pip",  "index_dip",
    "middle_mcp", "middle_pip", "middle_dip",
    "ring_mcp",   "ring_pip",   "ring_dip",
    "pinky_cmc",  "pinky_mcp",  "pinky_pip", "pinky_dip",
]

# 설정 파일 기본값 (locomotion_config.json 에 없는 동물/필드 대비)
_DEFAULTS: dict = {
    "base_anim":       "",
    "speed_scale":     0.04,
    "dir_scale":       0.8,
    "dir_deadzone":    5.0,
    "speed_max":       2.0,
    "ema_alpha":       0.3,
    "proximity_scale": 0.03,   # 거리 감쇠 계수 — 클수록 Walk 에서 조금만 벗어나도 speed 급감
}

CONFIG_FILENAME = "locomotion_config.json"


def _to_vec(dof_dict: dict[str, float]) -> np.ndarray:
    return np.array([float(dof_dict.get(d, 0.0)) for d in _HAND_DOF_NAMES], dtype=float)


def load_config(mappings_dir: str) -> dict[str, dict]:
    """locomotion_config.json 로드. 없으면 빈 dict 반환."""
    path = os.path.join(mappings_dir, CONFIG_FILENAME)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as f:
        raw = json.load(f)
    # _comment/_hint 같은 메타 키 제거
    return {k: v for k, v in raw.items() if not k.startswith("_")}


class LocomotionMapper:
    """
    관절 회전 매핑과 독립적으로 동작하는 이동 파라미터 추출기.

    wrist_dev 를 독점 사용한다 — 다른 매핑에서 반드시 제외.
    설정은 locomotion_config.json 에서 읽는다.
    """

    RESERVED_DOFS: tuple[str, ...] = ("wrist_dev",)

    def __init__(self, animal: str, mappings_dir: str):
        self._mappings_dir = mappings_dir
        self._file_config  = load_config(mappings_dir)
        self._animal       = animal
        self._cfg          = self._build_cfg(animal)

        # 캘리브레이션 기준
        self._ref_wd_r: float = 0.0

        # 속도 EMA
        self._ema_speed: float = 0.0

        # Walk cursor 추적
        self._cursor:          float = 0.0
        self._prev_walk_cursor: Optional[float] = None   # 이전 프레임 cursor 위치

    # ────────────────────────────────────────────────────────────
    # 초기화 API
    # ────────────────────────────────────────────────────────────

    def _build_cfg(self, animal: str) -> dict:
        cfg = dict(_DEFAULTS)
        cfg.update(self._file_config.get(animal, {}))
        return cfg

    def set_animal(self, animal: str):
        """동물 전환 시 설정 재로드 및 상태 리셋."""
        self._animal            = animal
        self._cfg               = self._build_cfg(animal)
        self._cursor            = 0.0
        self._prev_walk_cursor  = None
        self._ema_speed         = 0.0
        print(f"[LocomotionMapper] 동물 전환: {animal}  "
              f"base_anim='{self._cfg['base_anim']}'")

    def calibrate(self, hands_dofs: dict[str, dict[str, float]]):
        """캘리브레이션 완료 시 wrist_dev 기준값 저장."""
        self._ref_wd_r         = float(hands_dofs.get("right", {}).get("wrist_dev", 0.0))
        self._ema_speed        = 0.0
        self._prev_walk_cursor = None
        print(f"[LocomotionMapper] 캘리브레이션 완료  "
              f"ref_wrist_dev_right={self._ref_wd_r:.1f}°")

    def print_available_anims(self, engine: "KeyframeMappingEngine"):
        """
        시작 시 호출 — 현재 동물의 사용 가능한 anim 목록과 현재 base_anim 출력.
        유저가 locomotion_config.json 의 base_anim 을 채울 때 참고.
        """
        anims = engine.anim_names()
        base  = self._cfg["base_anim"]
        print(f"[LocomotionMapper] '{self._animal}' 사용 가능한 anim: {anims}")
        if base and base in anims:
            print(f"[LocomotionMapper] base_anim='{base}' ✓")
        elif base:
            print(f"[LocomotionMapper] ⚠ base_anim='{base}' 가 anim 목록에 없음. "
                  f"locomotion_config.json 을 확인하세요.")
        else:
            print(f"[LocomotionMapper] ⚠ base_anim 미설정. "
                  f"locomotion_config.json 의 '{self._animal}'.base_anim 을 채우세요.")

    # ────────────────────────────────────────────────────────────
    # 매 프레임 호출
    # ────────────────────────────────────────────────────────────

    def update(
        self,
        hands_dofs:    dict[str, dict[str, float]],
        hand_detected: bool,
        engine:        "KeyframeMappingEngine",
    ) -> dict:
        """
        매 프레임 호출.

        Parameters
        ----------
        hands_dofs    : {"left": {...}, "right": {...}}
        hand_detected : 손 감지 여부
        engine        : KeyframeMappingEngine — Walk 키프레임 참조용

        Returns
        -------
        {speed, yaw_delta, cursor, valid}
        """
        if not hand_detected or not hands_dofs:
            self._ema_speed *= 0.85
            return {
                "speed":     round(self._ema_speed, 4),
                "yaw_delta": 0.0,
                "cursor":    round(self._cursor, 3),
                "valid":     False,
            }

        cfg = self._cfg

        # ── 1. 방향: 오른손 wrist_dev ────────────────────────
        wd_r    = float(hands_dofs.get("right", {}).get("wrist_dev", self._ref_wd_r))
        raw_dev = wd_r - self._ref_wd_r
        dz      = cfg["dir_deadzone"]

        if abs(raw_dev) < dz:
            yaw_delta = 0.0
        else:
            sign      = 1.0 if raw_dev > 0 else -1.0
            yaw_delta = (abs(raw_dev) - dz) * cfg["dir_scale"] * sign * -1.0

        # 방향 디버그: 30프레임마다 출력 (turning 문제 진단용)
        if not hasattr(self, '_dir_log_cnt'):
            self._dir_log_cnt = 0
        self._dir_log_cnt += 1
        if self._dir_log_cnt % 30 == 0:
            print(f"[DIR] wd_r={wd_r:.1f}  ref={self._ref_wd_r:.1f}  "
                  f"raw_dev={raw_dev:+.1f}  yaw={yaw_delta:+.2f}")

        # ── 2. 속도: base_anim cursor 변화량 ─────────────────
        base_anim = cfg["base_anim"]
        h_left  = _to_vec(hands_dofs.get("left",  {}))
        h_right = _to_vec(hands_dofs.get("right", {}))

        raw_delta = 0.0
        if base_anim:
            walk_cursor, min_dist = self._walk_cursor_from_pose(
                h_left, h_right, engine, base_anim
            )

            if walk_cursor is not None:
                # cursor 변화량 (wrap-around 처리)
                if self._prev_walk_cursor is not None:
                    n = engine.anim_frame_count(base_anim)
                    delta = abs(walk_cursor - self._prev_walk_cursor)
                    if n > 1 and delta > n / 2:
                        delta = n - delta           # 순환 경계 처리
                    # proximity weight: Walk 포즈에 가까울수록 1, 멀수록 0
                    # exp(-dist * scale) — scale 클수록 예민하게 감쇠
                    proximity = float(np.exp(-min_dist * cfg["proximity_scale"]))
                    raw_delta = delta * proximity

                self._prev_walk_cursor = walk_cursor
                self._cursor = walk_cursor

        alpha           = cfg["ema_alpha"]
        self._ema_speed = alpha * raw_delta + (1.0 - alpha) * self._ema_speed
        speed           = min(self._ema_speed * cfg["speed_scale"], cfg["speed_max"])

        return {
            "speed":     round(speed,     4),
            "yaw_delta": round(yaw_delta, 3),
            "cursor":    round(self._cursor, 3),
            "valid":     True,
        }

    # ────────────────────────────────────────────────────────────
    # 내부 유틸
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _walk_cursor_from_pose(
        h_left:    np.ndarray,
        h_right:   np.ndarray,
        engine:    "KeyframeMappingEngine",
        anim_name: str,
    ) -> tuple[Optional[float], float]:
        """
        현재 손 포즈가 anim_name 시퀀스의 어느 위치에 해당하는지 계산.

        Returns
        -------
        (cursor_pos, min_distance)
            cursor_pos  : 시퀀스 내 연속 위치 (None = 해당 anim 없음)
            min_distance: 가장 가까운 키프레임과의 L2 거리
        """
        data = engine._cache.get(engine.current_animal)
        if data is None:
            return None, float("inf")

        group = data["anim_groups"].get(anim_name, [])
        n = len(group)
        if n == 0:
            return None, float("inf")

        mode = data["mode"]

        distances = np.array([
            engine._distance(h_left, h_right, trig, mode)
            for trig, _ in group
        ])

        lo = int(np.argmin(distances))
        min_dist = float(distances[lo])

        # 인접 프레임 중 두 번째로 가까운 것으로 소수점 보간
        neighbors = [i for i in (lo - 1, lo + 1) if 0 <= i < n]
        if not neighbors:
            return float(lo), min_dist

        hi = min(neighbors, key=lambda i: distances[i])
        d_lo, d_hi = distances[lo], distances[hi]

        if d_lo + d_hi < 1e-6:
            return float(lo), min_dist

        t = d_lo / (d_lo + d_hi)   # lo에 가까울수록 0, hi에 가까울수록 1
        cursor = lo + (t if hi > lo else -t)
        return float(cursor), min_dist

    # ────────────────────────────────────────────────────────────
    # 프로퍼티
    # ────────────────────────────────────────────────────────────

    @property
    def base_anim(self) -> str:
        return self._cfg.get("base_anim", "")

    @property
    def current_cursor(self) -> float:
        return self._cursor
