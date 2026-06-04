"""
main.py
--------
HandAvatar 전체 파이프라인 진입점.

실행:
    conda activate capstone_env
    python main.py [--animal spider] [--port 8765] [--no-window] [--no-gesture]

파이프라인:
    웹캠 → HandTracker → OcclusionHandler → MappingEngine → WebSocketServer → Unity

종료: 'q' 키 또는 Ctrl+C
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from typing import Optional

import cv2
import numpy as np

_PYTHON_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PYTHON_DIR)

import mediapipe as mp

from tracking.hand_tracker import (
    download_model,
    MODEL_PATH,
    compute_dof_angles,
    draw_landmarks,
)
from tracking.occlusion_handler import OcclusionHandler
from mapping.mapping_engine import MappingEngine
from mapping.keyframe_engine import KeyframeMappingEngine
from mapping.locomotion_mapper import LocomotionMapper
from communication.websocket_server import WebSocketServer

BaseOptions           = mp.tasks.BaseOptions
HandLandmarker        = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
VisionRunningMode     = mp.tasks.vision.RunningMode

DATA_DIR     = os.path.join(_PYTHON_DIR, "data")
MAPPINGS_DIR = os.path.join(DATA_DIR, "mappings")
POSES_DIR    = os.path.join(DATA_DIR, "animal_skeletons")

HAND_COLOR = {"left": (0, 255, 0), "right": (255, 100, 0)}

_bone_axes_cache: dict[str, dict[str, tuple[str, int]]] = {}

_DOF_FINGER = {
    "thumb":  "thumb",
    "index":  "index finger",
    "middle": "middle finger",
    "ring":   "ring finger",
    "pinky":  "pinky",
    "wrist":  "wrist",
}

def _calib_feedback(calib_warnings: dict) -> str:
    """
    calibrate() 반환값 → 화면 표시용 짧은 피드백 문자열.
    diff 가 큰 순서로 최대 2개 표시.
    예: "Extend thumb  |  Bend index finger"
    """
    items = []
    all_dofs: list[tuple[str, float, float, float]] = []
    for dof_list in calib_warnings.values():
        all_dofs.extend(dof_list)
    all_dofs.sort(key=lambda x: x[3], reverse=True)  # diff 내림차순

    seen_fingers: set[str] = set()
    for dof_name, user_val, ref_val, _ in all_dofs:
        finger = next((f for f in _DOF_FINGER if dof_name.startswith(f)), None)
        if finger is None or finger in seen_fingers:
            continue
        seen_fingers.add(finger)
        fname = _DOF_FINGER[finger]

        if dof_name.endswith("_abd"):
            action = "Spread" if user_val < ref_val else "Close"
            items.append(f"{action} {fname}")
        else:
            # MCP/PIP/DIP: 양수 = 굴곡(bend)
            action = "Bend" if user_val < ref_val else "Extend"
            items.append(f"{action} {fname}")

        if len(items) >= 2:
            break

    return "  |  ".join(items) if items else "Match the reference pose"


def _get_bone_axes(animal: str) -> dict[str, tuple[str, int]]:
    """
    {animal}_poses.json 에서 각 관절의 지배 축과 부호를 자동 계산 (캐시).
    반환: {joint_id: ('x'|'y'|'z', +1|-1)}
    """
    if animal in _bone_axes_cache:
        return _bone_axes_cache[animal]

    poses_path = os.path.join(POSES_DIR, f"{animal}_poses.json")
    if not os.path.exists(poses_path):
        _bone_axes_cache[animal] = {}
        return {}

    with open(poses_path, encoding="utf-8") as f:
        poses = json.load(f)

    # bone_map에서 명시된 축 읽기 (추측 없이)
    bone_map_path = os.path.join(POSES_DIR, f"bone_map_{animal}.json")
    axis_map: dict[str, str] = {}
    if os.path.exists(bone_map_path):
        with open(bone_map_path, encoding="utf-8") as f:
            bm = json.load(f)
        for jid, info in bm.get("joint_map", {}).items():
            axis_map[jid] = info.get("axis", "Y").lower()

    joints: set[str] = set(k for p in poses for k in p if not k.startswith("_"))
    result: dict[str, tuple[str, int]] = {}
    for jid in joints:
        # 축: bone_map 우선, 없으면 poses에서 dominant axis 추측
        if jid in axis_map:
            dom_ax = axis_map[jid]
        else:
            sx = sy = sz = 0.0
            for p in poses:
                v = p.get(jid)
                if not isinstance(v, dict):
                    continue
                sx += abs(v.get("x", 0.0))
                sy += abs(v.get("y", 0.0))
                sz += abs(v.get("z", 0.0))
            dom_ax = max(("x", sx), ("y", sy), ("z", sz), key=lambda t: t[1])[0]

        sign_sum = sum(
            p.get(jid, {}).get(dom_ax, 0.0)
            for p in poses if isinstance(p.get(jid), dict)
        )
        result[jid] = (dom_ax, -1 if sign_sum < 0 else 1)

    _bone_axes_cache[animal] = result
    return result


def _float_joints_to_xyz(joints: dict, animal: str) -> dict[str, dict]:
    """
    direct 모드의 float 출력을 {x,y,z} 포맷으로 변환.
    각 관절의 축과 부호는 실제 애니메이션 포즈 데이터에서 자동 계산.
    """
    axes = _get_bone_axes(animal)
    result = {}
    for jid, val in joints.items():
        ax, sign = axes.get(jid, ("y", 1))
        signed_val = float(val) * sign
        result[jid] = {
            "x": signed_val if ax == "x" else 0.0,
            "y": signed_val if ax == "y" else 0.0,
            "z": signed_val if ax == "z" else 0.0,
        }
    return result

_CALIB_DURATION = 5.0   # 카운트다운 초


# ──────────────────────────────────────────────────────────────
# 캘리브레이션 가이드 그리기
# ──────────────────────────────────────────────────────────────

def _draw_finger(img, x, y, base_angle, L1, L2, L3, bend1, bend2, color, thickness=3):
    """세 마디 손가락 선분을 그린다."""
    def _pt(px, py, ang, length):
        rad = math.radians(ang)
        return int(px + length * math.sin(rad)), int(py - length * math.cos(rad))

    p1 = _pt(x, y, base_angle, L1)
    cv2.line(img, (int(x), int(y)), p1, color, thickness)
    cv2.circle(img, p1, thickness + 1, color, -1)

    p2 = _pt(*p1, base_angle + bend1, L2)
    cv2.line(img, p1, p2, color, thickness)
    cv2.circle(img, p2, thickness + 1, color, -1)

    p3 = _pt(*p2, base_angle + bend1 + bend2, L3)
    cv2.line(img, p2, p3, color, thickness)
    cv2.circle(img, p3, thickness, color, -1)


def _draw_hand_guide(img, cx, cy, scale=1.0, color=(100, 220, 100)):
    """
    참조 포즈(MCP 20°, PIP 15°, DIP 10°)를 나타내는 손 가이드를 그린다.
    cx, cy: 손바닥 중심
    """
    pw   = int(80 * scale)
    ph   = int(55 * scale)
    L1   = int(32 * scale)
    L2   = int(24 * scale)
    L3   = int(18 * scale)
    palm_top = cy - ph // 2

    # 손바닥
    cv2.rectangle(img, (cx - pw // 2, palm_top), (cx + pw // 2, cy + ph // 2), color, -1)
    cv2.rectangle(img, (cx - pw // 2, palm_top), (cx + pw // 2, cy + ph // 2),
                  (200, 255, 200), 2)

    # 검지~소지 (4개)
    configs = [
        (cx - 30, palm_top, -12, L1,              L2,              L3),
        (cx - 10, palm_top - 5, -4, L1,            L2,              L3),
        (cx + 10, palm_top - 3,  4, int(L1 * .95), int(L2 * .95),  int(L3 * .95)),
        (cx + 28, palm_top,     10, int(L1 * .80), int(L2 * .80),  int(L3 * .80)),
    ]
    for fx, fy, base, l1, l2, l3 in configs:
        _draw_finger(img, fx, fy, base, l1, l2, l3, 20, 15, color)

    # 엄지
    _draw_finger(img, cx - pw // 2 + 5, cy, -80,
                 int(25 * scale), int(20 * scale), int(15 * scale),
                 25, 20, color)


def _draw_calib_overlay(
    frame,
    remaining: float,
    hands_detected: bool,
    retry: bool = False,
    reject_msg: str = "",
):
    """캘리브레이션 가이드 오버레이를 frame 위에 그린다."""
    h, w = frame.shape[:2]

    # 반투명 어두운 배경
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)

    # 오른쪽: 손 가이드
    guide_cx = w * 3 // 4
    guide_cy = h // 2
    _draw_hand_guide(frame, guide_cx, guide_cy, scale=1.2)
    cv2.putText(frame, "Reference Pose", (guide_cx - 70, guide_cy + int(90 * 1.2)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 255, 180), 1)

    # 왼쪽: 안내 텍스트
    cv2.putText(frame, "Calibration", (24, 55),
                cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 2)
    for i, line in enumerate([
        "Slightly bend both hands",
        "like the guide on the right.",
        "",
        "Thumb: ~20 deg",
        "Index~Ring: ~15-20 deg",
    ]):
        cv2.putText(frame, line, (24, 100 + i * 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.62, (200, 200, 200), 1)

    # 카운트다운 숫자 (중앙)
    count_str   = str(int(remaining) + 1)
    count_color = (0, 255, 255) if hands_detected else (80, 80, 255)
    text_size   = cv2.getTextSize(count_str, cv2.FONT_HERSHEY_SIMPLEX, 5.0, 8)[0]
    count_x     = w // 2 - text_size[0] // 2
    cv2.putText(frame, count_str, (count_x, h // 2 + 40),
                cv2.FONT_HERSHEY_SIMPLEX, 5.0, count_color, 8)

    # 하단 상태 표시
    if reject_msg:
        cv2.putText(frame, reject_msg, (24, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 80, 255), 2)
    elif retry:
        cv2.putText(frame, "Hand not detected. Retrying...", (24, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 80, 255), 2)
    elif hands_detected:
        cv2.putText(frame, "Hands detected  -  Hold the pose", (24, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 220, 100), 2)
    else:
        cv2.putText(frame, "Show both hands to the camera", (24, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 120, 255), 2)


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="HandAvatar 파이프라인")
    p.add_argument("--animal", default="spider",
                   choices=["spider", "butterfly", "fish", "horse"],
                   help="동물 선택 (기본값: spider)")
    p.add_argument("--port", type=int, default=8765,
                   help="WebSocket 포트 (기본값: 8765)")
    p.add_argument("--no-window", action="store_true",
                   help="OpenCV 미리보기 창 비활성화")
    p.add_argument("--mapping", choices=["keyframe", "direct", "blend"], default="keyframe",
                   help="매핑 방식: keyframe=키프레임 블렌딩, direct=직접 매핑, blend=동적 α 블렌딩(권장)")
    p.add_argument("--temperature", type=float, default=8.0,
                   help="[keyframe] 소프트맥스 온도. 높을수록 스냅, 낮을수록 부드럽게 (기본 8.0)")
    p.add_argument("--threshold", type=float, default=40.0,
                   help="[blend] 트리거 min_distance 임계값 (기본 40.0, 낮을수록 예민)")
    p.add_argument("--action-anims", type=str, default="Attack1,Attack2,Death",
                   help="[blend] 트리거 애니메이션 목록, 쉼표 구분 (기본 Attack1,Attack2,Death)")
    p.add_argument("--dist-log", type=int, default=0,
                   help="[blend] N프레임마다 anim 거리 콘솔 출력 (0=비활성, 권장 30)")
    p.add_argument("--base-anim", type=str, default="Walk",
                   help="[blend] Walk ROM 기준 애니메이션 이름 (기본 Walk)")
    p.add_argument("--locomotion", action="store_true",
                   help="로코모션 모듈 활성화: wrist_dev → 방향, 손가락 속도 → 이동. "
                        "활성화 시 wrist_dev 는 관절 매핑에서 제외됨.")
    p.add_argument("--head-dir", action="store_true",
                   help="방향 제어를 wrist_dev 대신 머리(얼굴) x위치로 변경. "
                        "--locomotion 과 함께 사용.")
    return p.parse_args()


# ──────────────────────────────────────────────────────────────
# 메인 루프
# ──────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    download_model()

    occlusion       = {"left": OcclusionHandler(), "right": OcclusionHandler()}
    occlusion_world = {"left": OcclusionHandler(), "right": OcclusionHandler()}
    _dof_ema: dict[str, dict[str, float]] = {}   # DOF 각도 EMA 스무딩 상태
    _EMA_ALPHA = 0.7   # 높을수록 반응 빠름, 낮을수록 부드러움 (0.5~0.8 권장)
    server    = WebSocketServer(port=args.port)

    if args.mapping == "keyframe":
        engine = KeyframeMappingEngine(
            MAPPINGS_DIR, POSES_DIR, temperature=args.temperature
        )
        print(f"[INFO] 매핑 모드: keyframe (temperature={args.temperature})")
    elif args.mapping == "blend":
        engine = KeyframeMappingEngine(
            MAPPINGS_DIR, POSES_DIR, temperature=args.temperature
        )
        _engine_direct = MappingEngine(MAPPINGS_DIR)
        print(f"[INFO] 매핑 모드: blend (Direct + Sequential, temperature={args.temperature})")
    else:
        engine = MappingEngine(MAPPINGS_DIR)
        print("[INFO] 매핑 모드: direct (연속 각도 매핑)")

    try:
        engine.set_animal(args.animal)
        if args.mapping == "blend":
            _engine_direct.set_animal(args.animal)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        print("[HINT] python scripts/generate_mappings.py 와 "
              "extract_avatar_poses.py 를 먼저 실행하세요.")
        sys.exit(1)

    # ── 로코모션 모듈 초기화 ──────────────────────────────────
    _loco: LocomotionMapper | None = None
    if args.locomotion:
        if not isinstance(engine, KeyframeMappingEngine):
            print("[WARN] --locomotion 은 keyframe/blend 모드에서만 동작합니다. "
                  "direct 모드에서는 비활성.")
        else:
            _loco = LocomotionMapper(args.animal, MAPPINGS_DIR)
            _loco.print_available_anims(engine)
            print(f"[INFO] 로코모션 활성  reserved_dofs={LocomotionMapper.RESERVED_DOFS}")

    # blend 모드 상태머신 초기화
    _TRIGGER_ANIMS     = set(args.action_anims.split(",")) if args.mapping == "blend" else set()
    _TRIG_FRACTION     = args.threshold / 100.0  # 피크값 도달 비율 (--threshold 65 → 65%)
    _TRIGGER_HOLD      = 10              # 트리거 발동에 필요한 연속 프레임 수 (노이즈 오발동 방지)
    _anim_state        = "normal"         # "normal" | "trigger"
    _trigger_anim      = None
    _trigger_frames    = 0                # 남은 트리거 프레임 수
    _trigger_cursor    = 0.0             # 키프레임 재생 위치
    _cooldown_frames   = 0
    _trigger_hold_cnt: dict[str, int] = {}  # {anim: 조건 유지 프레임 수}

    # 트리거 애니메이션별 피크 프레임 관절 (캘리브레이션 후 채워짐)
    _trigger_peak_joints: dict[str, dict] = {}   # {anim: {jid: {x,y,z}}}
    _trigger_cmp_joints:  dict[str, list] = {}   # {anim: [비교할 joint_id 목록]}

    # Walk ROM 추출 (blend 모드)
    _walk_skeleton: dict = {}
    _prev_h_right:  Optional[np.ndarray] = None
    _vel_ema:       float = 0.0
    _VEL_SCALE:     float = 0.05         # velocity → speed 배율

    # rule-based 트리거 규칙 로드 (locomotion_config.json에서)
    # {anim: {"dof": str, "hand": str, "delta": float}}
    _trigger_rules: dict = {}
    # 캘리브레이션 완료 후 채워질 기준값 {hand: {dof: ref_val}}
    _trigger_ref:   dict = {}

    if args.mapping == "blend":
        _walk_rom = engine.get_walk_rom(args.base_anim)
        if _walk_rom:
            _walk_skeleton = {
                "joints": [
                    {"id": jid, "min_angle": mn, "max_angle": mx}
                    for jid, (mn, mx) in _walk_rom.items()
                ]
            }
            print(f"[INFO] Walk ROM 로드: {len(_walk_rom)}개 관절 "
                  f"(anim='{args.base_anim}')")
        else:
            print(f"[WARN] Walk ROM 추출 실패 — base_anim='{args.base_anim}'이 "
                  f"poses.json에 없는지 확인. 전체 skeleton ROM 사용.")

        # 트리거 규칙 로드
        from mapping.locomotion_mapper import load_config as _load_loco_cfg
        _loco_cfg = _load_loco_cfg(MAPPINGS_DIR)
        _trigger_rules = _loco_cfg.get(args.animal, {}).get("triggers", {})
        # _comment 키 제거
        _trigger_rules = {k: v for k, v in _trigger_rules.items()
                          if not k.startswith("_") and isinstance(v, dict)}
        if _trigger_rules:
            _rule_summary = {k: f"{v['hand']}.{v['dof']} Δ{v['delta']}°" for k, v in _trigger_rules.items()}
            print(f"[INFO] 트리거 규칙 로드: {_rule_summary}")
        else:
            print(f"[WARN] 트리거 규칙 없음. locomotion_config.json의 '{args.animal}'.triggers 를 확인하세요.")

        print(f"[INFO] blend 설정: base_anim='{args.base_anim}'")

    # skeleton ROM 클리핑용
    _skel_path = os.path.join(POSES_DIR, f"{args.animal}.json")
    _skeleton  = None
    if os.path.exists(_skel_path):
        with open(_skel_path, encoding="utf-8") as _f:
            _skeleton = json.load(_f)
        print(f"[INFO] skeleton ROM 로드: {_skel_path}")

    # 별도 body mapping 로드 (있으면)
    _body_mapping  = None
    _body_ref_H    = {}
    _body_map_path = os.path.join(MAPPINGS_DIR, f"{args.animal}_body_mapping.json")
    if os.path.exists(_body_map_path):
        with open(_body_map_path, encoding="utf-8") as _f:
            _body_mapping = json.load(_f).get("mapping", {})
        # reference_pose_H는 메인 mapping.json에서 읽음
        _main_map_path = os.path.join(MAPPINGS_DIR, f"{args.animal}_mapping.json")
        if os.path.exists(_main_map_path):
            with open(_main_map_path, encoding="utf-8") as _f:
                _main_map = json.load(_f)
            ref = _main_map.get("reference_pose_H", {})
            # bilateral 구조면 {"left": {...}, "right": {...}}, flat이면 그대로
            if isinstance(ref, dict) and "right" in ref:
                _body_ref_H = ref["right"]
            else:
                _body_ref_H = ref
        print(f"[INFO] body mapping 로드: {_body_map_path}  ({len(_body_mapping)}관절)")

    server.start()

    # ── 머리 방향 제어 초기화 ─────────────────────────────────
    _face_detector   = None
    _head_yaw_delta: float = 0.0
    _HEAD_DEADZONE   = 0.12   # ±12% 이내 직진 (jitter 흡수)
    _HEAD_SCALE      = 4.0    # x offset → °/frame
    _head_ref_x      = 0.5
    _head_miss_cnt   = 0      # 연속 미감지 프레임 수
    _HEAD_MISS_RESET = 8      # 이 프레임 이상 미감지 시 yaw=0 리셋

    if args.head_dir and args.locomotion:
        _cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _face_detector = cv2.CascadeClassifier(_cascade_path)
        if _face_detector.empty():
            print(f"[ERROR] Haar cascade 로드 실패: {_cascade_path}")
            _face_detector = None
        else:
            print(f"[INFO] 머리 방향 제어 활성화 — cascade: {_cascade_path}")

    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=VisionRunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] 웹캠을 열 수 없습니다.")
        server.stop()
        sys.exit(1)

    print(f"[INFO] 캘리브레이션 시작 — {_CALIB_DURATION:.0f}초 카운트다운")

    # ── 캘리브레이션 상태 ──────────────────────────────────────
    calib_done            = False            # --no-window여도 캘리브레이션은 항상 실행
    calib_start           = time.time()
    calib_retry_until     = 0.0             # 재시도 메시지 표시 종료 시각
    calib_reject_msg      = ""              # g* 불일치 거부 메시지

    frame_count = 0
    t_start     = time.time()

    with HandLandmarker.create_from_options(options) as landmarker:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("[ERROR] 프레임 읽기 실패.")
                break

            h, w     = frame.shape[:2]
            ts_ms    = int(time.time() * 1000)
            mp_image = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
            )
            result = landmarker.detect_for_video(mp_image, ts_ms)

            # ── 손 감지 (캘리브레이션 중에도 실행) ────────────
            hands_angles: dict[str, dict[str, float]] = {}
            if result.hand_landmarks and result.hand_world_landmarks:
                for landmarks, world_landmarks, handedness_list in zip(
                    result.hand_landmarks,
                    result.hand_world_landmarks,
                    result.handedness,
                ):
                    side  = handedness_list[0].category_name.lower()
                    color = HAND_COLOR.get(side, (0, 255, 0))
                    if not args.no_window or not calib_done:
                        draw_landmarks(frame, landmarks, h, w, color=color)

                    # 이미지 좌표: occlusion 핸들러 + 2D DOF 계산용으로 캡처
                    img_filtered   = occlusion[side].process(landmarks)

                    # 월드 좌표: 손목 DOF 계산용
                    world_filtered = occlusion_world[side].process(world_landmarks)

                    # 손가락 굴신각은 2D 이미지 좌표에서 계산 (손목 방향 무관하게 안정적)
                    raw_angles     = compute_dof_angles(world_filtered, img_filtered)

                    # EMA 스무딩: 손가락 위치 노이즈 추가 억제
                    prev = _dof_ema.get(side, raw_angles)
                    smoothed = {
                        k: _EMA_ALPHA * raw_angles[k] + (1.0 - _EMA_ALPHA) * prev.get(k, raw_angles[k])
                        for k in raw_angles
                    }
                    _dof_ema[side] = smoothed
                    hands_angles[side] = smoothed

            # ── 캘리브레이션 단계 ──────────────────────────────
            if not calib_done:
                now       = time.time()
                remaining = _CALIB_DURATION - (now - calib_start)

                if remaining <= 0:
                    if hands_angles:
                        calib_warnings = engine.calibrate(hands_angles)
                        if calib_warnings:
                            # g* 와 너무 다른 포즈 → 재시도
                            calib_start       = time.time()
                            calib_retry_until = now + 2.5
                            calib_reject_msg  = _calib_feedback(calib_warnings)
                            print(f"[WARN] 캘리브레이션 거부. 재시도...")
                        else:
                            # blend 모드: direct 엔진도 동일한 캘리브레이션 적용
                            if args.mapping == "blend":
                                _engine_direct.calibrate(hands_angles)
                                # 트리거 기준값 저장 (현재 손 DOF)
                                _trigger_ref = {
                                    side: dict(dofs)
                                    for side, dofs in hands_angles.items()
                                }
                                if _trigger_rules:
                                    print("[INFO] 트리거 기준값 저장:")
                                    for anim, rule in _trigger_rules.items():
                                        ref_val = _trigger_ref.get(
                                            rule["hand"], {}
                                        ).get(rule["dof"], 0.0)
                                        print(f"  {anim}: {rule['hand']}.{rule['dof']} "
                                              f"ref={ref_val:.1f}°  → trigger at "
                                              f"{ref_val + rule['delta']:.1f}°")
                            # 로코모션 캘리브레이션
                            if _loco is not None:
                                _loco.calibrate(hands_angles)

                            # 트리거 피크 포즈 계산 (animal joint 공간)
                            if args.mapping == "blend" and _trigger_rules:
                                _MIN_PEAK_DEG = 8.0
                                for _anim_t in _trigger_rules:
                                    _n_kf = engine.anim_frame_count(_anim_t)
                                    if _n_kf == 0:
                                        continue
                                    # 피크 프레임 탐색
                                    _best_fi, _best_mag = 0, 0.0
                                    for _fi in range(_n_kf):
                                        _p = engine.get_sequential_pose(_anim_t, float(_fi))
                                        _mag = sum(
                                            abs(v.get("x", 0)) + abs(v.get("y", 0)) + abs(v.get("z", 0))
                                            for v in _p.values()
                                        )
                                        if _mag > _best_mag:
                                            _best_mag, _best_fi = _mag, _fi
                                    _peak = engine.get_sequential_pose(_anim_t, float(_best_fi))
                                    # 직접 매핑 가능하고 충분히 움직이는 관절만 비교
                                    _cmp = [
                                        jid for jid, xyz in _peak.items()
                                        if max(abs(xyz.get("x", 0)), abs(xyz.get("y", 0)), abs(xyz.get("z", 0))) > _MIN_PEAK_DEG
                                    ]
                                    _trigger_peak_joints[_anim_t] = _peak
                                    _trigger_cmp_joints[_anim_t]  = _cmp
                                    print(f"[INFO] {_anim_t} 피크=frame{_best_fi}  "
                                          f"비교관절={len(_cmp)}개  "
                                          f"fraction={_TRIG_FRACTION*100:.0f}%")

                            _vel_ema      = 0.0
                            _prev_h_right = None
                            # 머리 방향: 캘리브 시점 얼굴 x를 기준으로 저장
                            if _face_detector is not None:
                                _gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                                _faces = _face_detector.detectMultiScale(
                                    _gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60)
                                )
                                if len(_faces) > 0:
                                    _fx, _fy, _fw, _fh = _faces[0]
                                    _head_ref_x = (_fx + _fw / 2.0) / frame.shape[1]
                                    print(f"[INFO] 머리 기준 x={_head_ref_x:.3f} 저장")
                                else:
                                    print("[WARN] 캘리브레이션 시 얼굴 미감지 — 기준 x=0.5 사용")
                            print("[INFO] 캘리브레이션 완료")
                            calib_done = True
                            # --no-window 모드: 캘리브 완료 후 창 닫기
                            if args.no_window:
                                cv2.destroyAllWindows()
                    else:
                        # 손 미감지 → 카운트다운 재시작
                        calib_start       = time.time()
                        calib_retry_until = now + 1.5
                        calib_reject_msg  = ""
                        print("[WARN] 손 미감지. 캘리브레이션 재시도...")

                # 캘리브레이션 중엔 --no-window여도 창 표시
                _draw_calib_overlay(
                    frame,
                    max(0.0, remaining),
                    bool(hands_angles),
                    retry=(now < calib_retry_until),
                    reject_msg=calib_reject_msg if now < calib_retry_until else "",
                )
                cv2.imshow("HandAvatar", frame)

                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                continue

            # ── 정상 동작 단계 ────────────────────────────────
            hand_detected = bool(hands_angles)
            if hand_detected:
                try:
                    if args.mapping == "blend":
                        # ── 손 벡터 ──────────────────────────────────
                        from mapping.keyframe_engine import _dof_dict_to_vec
                        h_left  = _dof_dict_to_vec(hands_angles.get("left",  {}))
                        h_right = _dof_dict_to_vec(hands_angles.get("right", {}))

                        # ── 속도(velocity) 계산 ───────────────────────
                        # deadzone: 노이즈(~10-15) 이하는 0으로 처리
                        _VEL_DEADZONE = 12.0
                        if _prev_h_right is not None:
                            _vel_raw = float(np.linalg.norm(h_right - _prev_h_right))
                            if _vel_raw < _VEL_DEADZONE:
                                # 실제 움직임 없음 → 즉시 감쇠
                                _vel_ema *= 0.2
                            else:
                                _vel = _vel_raw - _VEL_DEADZONE
                                # 빠른 반응: 새 값 70% 반영
                                _vel_ema = 0.7 * _vel + 0.3 * _vel_ema
                        _prev_h_right = h_right.copy()

                        # ── 쿨다운 감소 ──────────────────────────────
                        if _cooldown_frames > 0:
                            _cooldown_frames -= 1

                        # ── 트리거 진행 중: 키프레임 직접 재생 ───────
                        if _anim_state == "trigger":
                            joints = engine.get_sequential_pose(
                                _trigger_anim, _trigger_cursor
                            )
                            _trigger_cursor += 1.0
                            _trigger_frames -= 1
                            if _trigger_frames <= 0:
                                _anim_state      = "normal"
                                _trigger_anim    = None
                                _cooldown_frames = 30
                                print("[BLEND] → normal (trigger ended)")

                        else:
                            # ── Walk ROM direct mapping (트리거 비교 전에 먼저 계산) ──
                            _sk = _walk_skeleton if _walk_skeleton else _skeleton
                            joints_d = _engine_direct.transform_clamped(hands_angles, _sk)
                            joints_d = _float_joints_to_xyz(joints_d, _engine_direct.current_animal)
                            _DEAD = 3.0
                            for _jid in joints_d:
                                _v = joints_d[_jid]
                                joints_d[_jid] = {
                                    ax: (v if abs(v) >= _DEAD else 0.0)
                                    for ax, v in _v.items()
                                }
                            joints = joints_d

                            # ── 트리거 감지 (DOF delta 직접 비교 + hold) ──
                            # locomotion_config의 delta = 캘리브 기준 대비 필요 DOF 변화량
                            # _TRIG_FRACTION 비율만큼 변화하면 발동 (기본 65%)
                            if _cooldown_frames == 0 and _trigger_rules and _trigger_ref:
                                _fired_anim = None
                                for _aname, _rule in _trigger_rules.items():
                                    _dof          = _rule["dof"]
                                    _hand         = _rule["hand"]
                                    _delta_needed = _rule["delta"] * _TRIG_FRACTION
                                    _ref_v = _trigger_ref.get(_hand, {}).get(_dof, 0.0)
                                    _cur_v = hands_angles.get(_hand, {}).get(_dof, 0.0)
                                    _actual = _cur_v - _ref_v
                                    _ok = (_actual >= _delta_needed) if _delta_needed >= 0 \
                                          else (_actual <= _delta_needed)
                                    if _ok:
                                        _trigger_hold_cnt[_aname] = _trigger_hold_cnt.get(_aname, 0) + 1
                                        if _trigger_hold_cnt[_aname] >= _TRIGGER_HOLD:
                                            _fired_anim = _aname
                                            _trigger_hold_cnt[_aname] = 0
                                            break
                                    else:
                                        _trigger_hold_cnt[_aname] = 0
                                if _fired_anim is not None:
                                    _n_kf = engine.anim_frame_count(_fired_anim)
                                    _anim_state     = "trigger"
                                    _trigger_anim   = _fired_anim
                                    _trigger_frames = _n_kf - 1
                                    _trigger_cursor = 1.0
                                    print(f"[BLEND] → trigger: {_fired_anim}  frames={_n_kf}")

                            # ── 트리거 진행도 디버그 로그 ────────────
                            if args.dist_log > 0 and frame_count % args.dist_log == 0 and _trigger_rules and _trigger_ref:
                                _prog_strs = []
                                for _aname, _rule in _trigger_rules.items():
                                    _dof  = _rule["dof"]
                                    _hand = _rule["hand"]
                                    _dn   = _rule["delta"] * _TRIG_FRACTION
                                    _ref_v = _trigger_ref.get(_hand, {}).get(_dof, 0.0)
                                    _cur_v = hands_angles.get(_hand, {}).get(_dof, 0.0)
                                    _actual = _cur_v - _ref_v
                                    _pct = (_actual / _dn * 100) if _dn != 0 else 0.0
                                    _hold = _trigger_hold_cnt.get(_aname, 0)
                                    _prog_strs.append(
                                        f"{_aname}({_hand}.{_dof})={_actual:+.1f}°/{_dn:.1f}°"
                                        + (f"[{_hold}f]" if _hold > 0 else "")
                                    )
                                print(f"[TRIG] " + "  ".join(_prog_strs))
                    else:
                        joints = engine.transform_clamped(hands_angles, _skeleton)
                        # direct 모드: float → {x,y,z} 변환 (애니메이션 데이터에서 축 자동 계산)
                        if joints and not isinstance(next(iter(joints.values())), dict):
                            joints = _float_joints_to_xyz(joints, engine.current_animal)

                    # body joints를 항상 0으로 명시 전송 (Unity가 이전 값을 유지하는 것 방지)
                    if _body_mapping:
                        for _bj in _body_mapping:
                            if _bj not in joints:
                                joints[_bj] = {"x": 0.0, "y": 0.0, "z": 0.0}
                except Exception as e:
                    print(f"[WARN] 변환 오류: {e}")
                    joints = {}

                # 디버그: 50프레임마다 출력
                if frame_count % 50 == 0:
                    detected_sides = list(hands_angles.keys())
                    print(f"\n[DEBUG] 감지된 손: {detected_sides}")
                    if "right" in hands_angles:
                        r = hands_angles["right"]
                        print(f"  right: index_mcp={r.get('index_mcp',0):.1f}  "
                              f"middle_mcp={r.get('middle_mcp',0):.1f}  "
                              f"wrist_rot={r.get('wrist_rot',0):.1f}  "
                              f"wrist_flex={r.get('wrist_flex',0):.1f}")
                    if "left" in hands_angles:
                        l = hands_angles["left"]
                        print(f"  left:  index_mcp={l.get('index_mcp',0):.1f}  "
                              f"middle_mcp={l.get('middle_mcp',0):.1f}  "
                              f"wrist_rot={l.get('wrist_rot',0):.1f}  "
                              f"wrist_flex={l.get('wrist_flex',0):.1f}")
                    def _jv(jid, ax):
                        v = joints.get(jid, {})
                        return v.get(ax, 0.0) if isinstance(v, dict) else float(v)
                    def _jxyz(jid):
                        v = joints.get(jid, {})
                        if isinstance(v, dict):
                            return f"X{v.get('x',0):.1f}/Y{v.get('y',0):.1f}/Z{v.get('z',0):.1f}"
                        return f"{float(v):.1f}"
                    print(f"  → r_leg={_jxyz('r_leg')}  "
                          f"l_leg={_jxyz('l_leg')}  "
                          f"r_bone_006={_jxyz('r_bone_006')}")
            else:
                joints = {}
                # 손 미감지 시 velocity EMA 감쇠 (안 하면 이전 값이 유지되어 계속 이동)
                if args.mapping == "blend":
                    _vel_ema *= 0.7

            # ── 머리 방향 감지 ────────────────────────────────
            if _face_detector is not None:
                _face_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                _faces = _face_detector.detectMultiScale(
                    _face_gray, scaleFactor=1.05, minNeighbors=2, minSize=(50, 50)
                )
                if len(_faces) > 0:
                    _head_miss_cnt = 0
                    _fx, _fy, _fw, _fh = _faces[0]
                    _face_cx = (_fx + _fw / 2.0) / frame.shape[1]
                    _offset  = _face_cx - _head_ref_x
                    if abs(_offset) < _HEAD_DEADZONE:
                        _target_yaw = 0.0
                    else:
                        _sign = 1.0 if _offset > 0 else -1.0
                        _target_yaw = (abs(_offset) - _HEAD_DEADZONE) * _HEAD_SCALE * _sign * -1.0
                    # 강한 EMA — jitter 억제
                    _head_yaw_delta = 0.3 * _target_yaw + 0.7 * _head_yaw_delta
                    if frame_count % 30 == 0:
                        print(f"[HEAD] cx={_face_cx:.3f}  offset={_offset:+.3f}  yaw={_head_yaw_delta:+.2f}")
                else:
                    _head_miss_cnt += 1
                    if _head_miss_cnt >= _HEAD_MISS_RESET:
                        _head_yaw_delta = 0.0   # 일정 프레임 이상 미감지 → 리셋

            # ── 로코모션 계산 (관절 매핑과 독립) ─────────────
            _loco_result: dict | None = None
            if _loco is not None:
                _loco_result = _loco.update(hands_angles, hand_detected, engine)
                # blend 모드: cursor 기반 speed → velocity 기반 speed로 교체
                if args.mapping == "blend" and _loco_result is not None:
                    _loco_result["speed"] = round(
                        min(_vel_ema * _VEL_SCALE, 2.0), 4
                    )
                    # 머리 방향 제어 활성 시 yaw_delta 덮어쓰기
                    if _face_detector is not None:
                        _loco_result["yaw_delta"] = round(_head_yaw_delta, 3)

                    if frame_count % 30 == 0:
                        print(f"[LOCO] vel_ema={_vel_ema:.2f}  speed={_loco_result['speed']:.4f}  "
                              f"yaw={_loco_result.get('yaw_delta', 0):+.2f}  valid={_loco_result['valid']}")

            server.send_frame(
                joints        = joints,
                animal        = engine.current_animal,
                hand_detected = hand_detected,
                locomotion    = _loco_result,
            )

            frame_count += 1
            elapsed = time.time() - t_start
            fps = frame_count / elapsed if elapsed > 0 else 0

            if not args.no_window:
                cv2.putText(frame, f"FPS: {fps:.1f}", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
                cv2.putText(frame, f"Animal: {engine.current_animal}", (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
                cv2.putText(frame,
                            f"Unity clients: {server.client_count}", (10, 90),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
                # 로코모션 HUD
                if _loco_result is not None:
                    _spd = _loco_result.get("speed", 0.0)
                    _yaw = _loco_result.get("yaw_delta", 0.0)
                    _cur = _loco_result.get("cursor", 0.0)
                    _arrow = ("→" if _yaw > 1 else "←" if _yaw < -1 else "↑")
                    cv2.putText(frame,
                                f"Loco: spd={_spd:.3f}  yaw={_yaw:+.1f}  cur={_cur:.1f} {_arrow}",
                                (10, 115),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 255, 100), 1)

                # blend 모드 상태 표시 (locomotion HUD 아래로 내림)
                _hud_y = 145 if _loco_result is not None else 125
                if args.mapping == "blend":
                    if _anim_state == "trigger":
                        state_txt   = f"TRIGGER: {_trigger_anim}  [{_trigger_frames}f]"
                        state_color = (0, 100, 255)
                    else:
                        state_txt   = f"normal  vel={_vel_ema:.2f}"
                        state_color = (0, 220, 100)
                    cv2.putText(frame, state_txt, (10, _hud_y),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.65, state_color, 2)

                # 키프레임 블렌드 정보
                blend_info = getattr(engine, "_last_blend_info", [])
                if blend_info and args.mapping != "blend":
                    top = blend_info[0]
                    cv2.putText(frame,
                                f"[{top[1]} f{top[2]}] {top[0]*100:.0f}%",
                                (10, _hud_y),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 180), 2)
                    for idx, (w, anim, frm) in enumerate(blend_info[1:4]):
                        if w < 0.03:
                            break
                        cv2.putText(frame,
                                    f"  {anim} f{frm}  {w*100:.0f}%",
                                    (10, 152 + idx * 24),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (100, 200, 150), 1)

                cv2.imshow("HandAvatar", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                print("[INFO] 종료합니다.")
                break

    cap.release()
    if not args.no_window:
        cv2.destroyAllWindows()
    server.stop()


if __name__ == "__main__":
    main()
