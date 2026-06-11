"""
clap_test.py
박수 감지만 테스트. 파이프라인 없이 웹캠만 사용.
실행: python scripts/clap_test.py
종료: q
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cv2
import numpy as np
import mediapipe as mp

BaseOptions           = mp.tasks.BaseOptions
HandLandmarker        = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
VisionRunningMode     = mp.tasks.vision.RunningMode

from tracking.hand_tracker import download_model, MODEL_PATH

# ── 파라미터 (main.py와 동일) ──────────────────────────────────
CLAP_NEAR_THR = 0.20
CLAP_FAR_THR  = 0.38
CLAP_WINDOW   = 25
CLAP_COOLDOWN = 90

# ──────────────────────────────────────────────────────────────

def main():
    download_model()

    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=VisionRunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    cap = cv2.VideoCapture(0)

    clap_dist_hist: list[float] = []
    clap_cooldown  = 0
    clap_flash_cnt = 0
    clap_count     = 0

    import time
    with HandLandmarker.create_from_options(options) as landmarker:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            h, w = frame.shape[:2]
            ts_ms = int(time.time() * 1000)
            mp_img = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
            )
            result = landmarker.detect_for_video(mp_img, ts_ms)

            palm_pos: dict[str, np.ndarray] = {}
            if result.hand_landmarks:
                for lms, handedness_list in zip(result.hand_landmarks, result.handedness):
                    side = handedness_list[0].category_name.lower()
                    palm_pos[side] = np.array([lms[9].x, lms[9].y])
                    # 손바닥 중심 점 표시
                    cx, cy = int(lms[9].x * w), int(lms[9].y * h)
                    cv2.circle(frame, (cx, cy), 10, (0, 255, 0), -1)

            # ── 감지 로직 ──────────────────────────────────────
            fired = False
            if clap_cooldown > 0:
                clap_cooldown -= 1
            elif "left" in palm_pos and "right" in palm_pos:
                dist = float(np.linalg.norm(palm_pos["left"] - palm_pos["right"]))
                clap_dist_hist.append(dist)
                if len(clap_dist_hist) > CLAP_WINDOW:
                    clap_dist_hist.pop(0)

                was_far   = len(clap_dist_hist) == CLAP_WINDOW and max(clap_dist_hist) >= CLAP_FAR_THR
                now_close = dist < CLAP_NEAR_THR

                if was_far and now_close:
                    fired = True
                    clap_count += 1
                    clap_flash_cnt = 45
                    clap_cooldown  = CLAP_COOLDOWN
                    clap_dist_hist.clear()
                    print(f"[CLAP] 감지! (누적: {clap_count}회)")

                # 손 사이 선 + 거리 표시
                if "left" in palm_pos and "right" in palm_pos:
                    lx, ly = int(palm_pos["left"][0]  * w), int(palm_pos["left"][1]  * h)
                    rx, ry = int(palm_pos["right"][0] * w), int(palm_pos["right"][1] * h)
                    line_color = (0, 80, 255) if now_close else (200, 200, 200)
                    cv2.line(frame, (lx, ly), (rx, ry), line_color, 2)
                    mid_x, mid_y = (lx + rx) // 2, (ly + ry) // 2
                    cv2.putText(frame, f"{dist:.2f}", (mid_x - 20, mid_y - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, line_color, 2)

                # 이력 바 (화면 하단)
                bar_w = int((len(clap_dist_hist) / CLAP_WINDOW) * 200)
                cv2.rectangle(frame, (10, h - 20), (10 + bar_w, h - 8), (100, 200, 100), -1)
                if was_far:
                    cv2.putText(frame, "WAS FAR: YES", (220, h - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 120), 1)
                else:
                    cv2.putText(frame, "WAS FAR: NO", (220, h - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 100, 100), 1)
            else:
                clap_dist_hist.clear()

            # ── HUD ────────────────────────────────────────────
            # 상태
            if clap_cooldown > 0:
                status, sc = f"COOLDOWN  {clap_cooldown}f", (0, 140, 255)
            elif "left" in palm_pos and "right" in palm_pos:
                status, sc = "READY", (0, 220, 100)
            else:
                status, sc = "NO HANDS", (100, 100, 100)

            cv2.putText(frame, f"STATUS: {status}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, sc, 2)
            cv2.putText(frame, f"CLAP COUNT: {clap_count}", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 100), 2)
            cv2.putText(frame,
                        f"NEAR<{CLAP_NEAR_THR}  FAR>{CLAP_FAR_THR}  WIN={CLAP_WINDOW}f",
                        (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)

            # 박수 플래시
            if clap_flash_cnt > 0:
                clap_flash_cnt -= 1
                alpha = clap_flash_cnt / 45.0
                cv2.putText(frame, f"CLAP! #{clap_count}",
                            (w // 2 - 100, h // 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 2.0,
                            (int(255 * alpha), int(255 * alpha), 50), 4)

            cv2.imshow("Clap Test", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    cap.release()
    cv2.destroyAllWindows()
    print(f"\n총 박수 횟수: {clap_count}회")


if __name__ == "__main__":
    main()
