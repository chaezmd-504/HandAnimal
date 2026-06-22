"""
measure_dof_noise.py
--------------------
손을 정지 상태로 유지하면서 3가지 좌표계 방식의 DOF 측정 변동폭을 비교한다.

결과: 표 2의 ±X° 수치 근거로 사용 가능.

실행:
    conda activate capstone_env
    python scripts/measure_dof_noise.py

조작:
  - 카메라 앞에서 손을 펼쳐 정지 유지 (3~5초)
  - 측정 완료 후 q 키로 종료
"""

import os
import sys
import time
import cv2
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PYTHON_DIR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import mediapipe as mp

# MediaPipe Tasks API (0.10.x)
BaseOptions        = mp.tasks.BaseOptions
HandLandmarker     = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
VisionRunningMode  = mp.tasks.vision.RunningMode

MODEL_PATH = os.path.join(PYTHON_DIR, "tracking", "hand_landmarker.task")
MODEL_URL  = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)

# ──────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────

def download_model():
    if not os.path.exists(MODEL_PATH):
        import urllib.request
        print(f"[INFO] 모델 파일 다운로드 중...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        print("[INFO] 완료.")


def angle_3pts(p1, p2, p3):
    """p1-p2-p3 의 내각 (도)"""
    v1 = np.array(p1) - np.array(p2)
    v2 = np.array(p3) - np.array(p2)
    cos = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-8)
    return float(np.degrees(np.arccos(np.clip(cos, -1, 1))))


def lm_to_np(landmarks):
    """MediaPipe landmark list → numpy array (N, 3)"""
    return np.array([[lm.x, lm.y, lm.z] for lm in landmarks])


# ──────────────────────────────────────────────
# 세 가지 방식으로 굴신각 계산
# ──────────────────────────────────────────────

DOF_KEYS = [
    ("index_mcp",  0,  5,  6),
    ("index_pip",  5,  6,  7),
    ("middle_mcp", 0,  9, 10),
    ("ring_mcp",   0, 13, 14),
]


def method_world(world_lm):
    """방법 1: 3D world frame (raw)"""
    pts = lm_to_np(world_lm)
    return {name: angle_3pts(pts[i], pts[j], pts[k])
            for name, i, j, k in DOF_KEYS}


def method_palm_local(world_lm):
    """방법 2: palm-local 3D (손목 기준 좌표 변환)"""
    pts = lm_to_np(world_lm)
    origin = pts[0]
    x_axis = pts[5] - pts[0]
    x_axis /= (np.linalg.norm(x_axis) + 1e-8)
    z_ref  = np.cross(pts[5] - pts[0], pts[17] - pts[0])
    z_axis = z_ref / (np.linalg.norm(z_ref) + 1e-8)
    y_axis = np.cross(z_axis, x_axis)
    R = np.column_stack([x_axis, y_axis, z_axis])
    local = (pts - origin) @ R
    return {name: angle_3pts(local[i], local[j], local[k])
            for name, i, j, k in DOF_KEYS}


def method_2d_image(img_lm):
    """방법 3: 2D 이미지 좌표 palm-local"""
    pts_2d = np.array([[lm.x, lm.y] for lm in img_lm])
    origin = pts_2d[0]
    v_x = pts_2d[5] - pts_2d[0]
    v_x /= (np.linalg.norm(v_x) + 1e-8)
    v_y = np.array([-v_x[1], v_x[0]])     # CCW 90°
    R2 = np.column_stack([v_x, v_y])
    local_2d = [(R2.T @ (p - origin)).tolist() + [0.0] for p in pts_2d]
    return {name: angle_3pts(local_2d[i], local_2d[j], local_2d[k])
            for name, i, j, k in DOF_KEYS}


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main():
    download_model()

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] 웹캠을 열 수 없습니다.")
        return

    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=VisionRunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.7,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    records = {"world": [], "palm_local": [], "2d_image": []}
    N_TARGET = 150  # 약 5초 @ 30fps

    print("=" * 55)
    print("  손을 펼쳐 카메라 앞에 정지 유지 (5초)")
    print("  q 키: 종료 / 충분히 모이면 자동 종료")
    print("=" * 55)

    with HandLandmarker.create_from_options(options) as landmarker:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            frame = cv2.flip(frame, 1)
            timestamp_ms = int(time.time() * 1000)

            mp_image = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
            )
            result = landmarker.detect_for_video(mp_image, timestamp_ms)

            if result.hand_landmarks and result.hand_world_landmarks:
                img_lm   = result.hand_landmarks[0]
                world_lm = result.hand_world_landmarks[0]

                records["world"].append(method_world(world_lm))
                records["palm_local"].append(method_palm_local(world_lm))
                records["2d_image"].append(method_2d_image(img_lm))

            n = len(records["world"])
            cv2.putText(frame, f"수집: {n}/{N_TARGET}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
            if n >= N_TARGET:
                cv2.putText(frame, "완료! 결과 출력 중...",
                            (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 255), 2)

            cv2.imshow("DOF Noise Measurement", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or n >= N_TARGET:
                break

    cap.release()
    cv2.destroyAllWindows()

    n = len(records["world"])
    if n < 30:
        print(f"데이터 부족 ({n}개). 다시 실행하세요.")
        return

    print(f"\n측정 프레임: {n}개\n")
    print(f"{'DOF':<14}  {'world ±(°)':>11}  {'palm_local ±(°)':>16}  {'2D_image ±(°)':>14}")
    print("─" * 62)

    for name, *_ in DOF_KEYS:
        stds = {}
        for m in records:
            vals = [r[name] for r in records[m] if name in r]
            stds[m] = float(np.std(vals)) if vals else float('nan')
        print(f"{name:<14}  "
              f"{stds['world']:>11.2f}  "
              f"{stds['palm_local']:>16.2f}  "
              f"{stds['2d_image']:>14.2f}")

    print("\n* ±값은 표준편차(1σ). 보고서 표 2의 '측정 변동 폭'으로 사용 가능.")
    print("* 동일 측정을 3회 반복 후 평균을 사용하는 것을 권장.")


if __name__ == "__main__":
    main()
