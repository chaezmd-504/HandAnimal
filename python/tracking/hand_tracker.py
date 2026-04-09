import cv2
import numpy as np
import mediapipe as mp
import urllib.request
import os
import time

try:
    from occlusion_handler import OcclusionHandler
except ModuleNotFoundError:
    from tracking.occlusion_handler import OcclusionHandler

# MediaPipe Tasks API
BaseOptions = mp.tasks.BaseOptions
HandLandmarker = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode

MODEL_PATH = os.path.join(os.path.dirname(__file__), "hand_landmarker.task")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)

FINGER_BEND_JOINTS = {
    "thumb":  (1, 2, 4),
    "index":  (5, 6, 8),
    "middle": (9, 10, 12),
    "ring":   (13, 14, 16),
    "pinky":  (17, 18, 20),
}

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (0, 9), (9, 10), (10, 11), (11, 12),
    (0, 13), (13, 14), (14, 15), (15, 16),
    (0, 17), (17, 18), (18, 19), (19, 20),
    (5, 9), (9, 13), (13, 17),
]


def download_model():
    if not os.path.exists(MODEL_PATH):
        print(f"[INFO] 모델 파일 다운로드 중... ({MODEL_PATH})")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        print("[INFO] 다운로드 완료.")


def calculate_bend_angle(p1: tuple, p2: tuple, p3: tuple) -> float:
    """세 점 p1-p2-p3 로 이루어진 각도를 계산한다 (p2가 꼭짓점)."""
    v1 = np.array(p1) - np.array(p2)
    v2 = np.array(p3) - np.array(p2)
    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    cos_angle = np.clip(np.dot(v1, v2) / (norm1 * norm2), -1.0, 1.0)
    return float(np.degrees(np.arccos(cos_angle)))


def compute_finger_angles(filtered_coords: list[tuple]) -> dict[str, float]:
    """필터링된 21개 관절 좌표에서 손가락 5개의 구부림 각도를 계산한다."""
    return {
        finger: calculate_bend_angle(
            filtered_coords[i1], filtered_coords[i2], filtered_coords[i3]
        )
        for finger, (i1, i2, i3) in FINGER_BEND_JOINTS.items()
    }


def draw_landmarks(frame, landmarks, h: int, w: int, color: tuple = (0, 255, 0)):
    """관절 점과 연결선을 프레임에 그린다."""
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for start, end in HAND_CONNECTIONS:
        cv2.line(frame, pts[start], pts[end], color, 2)
    for pt in pts:
        cv2.circle(frame, pt, 4, (0, 0, 255), -1)


def run_tracker():
    download_model()

    # 왼손/오른손 각각 독립적인 칼만 필터 유지
    occlusion_handlers = {
        "left":  OcclusionHandler(),
        "right": OcclusionHandler(),
    }

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] 웹캠을 열 수 없습니다. 장치 연결을 확인하세요.")
        return
    print("[INFO] 웹캠 연결 성공. 양손 인식 시작. 종료: q")
    print("  왼손: 초록  /  오른손: 파랑 (웹캠 미러 기준)")

    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=VisionRunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    # 손 색상: MediaPipe는 미러 이미지 기준으로 handedness를 반환
    HAND_COLOR = {"left": (0, 255, 0), "right": (255, 100, 0)}

    with HandLandmarker.create_from_options(options) as landmarker:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("[ERROR] 프레임을 읽을 수 없습니다.")
                break

            h, w = frame.shape[:2]
            timestamp_ms = int(time.time() * 1000)

            mp_image = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
            )
            result = landmarker.detect_for_video(mp_image, timestamp_ms)

            hands_angles: dict[str, dict[str, float]] = {}

            if result.hand_landmarks:
                for landmarks, handedness_list in zip(
                    result.hand_landmarks, result.handedness
                ):
                    # MediaPipe Tasks API: handedness[0].category_name = "Left" / "Right"
                    side = handedness_list[0].category_name.lower()
                    color = HAND_COLOR.get(side, (0, 255, 0))
                    draw_landmarks(frame, landmarks, h, w, color=color)

                    filtered = occlusion_handlers[side].process(landmarks)
                    angles = compute_finger_angles(filtered)
                    hands_angles[side] = angles

                print("\n--- 손가락 구부림 각도 ---")
                for side in ("left", "right"):
                    if side not in hands_angles:
                        print(f"  [{side:5s}] 미감지")
                        continue
                    angles_str = "  ".join(
                        f"{f}={a:.0f}°" for f, a in hands_angles[side].items()
                    )
                    print(f"  [{side:5s}] {angles_str}")

            cv2.imshow("Hand Tracker", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("[INFO] 종료합니다.")
                break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    run_tracker()
