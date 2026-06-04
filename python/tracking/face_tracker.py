"""
face_tracker.py
---------------
MediaPipe FaceDetector Tasks API 래퍼.
얼굴 위치(x) 대신 실제 yaw 회전각(코 vs 눈 중심 비율)을 계산한다.

blaze_face_short_range.tflite 모델은 최초 실행 시 자동 다운로드.

Keypoint 순서 (MediaPipe FaceDetector):
    0: right eye
    1: left eye
    2: nose tip
    3: mouth center
    4: right ear tragion
    5: left ear tragion
"""

import os
import urllib.request

FACE_MODEL_PATH = os.path.join(os.path.dirname(__file__), "blaze_face_short_range.tflite")
FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)


def download_face_model():
    if not os.path.exists(FACE_MODEL_PATH):
        print(f"[INFO] 얼굴 모델 다운로드 중... ({FACE_MODEL_PATH})")
        urllib.request.urlretrieve(FACE_MODEL_URL, FACE_MODEL_PATH)
        print("[INFO] 얼굴 모델 다운로드 완료.")


def compute_face_yaw(detection) -> tuple[float, float]:
    """
    FaceDetector 감지 결과에서 얼굴 yaw 비율 계산.

    Parameters
    ----------
    detection : mediapipe FaceDetectorResult.detections[i]

    Returns
    -------
    (yaw_ratio, eye_dist)
        yaw_ratio : (nose.x - eye_mid.x) / eye_dist
                    ≈ 0 = 정면, 양수 = 오른쪽 회전, 음수 = 왼쪽 회전
                    (카메라 비미러 기준 — 부호는 * -1 반전 적용 후)
        eye_dist  : 두 눈 사이 거리 (0.0 = 키포인트 부족)
    """
    kp = detection.keypoints
    if len(kp) < 3:
        return 0.0, 0.0

    right_eye = kp[0]
    left_eye  = kp[1]
    nose      = kp[2]

    eye_dist  = abs(right_eye.x - left_eye.x)
    if eye_dist < 1e-4:
        return 0.0, eye_dist

    eye_mid_x = (right_eye.x + left_eye.x) / 2.0
    # 비미러 카메라: 오른쪽 회전 → nose.x < eye_mid_x → ratio 음수
    # 직관적 부호로 반전: 오른쪽 회전 = 양수
    yaw_ratio = -((nose.x - eye_mid_x) / eye_dist)
    return float(yaw_ratio), float(eye_dist)
