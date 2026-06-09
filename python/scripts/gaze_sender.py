"""
gaze_sender.py
--------------
홍채(iris) 기반 시선 추적 → WebSocket 전송 독립 스크립트.
Hand tracking / 동물 파이프라인과 완전히 분리.

전송 JSON 포맷:
    {"gaze_x": 0.47, "gaze_y": 0.51, "valid": true}

실행:
    conda activate capstone_env
    python scripts/gaze_sender.py [--port 8766] [--no-window]

Unity 씬에 GazeReceiver 컴포넌트를 ws://localhost:8766 으로 연결.

종료: 'q' 키 또는 Ctrl+C
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import time

import cv2
import mediapipe as mp

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_DIR = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PYTHON_DIR)

from tracking.face_tracker import (
    download_face_landmark_model,
    FACE_LANDMARK_MODEL_PATH,
    compute_gaze_uv,
)
from communication.websocket_server import WebSocketServer

BaseOptions           = mp.tasks.BaseOptions
FaceLandmarker        = mp.tasks.vision.FaceLandmarker
FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
VisionRunningMode     = mp.tasks.vision.RunningMode

# EMA 계수 — 낮을수록 부드러움 (초기값)
_GAZE_EMA   = 0.15
_MISS_RESET = 10


def parse_args():
    p = argparse.ArgumentParser(description="Gaze sender — iris UV → WebSocket")
    p.add_argument("--port",      type=int,   default=8766,
                   help="WebSocket 포트 (기본 8766, main.py와 충돌 방지)")
    p.add_argument("--ema",       type=float, default=_GAZE_EMA,
                   help="EMA 스무딩 계수 [0~1] (기본 0.15, 낮을수록 부드러움)")
    p.add_argument("--no-window", action="store_true",
                   help="카메라 미리보기 창 숨김")
    p.add_argument("--video",     type=str,   default=None,
                   help="웹캠 대신 사용할 영상 파일 경로")
    return p.parse_args()


def main():
    args = parse_args()

    download_face_landmark_model()

    server = WebSocketServer(port=args.port)
    server.start()
    print(f"[INFO] WebSocket 서버 시작: ws://localhost:{args.port}")
    print("[INFO] Unity GazeReceiver를 같은 포트로 연결하세요.")

    opts = FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=FACE_LANDMARK_MODEL_PATH),
        running_mode=VisionRunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    cap_src = args.video if args.video else 0
    cap = cv2.VideoCapture(cap_src)
    if not cap.isOpened():
        print(f"[ERROR] 영상 소스를 열 수 없습니다: {cap_src}")
        server.stop()
        sys.exit(1)

    gaze_x:   float = 0.5
    gaze_y:   float = 0.5
    gaze_valid: bool = False
    miss_cnt: int   = 0
    ema = args.ema

    frame_count = 0
    t_start     = time.time()

    with FaceLandmarker.create_from_options(opts) as landmarker:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("[ERROR] 프레임 읽기 실패.")
                break

            h, w  = frame.shape[:2]
            ts_ms = int(time.time() * 1000)
            mp_img = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
            )

            result = landmarker.detect_for_video(mp_img, ts_ms)

            if result.face_landmarks:
                gx, gy, gv = compute_gaze_uv(result.face_landmarks[0])
                if gv:
                    miss_cnt  = 0
                    gaze_x    = ema * gx + (1.0 - ema) * gaze_x
                    gaze_y    = ema * gy + (1.0 - ema) * gaze_y
                    gaze_valid = True
                else:
                    miss_cnt += 1
                    if miss_cnt >= _MISS_RESET:
                        gaze_valid = False
            else:
                miss_cnt += 1
                if miss_cnt >= _MISS_RESET:
                    gaze_valid = False

            # WebSocket 전송
            payload = json.dumps({
                "gaze_x": round(gaze_x, 4),
                "gaze_y": round(gaze_y, 4),
                "valid":  gaze_valid,
            })
            server._broadcast(payload)

            # 콘솔 로그
            if frame_count % 30 == 0:
                fps = frame_count / (time.time() - t_start + 1e-6)
                status = f"({gaze_x:.3f}, {gaze_y:.3f})" if gaze_valid else "MISS"
                print(f"[GAZE] {status}  clients={server.client_count}  fps={fps:.1f}")

            # 미리보기 창
            if not args.no_window:
                cv2.putText(frame, f"FPS: {(frame_count/(time.time()-t_start+1e-6)):.1f}",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
                status_txt = f"Gaze: ({gaze_x:.2f}, {gaze_y:.2f})" if gaze_valid else "Gaze: MISS"
                color = (0, 255, 100) if gaze_valid else (0, 100, 255)
                cv2.putText(frame, status_txt, (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

                # 화면 위에 시선 위치 표시 (십자선)
                if gaze_valid:
                    cx = int(gaze_x * w)
                    cy = int(gaze_y * h)
                    cv2.drawMarker(frame, (cx, cy), (0, 255, 0),
                                   cv2.MARKER_CROSS, 30, 2)

                cv2.imshow("Gaze Sender", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                print("[INFO] 종료합니다.")
                break

            frame_count += 1

    cap.release()
    if not args.no_window:
        cv2.destroyAllWindows()
    server.stop()


if __name__ == "__main__":
    main()
