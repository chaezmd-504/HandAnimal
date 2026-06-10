"""
face_tracker.py
---------------
MediaPipe 얼굴 추적 래퍼.

1. FaceDetector (blaze_face_short_range.tflite) — 얼굴 yaw 계산 (--head-dir)
   Keypoint 순서:
     0: right eye  1: left eye  2: nose tip  3: mouth  4: right ear  5: left ear

2. FaceLandmarker (face_landmarker.task) — 홍채 기반 시선(gaze) UV 계산 (--gaze)
   478개 랜드마크:
     0-467: face mesh
     468-472: 왼쪽 iris (468 = center)
     473-477: 오른쪽 iris (473 = center)
"""

import os
import urllib.request

# ── FaceDetector (head-dir) ──────────────────────────────────────────────────
FACE_MODEL_PATH = os.path.join(os.path.dirname(__file__), "blaze_face_short_range.tflite")
FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)

# ── FaceLandmarker (gaze) ────────────────────────────────────────────────────
FACE_LANDMARK_MODEL_PATH = os.path.join(os.path.dirname(__file__), "face_landmarker.task")
FACE_LANDMARK_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)

# FaceMesh landmark indices (face mesh, 0-467)
_EYE_L_OUTER = 33    # 왼눈 외측 코너 (image 기준: 카메라 미러 시 왼쪽)
_EYE_L_INNER = 133   # 왼눈 내측 코너
_EYE_L_TOP   = 159   # 왼눈 위쪽 경계
_EYE_L_BOT   = 145   # 왼눈 아래쪽 경계
_EYE_R_OUTER = 263   # 오른눈 외측 코너
_EYE_R_INNER = 362   # 오른눈 내측 코너
_EYE_R_TOP   = 386   # 오른눈 위쪽 경계
_EYE_R_BOT   = 374   # 오른눈 아래쪽 경계
# Iris centers (478-landmark model)
_IRIS_L_CENTER = 468
_IRIS_R_CENTER = 473


def download_face_model():
    if not os.path.exists(FACE_MODEL_PATH):
        print(f"[INFO] 얼굴 모델 다운로드 중... ({FACE_MODEL_PATH})")
        urllib.request.urlretrieve(FACE_MODEL_URL, FACE_MODEL_PATH)
        print("[INFO] 얼굴 모델 다운로드 완료.")


def download_face_landmark_model():
    if not os.path.exists(FACE_LANDMARK_MODEL_PATH):
        print(f"[INFO] FaceLandmarker 모델 다운로드 중... ({FACE_LANDMARK_MODEL_PATH})")
        urllib.request.urlretrieve(FACE_LANDMARK_MODEL_URL, FACE_LANDMARK_MODEL_PATH)
        print("[INFO] FaceLandmarker 모델 다운로드 완료.")


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


_EYE_OPEN_THR = 0.007  # 눈 개방도 최소값 (정규화 좌표 기준) — 이하면 깜빡임으로 판정

def compute_gaze_uv(face_landmarks) -> tuple[float, float, bool]:
    """
    FaceLandmarker 결과 (단일 얼굴의 478개 랜드마크)에서 시선 UV 계산.
    깜빡임(blink) 감지 시 valid=False 반환 → EMA 오염 방지.

    Returns
    -------
    (gaze_x, gaze_y, valid)
        gaze_x : 정규화된 수평 시선 [0,1] — 0=왼쪽, 0.5=중앙, 1=오른쪽
        gaze_y : 정규화된 수직 시선 [0,1] — 0=위쪽, 0.5=중앙, 1=아래쪽
        valid  : 눈 뜨고 있고 랜드마크 신뢰 가능하면 True
    """
    if len(face_landmarks) < 478:
        return 0.5, 0.5, False

    iris_l = face_landmarks[_IRIS_L_CENTER]
    iris_r = face_landmarks[_IRIS_R_CENTER]

    eye_l_outer = face_landmarks[_EYE_L_OUTER]
    eye_l_inner = face_landmarks[_EYE_L_INNER]
    eye_l_top   = face_landmarks[_EYE_L_TOP]
    eye_l_bot   = face_landmarks[_EYE_L_BOT]
    eye_r_outer = face_landmarks[_EYE_R_OUTER]
    eye_r_inner = face_landmarks[_EYE_R_INNER]
    eye_r_top   = face_landmarks[_EYE_R_TOP]
    eye_r_bot   = face_landmarks[_EYE_R_BOT]

    # ── 깜빡임 필터: 눈 세로 개방도가 임계값 이하면 무효 ──────────
    eye_l_h = eye_l_bot.y - eye_l_top.y
    eye_r_h = eye_r_bot.y - eye_r_top.y
    if eye_l_h < _EYE_OPEN_THR or eye_r_h < _EYE_OPEN_THR:
        return 0.5, 0.5, False

    # ── 수평 gaze ─────────────────────────────────────────────────
    eye_l_w = eye_l_inner.x - eye_l_outer.x
    eye_r_w = eye_r_outer.x - eye_r_inner.x
    if abs(eye_l_w) < 1e-4 or abs(eye_r_w) < 1e-4:
        return 0.5, 0.5, False

    gaze_x_l = (iris_l.x - eye_l_outer.x) / eye_l_w
    gaze_x_r = (iris_r.x - eye_r_inner.x) / eye_r_w
    gaze_x = (gaze_x_l + (1.0 - gaze_x_r)) / 2.0

    # ── 수직 gaze ─────────────────────────────────────────────────
    gaze_y_l = (iris_l.y - eye_l_top.y) / eye_l_h
    gaze_y_r = (iris_r.y - eye_r_top.y) / eye_r_h
    gaze_y = (gaze_y_l + gaze_y_r) / 2.0

    gaze_x = float(max(0.0, min(1.0, gaze_x)))
    gaze_y = float(max(0.0, min(1.0, gaze_y)))
    return gaze_x, gaze_y, True
