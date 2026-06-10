"""
gaze_sender.py (Unity 캘리브레이션 버전)
-----------------------------------------
Unity 씬 안에서 9포인트 캘리브레이션 후 실시간 gaze UV 전송.

흐름:
    1. Python 서버 시작 → 카메라 초기화
    2. Unity 클라이언트 연결 시 → {"type": "ready"} 전송
    3. Unity가 점을 표시하며 {"cmd": "collect", "step": N, "uv_x": X, "uv_y": Y} 전송
    4. Python이 COLLECT_FRAMES 프레임 수집 후 {"type": "point_done", "step": N} 응답
    5. Unity가 9포인트 완료 후 {"cmd": "train"} 전송
    6. Python 모델 학습 → {"type": "calib_complete"} 전송
    7. 이후 실시간 {"gaze_x": X, "gaze_y": Y, "valid": B} 전송

종료: 'q' 키 또는 Ctrl+C
"""

from __future__ import annotations
import json
import os
import sys
import time
from queue import Queue

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_DIR = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PYTHON_DIR)

import numpy as np
from eyetrax import GazeEstimator
import screeninfo
import cv2

from communication.websocket_server import WebSocketServer

PORT           = 8766
COLLECT_FRAMES = 60   # 포인트당 수집 프레임 수 (~2초 @ 30fps)


def get_screen_size() -> tuple[int, int]:
    try:
        m = screeninfo.get_monitors()[0]
        return m.width, m.height
    except Exception:
        return 1920, 1080


def main():
    sw, sh = get_screen_size()
    print(f"[INFO] 화면 해상도: {sw}x{sh}")

    # ── WebSocket 서버 ────────────────────────────────────────────────────────
    cmd_queue: Queue[str] = Queue()
    server = WebSocketServer(port=PORT)
    server.on_message = cmd_queue.put

    # Unity 클라이언트 연결 시 "ready" 전송 (타이밍 문제 방지)
    def on_connect():
        server._broadcast(json.dumps({"type": "ready"}))
        print("[INFO] Unity 연결됨 → ready 전송")

    server.on_connect = on_connect
    server.start()
    print(f"[INFO] WebSocket 서버: ws://localhost:{PORT}")
    print("[INFO] Unity Play 버튼을 눌러 캘리브레이션을 시작하세요.")

    # ── 카메라 + 모델 초기화 ──────────────────────────────────────────────────
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] 카메라를 열 수 없습니다.")
        server.stop()
        sys.exit(1)

    estimator = GazeEstimator()

    # ── 상태 변수 ─────────────────────────────────────────────────────────────
    state          = "waiting"    # "waiting" | "collecting" | "ready"
    collect_target = (sw / 2, sh / 2)
    collect_count  = 0
    collect_step   = 0
    calib_features: list = []
    calib_targets:  list = []

    gaze_x, gaze_y = 0.5, 0.5
    frame_count    = 0
    t_start        = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # ── Unity 커맨드 처리 ─────────────────────────────────────────────────
        while not cmd_queue.empty():
            msg = cmd_queue.get_nowait()
            try:
                data = json.loads(msg)
                cmd  = data.get("cmd")

                if cmd == "collect":
                    uv_x = float(data["uv_x"])
                    uv_y = float(data["uv_y"])
                    collect_target = (uv_x * sw, uv_y * sh)
                    collect_count  = 0
                    collect_step   = int(data.get("step", 1))
                    state          = "collecting"
                    print(f"[CALIB] Step {collect_step}: "
                          f"화면 ({collect_target[0]:.0f}, {collect_target[1]:.0f}) 수집 중...")

                elif cmd == "train":
                    if len(calib_features) >= 9:
                        estimator.train(
                            np.array(calib_features),
                            np.array(calib_targets),
                        )
                        state = "ready"
                        server._broadcast(json.dumps({"type": "calib_complete"}))
                        print(f"[INFO] 모델 학습 완료 ({len(calib_features)}샘플) → gaze 전송 시작")
                    else:
                        print(f"[WARN] 샘플 부족 ({len(calib_features)}개). 캘리브레이션을 다시 하세요.")

            except Exception as e:
                print(f"[WARN] 커맨드 파싱 오류: {e}")

        # ── 특징 추출 ─────────────────────────────────────────────────────────
        features, is_blink = None, False
        try:
            features, is_blink = estimator.extract_features(frame)
        except Exception as e:
            print(f"[WARN] extract_features 오류: {e}")

        # ── 상태별 처리 ───────────────────────────────────────────────────────
        if state == "collecting":
            if features is not None and not is_blink:
                calib_features.append(features)
                calib_targets.append(list(collect_target))
                collect_count += 1

                if collect_count >= COLLECT_FRAMES:
                    state = "waiting"
                    server._broadcast(json.dumps({
                        "type":  "point_done",
                        "step":  collect_step,
                        "total": len(calib_features),
                    }))
                    print(f"[CALIB] Step {collect_step} 완료 (누적 {len(calib_features)}샘플)")

        elif state == "ready":
            valid = False
            try:
                if features is not None and not is_blink:
                    px, py = estimator.predict(features.reshape(1, -1))[0]
                    gaze_x = max(0.0, min(1.0, px / sw))
                    gaze_y = max(0.0, min(1.0, py / sh))
                    valid  = True
            except Exception as e:
                print(f"[WARN] predict 오류: {e}")

            server._broadcast(json.dumps({
                "gaze_x": round(gaze_x, 4),
                "gaze_y": round(gaze_y, 4),
                "valid":  valid,
            }))

            if frame_count % 30 == 0:
                fps    = frame_count / (time.time() - t_start + 1e-6)
                status = f"({gaze_x:.3f},{gaze_y:.3f})" if valid else "MISS"
                print(f"[GAZE] {status}  clients={server.client_count}  fps={fps:.1f}")

        # ── 카메라 미리보기 ───────────────────────────────────────────────────
        display = frame.copy()
        h, w = display.shape[:2]

        if state == "collecting":
            prog   = collect_count / COLLECT_FRAMES
            bar_w  = int(w * prog)
            cv2.rectangle(display, (0, h - 12), (bar_w, h), (0, 200, 100), -1)
            cv2.putText(display,
                        f"COLLECT Step {collect_step}: {collect_count}/{COLLECT_FRAMES}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 220, 100), 2)

        elif state == "ready":
            if valid:
                cx, cy = int(gaze_x * w), int(gaze_y * h)
                cv2.drawMarker(display, (cx, cy), (0, 255, 0), cv2.MARKER_CROSS, 30, 2)
            label = f"({gaze_x:.2f},{gaze_y:.2f})" if valid else "MISS"
            cv2.putText(display, f"GAZE: {label}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                        (0, 255, 100) if valid else (0, 100, 255), 2)

        else:  # waiting
            cv2.putText(display, "WAITING FOR UNITY CALIBRATION...",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (100, 140, 255), 2)

        cv2.imshow("Gaze Sender (카메라 확인용)", display)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

        frame_count += 1

    cap.release()
    cv2.destroyAllWindows()
    server.stop()


if __name__ == "__main__":
    main()
