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

    joints: set[str] = set(k for p in poses for k in p if not k.startswith("_"))
    result: dict[str, tuple[str, int]] = {}
    for jid in joints:
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


def _draw_calib_overlay(frame, remaining: float, hands_detected: bool, retry: bool = False):
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
    cv2.putText(frame, "Reference pose", (guide_cx - 80, guide_cy + int(90 * 1.2)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 255, 180), 1)

    # 왼쪽: 안내 텍스트
    cv2.putText(frame, "Calibration", (24, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 2)
    for i, line in enumerate([
        "오른쪽처럼 양손 손가락을",
        "살짝 구부려 주세요.",
        "",
        "엄지: 약 20도",
        "검지~약지: 약 15~20도",
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
    if retry:
        cv2.putText(frame, "손이 감지되지 않았습니다. 다시 시도...", (24, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 80, 255), 2)
    elif hands_detected:
        cv2.putText(frame, "손 감지됨  ✓  자세를 유지하세요", (24, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 220, 100), 2)
    else:
        cv2.putText(frame, "손을 카메라에 보여주세요", (24, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 120, 255), 2)


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="HandAvatar 파이프라인")
    p.add_argument("--animal", default="spider",
                   choices=["spider", "butterfly", "fish"],
                   help="동물 선택 (기본값: spider)")
    p.add_argument("--port", type=int, default=8765,
                   help="WebSocket 포트 (기본값: 8765)")
    p.add_argument("--no-window", action="store_true",
                   help="OpenCV 미리보기 창 비활성화")
    p.add_argument("--mapping", choices=["keyframe", "direct"], default="keyframe",
                   help="매핑 방식: keyframe=키프레임 블렌딩(기본), direct=직접 각도 매핑(wobbly)")
    p.add_argument("--temperature", type=float, default=8.0,
                   help="[keyframe] 소프트맥스 온도. 높을수록 스냅, 낮을수록 부드럽게 (기본 8.0)")
    return p.parse_args()


# ──────────────────────────────────────────────────────────────
# 메인 루프
# ──────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    download_model()

    occlusion = {"left": OcclusionHandler(), "right": OcclusionHandler()}
    server    = WebSocketServer(port=args.port)

    if args.mapping == "keyframe":
        engine = KeyframeMappingEngine(
            MAPPINGS_DIR, POSES_DIR, temperature=args.temperature
        )
        print(f"[INFO] 매핑 모드: keyframe (temperature={args.temperature})")
    else:
        engine = MappingEngine(MAPPINGS_DIR)
        print("[INFO] 매핑 모드: direct (연속 각도 매핑)")

    try:
        engine.set_animal(args.animal)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        print("[HINT] python scripts/generate_mappings.py 와 "
              "extract_avatar_poses.py 를 먼저 실행하세요.")
        sys.exit(1)

    server.start()

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
    calib_done            = args.no_window   # 창 없으면 캘리브 스킵
    calib_start           = time.time()
    calib_retry_until     = 0.0             # 재시도 메시지 표시 종료 시각

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
            if result.hand_landmarks:
                for landmarks, handedness_list in zip(
                    result.hand_landmarks, result.handedness
                ):
                    side  = handedness_list[0].category_name.lower()
                    color = HAND_COLOR.get(side, (0, 255, 0))
                    if not args.no_window:
                        draw_landmarks(frame, landmarks, h, w, color=color)
                    filtered = occlusion[side].process(landmarks)
                    hands_angles[side] = compute_dof_angles(filtered)

            # ── 캘리브레이션 단계 ──────────────────────────────
            if not calib_done:
                now       = time.time()
                remaining = _CALIB_DURATION - (now - calib_start)

                if remaining <= 0:
                    if hands_angles:
                        engine.calibrate(hands_angles)
                        print("[INFO] 캘리브레이션 완료")
                        calib_done = True
                    else:
                        # 손 미감지 → 카운트다운 재시작
                        calib_start       = time.time()
                        calib_retry_until = now + 1.5
                        print("[WARN] 손 미감지. 캘리브레이션 재시도...")

                if not args.no_window:
                    _draw_calib_overlay(
                        frame,
                        max(0.0, remaining),
                        bool(hands_angles),
                        retry=(now < calib_retry_until),
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
                    joints = engine.transform_bilateral(hands_angles)
                    # direct 모드: float → {x,y,z} 변환 (애니메이션 데이터에서 축 자동 계산)
                    if joints and not isinstance(next(iter(joints.values())), dict):
                        joints = _float_joints_to_xyz(joints, engine.current_animal)
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
                              f"thumb_ip={r.get('thumb_ip',0):.1f}  "
                              f"pinky_pip={r.get('pinky_pip',0):.1f}")
                    if "left" in hands_angles:
                        l = hands_angles["left"]
                        print(f"  left:  index_mcp={l.get('index_mcp',0):.1f}  "
                              f"middle_mcp={l.get('middle_mcp',0):.1f}  "
                              f"thumb_ip={l.get('thumb_ip',0):.1f}  "
                              f"pinky_pip={l.get('pinky_pip',0):.1f}")
                    def _jv(jid, ax):
                        v = joints.get(jid, {})
                        return v.get(ax, 0.0) if isinstance(v, dict) else float(v)
                    print(f"  → r_leg=Y{_jv('r_leg','y'):.1f}  "
                          f"l_leg=Y{_jv('l_leg','y'):.1f}  "
                          f"r_bone_006=Y{_jv('r_bone_006','y'):.1f}")
            else:
                joints = {}

            server.send_frame(
                joints        = joints,
                animal        = engine.current_animal,
                hand_detected = hand_detected,
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

                # 키프레임 블렌드 정보
                blend_info = getattr(engine, "_last_blend_info", [])
                if blend_info:
                    top = blend_info[0]
                    cv2.putText(frame,
                                f"[{top[1]} f{top[2]}] {top[0]*100:.0f}%",
                                (10, 125),
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
