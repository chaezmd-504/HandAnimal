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
import os
import sys
import time

import cv2

_PYTHON_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PYTHON_DIR)

import mediapipe as mp

from tracking.hand_tracker import (
    download_model,
    MODEL_PATH,
    compute_finger_angles,
    draw_landmarks,
)
from tracking.occlusion_handler import OcclusionHandler
from mapping.mapping_engine import MappingEngine
from mapping.gesture_detector import GestureDetector
from communication.websocket_server import WebSocketServer

BaseOptions           = mp.tasks.BaseOptions
HandLandmarker        = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
VisionRunningMode     = mp.tasks.vision.RunningMode

DATA_DIR     = os.path.join(_PYTHON_DIR, "data")
MAPPINGS_DIR = os.path.join(DATA_DIR, "mappings")

HAND_COLOR = {"left": (0, 255, 0), "right": (255, 100, 0)}


def parse_args():
    p = argparse.ArgumentParser(description="HandAvatar 파이프라인")
    p.add_argument("--animal", default="spider",
                   choices=["spider", "butterfly", "fish", "octopus", "snake"],
                   help="시작 동물 (기본값: spider)")
    p.add_argument("--port", type=int, default=8765,
                   help="WebSocket 포트 (기본값: 8765)")
    p.add_argument("--no-window", action="store_true",
                   help="OpenCV 미리보기 창 비활성화")
    p.add_argument("--no-gesture", action="store_true",
                   help="제스처 감지 비활성화 (동물 전환 없음)")
    return p.parse_args()


def main():
    args = parse_args()

    download_model()

    occlusion = {"left": OcclusionHandler(), "right": OcclusionHandler()}
    engine    = MappingEngine(MAPPINGS_DIR)
    gesture   = GestureDetector(cooldown=1.5)
    server    = WebSocketServer(port=args.port)

    try:
        engine.set_animal(args.animal)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        print("[HINT] python scripts/generate_mappings.py 를 먼저 실행하세요.")
        sys.exit(1)

    server.start()

    if args.no_gesture:
        print("[INFO] 제스처 감지 비활성화됨 (--no-gesture)")

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

    print(f"[INFO] 파이프라인 시작 — 동물: {engine.current_animal}, 종료: q")

    frame_count = 0
    t_start     = time.time()

    with HandLandmarker.create_from_options(options) as landmarker:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("[ERROR] 프레임 읽기 실패.")
                break

            h, w   = frame.shape[:2]
            ts_ms  = int(time.time() * 1000)
            mp_image = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
            )
            result = landmarker.detect_for_video(mp_image, ts_ms)

            hands_angles: dict[str, dict[str, float]] = {}
            detected_gesture = None

            if result.hand_landmarks:
                for landmarks, handedness_list in zip(
                    result.hand_landmarks, result.handedness
                ):
                    side  = handedness_list[0].category_name.lower()
                    color = HAND_COLOR.get(side, (0, 255, 0))

                    if not args.no_window:
                        draw_landmarks(frame, landmarks, h, w, color=color)

                    filtered = occlusion[side].process(landmarks)
                    hands_angles[side] = compute_finger_angles(filtered)

                    # 제스처 감지 (--no-gesture 시 스킵)
                    if not args.no_gesture:
                        if side == "right" or detected_gesture is None:
                            g = gesture.detect(landmarks)
                            if g is not None:
                                detected_gesture = g

            # 제스처 → 동물 전환
            if detected_gesture == "thumbs_up":
                new_animal = engine.next_animal()
                server.send_switch(new_animal)
                print(f"[GESTURE] 👍 다음 동물: {new_animal}")
            elif detected_gesture == "fist":
                new_animal = engine.prev_animal()
                server.send_switch(new_animal)
                print(f"[GESTURE] ✊ 이전 동물: {new_animal}")

            # 관절 변환
            hand_detected = bool(hands_angles)
            if hand_detected:
                try:
                    joints = engine.transform_bilateral({
                        "left":  hands_angles.get("left"),
                        "right": hands_angles.get("right"),
                    })
                except Exception as e:
                    print(f"[WARN] 변환 오류: {e}")
                    joints = {}
            else:
                joints = {}

            server.send_frame(
                joints        = joints,
                animal        = engine.current_animal,
                hand_detected = hand_detected,
                gesture       = detected_gesture,
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
                cv2.imshow("HandAvatar", frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("[INFO] 종료합니다.")
                break

    cap.release()
    if not args.no_window:
        cv2.destroyAllWindows()
    server.stop()


if __name__ == "__main__":
    main()
