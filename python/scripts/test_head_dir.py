"""
test_head_dir.py — 머리 회전(yaw) 인식 단독 테스트
MediaPipe FaceDetector: 코/눈 키포인트 비율 → 실제 yaw 각도

실행: python python/scripts/test_head_dir.py
종료: q 키  |  스페이스바: 기준 yaw 재설정
"""
import sys
import os
import time
import cv2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mediapipe as mp
from tracking.face_tracker import download_face_model, FACE_MODEL_PATH, compute_face_yaw

HEAD_DEADZONE = 0.05   # yaw_ratio deadzone (눈 간격 대비 %)
HEAD_SCALE    = 8.0    # yaw_ratio → °/frame

download_face_model()

FaceDetector        = mp.tasks.vision.FaceDetector
FaceDetectorOptions = mp.tasks.vision.FaceDetectorOptions
VisionRunningMode   = mp.tasks.vision.RunningMode

options = FaceDetectorOptions(
    base_options=mp.tasks.BaseOptions(model_asset_path=FACE_MODEL_PATH),
    running_mode=VisionRunningMode.VIDEO,
    min_detection_confidence=0.5,
)

cap     = cv2.VideoCapture(0)
ref_yaw = 0.0   # 스페이스바로 재설정
yaw_ema = 0.0

print("실행 중... 스페이스바=기준 재설정, q=종료")

with FaceDetector.create_from_options(options) as detector:
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        h, w   = frame.shape[:2]
        ts_ms  = int(time.time() * 1000)
        mp_img = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
        )
        result = detector.detect_for_video(mp_img, ts_ms)

        direction = "NO FACE"
        color     = (80, 80, 80)

        if result.detections:
            det        = result.detections[0]
            yaw_r, ed  = compute_face_yaw(det)

            if ed > 1e-4:
                # 얼굴 bounding box
                bb  = det.bounding_box
                x1  = int(bb.origin_x)
                y1  = int(bb.origin_y)
                x2  = x1 + int(bb.width)
                y2  = y1 + int(bb.height)
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 100), 2)

                # 눈/코 키포인트
                kp = det.keypoints
                for i, (lbl, c) in enumerate(
                    [("RE", (0,200,100)), ("LE", (0,200,100)), ("N", (0,100,255))]
                ):
                    if i >= len(kp):
                        break
                    px, py = int(kp[i].x * w), int(kp[i].y * h)
                    cv2.circle(frame, (px, py), 5, c, -1)
                    cv2.putText(frame, lbl, (px + 6, py - 4),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, c, 1)

                # yaw 계산
                offset = yaw_r - ref_yaw
                if abs(offset) < HEAD_DEADZONE:
                    target_yaw = 0.0
                else:
                    sign       = 1.0 if offset > 0 else -1.0
                    target_yaw = (abs(offset) - HEAD_DEADZONE) * HEAD_SCALE * sign
                yaw_ema = 0.3 * target_yaw + 0.7 * yaw_ema

                if abs(yaw_ema) < 0.05:
                    direction = "STRAIGHT"
                    color     = (180, 180, 180)
                elif yaw_ema > 0:
                    direction = f"TURNING RIGHT  ({yaw_ema:+.2f})"
                    color     = (255, 160, 40)
                else:
                    direction = f"TURNING LEFT   ({yaw_ema:+.2f})"
                    color     = (40, 180, 255)

                # offset 바
                bar_x = int(w / 2 + offset * w * 2.0)
                bar_x = max(0, min(w - 1, bar_x))
                cv2.line(frame, (w // 2, h - 40), (bar_x, h - 40), color, 6)
                cv2.circle(frame, (bar_x, h - 40), 8, color, -1)
                cv2.line(frame, (w // 2, h - 55), (w // 2, h - 25), (100, 100, 100), 2)

                print(f"\r[HEAD] yaw_r={yaw_r:+.3f}  ref={ref_yaw:+.3f}  "
                      f"offset={offset:+.3f}  yaw_ema={yaw_ema:+.2f}  ed={ed:.3f}  "
                      f"→ {direction[:14]}   ", end="")
        else:
            print(f"\r[HEAD] 얼굴 미감지                                              ", end="")

        # 기준선
        cv2.line(frame, (w // 2, 0), (w // 2, h), (60, 60, 180), 1)

        cv2.putText(frame, direction, (20, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 2)
        cv2.putText(frame, f"ref_yaw={ref_yaw:.3f}  [SPACE] reset",
                    (20, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (150, 150, 150), 1)

        cv2.imshow("Head Yaw Test (MediaPipe)", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord(" "):
            if result.detections:
                yaw_r2, ed2 = compute_face_yaw(result.detections[0])
                if ed2 > 1e-4:
                    ref_yaw = yaw_r2
                    yaw_ema = 0.0
                    print(f"\n[HEAD] 기준 yaw={ref_yaw:.3f} 재설정")
                else:
                    print("\n[HEAD] 눈 간격 너무 좁음 — 재설정 실패")
            else:
                print("\n[HEAD] 얼굴 없음 — 재설정 실패")

cap.release()
cv2.destroyAllWindows()
