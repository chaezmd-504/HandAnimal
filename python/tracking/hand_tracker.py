import cv2
import mediapipe as mp
import numpy as np

from occlusion_handler import OcclusionHandler


# MediaPipe 손 관절 인덱스 상수
FINGER_JOINTS = {
    "thumb":  [(1, 2, 3), (2, 3, 4)],
    "index":  [(5, 6, 7), (6, 7, 8)],
    "middle": [(9, 10, 11), (10, 11, 12)],
    "ring":   [(13, 14, 15), (14, 15, 16)],
    "pinky":  [(17, 18, 19), (18, 19, 20)],
}

# 손가락별 대표 구부림 각도에 사용할 관절 3개 (MCP-PIP-DIP)
FINGER_BEND_JOINTS = {
    "thumb":  (1, 2, 4),
    "index":  (5, 6, 8),
    "middle": (9, 10, 12),
    "ring":   (13, 14, 16),
    "pinky":  (17, 18, 20),
}


def calculate_bend_angle(p1: tuple, p2: tuple, p3: tuple) -> float:
    """
    세 점 p1-p2-p3 로 이루어진 각도를 계산한다 (p2가 꼭짓점).

    Args:
        p1, p2, p3: (x, y, z) 좌표 튜플

    Returns:
        각도 (도 단위, 0 ~ 180)
    """
    v1 = np.array(p1) - np.array(p2)
    v2 = np.array(p3) - np.array(p2)

    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)

    if norm1 == 0 or norm2 == 0:
        return 0.0

    cos_angle = np.dot(v1, v2) / (norm1 * norm2)
    cos_angle = np.clip(cos_angle, -1.0, 1.0)
    angle = np.degrees(np.arccos(cos_angle))
    return float(angle)


def compute_finger_angles(filtered_coords: list[tuple]) -> dict[str, float]:
    """
    필터링된 21개 관절 좌표에서 손가락 5개의 구부림 각도를 계산한다.

    Returns:
        {"thumb": angle, "index": angle, ...}
    """
    angles = {}
    for finger, (i1, i2, i3) in FINGER_BEND_JOINTS.items():
        angle = calculate_bend_angle(
            filtered_coords[i1],
            filtered_coords[i2],
            filtered_coords[i3],
        )
        angles[finger] = angle
    return angles


def run_tracker():
    mp_hands = mp.solutions.hands
    mp_draw = mp.solutions.drawing_utils

    occlusion_handler = OcclusionHandler()

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] 웹캠을 열 수 없습니다. 장치 연결을 확인하세요.")
        return

    print("[INFO] 웹캠 연결 성공. 손 인식 시작. 종료: q")

    with mp_hands.Hands(
        static_image_mode=False,
        max_num_hands=1,
        model_complexity=1,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    ) as hands:

        while True:
            ret, frame = cap.read()
            if not ret:
                print("[ERROR] 프레임을 읽을 수 없습니다.")
                break

            # BGR → RGB 변환 후 MediaPipe 처리
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = hands.process(rgb)

            if results.multi_hand_landmarks:
                for hand_landmarks in results.multi_hand_landmarks:
                    # 관절 점 및 연결선 그리기
                    mp_draw.draw_landmarks(
                        frame,
                        hand_landmarks,
                        mp_hands.HAND_CONNECTIONS,
                    )

                    # Occlusion 처리 (칼만 필터)
                    filtered = occlusion_handler.process(hand_landmarks)

                    # 관절 좌표 및 visibility 출력
                    print("\n--- 관절 좌표 (x, y, z, visibility) ---")
                    for i, lm in enumerate(hand_landmarks.landmark):
                        fx, fy, fz = filtered[i]
                        print(
                            f"  [{i:2d}] raw=({lm.x:.3f}, {lm.y:.3f}, {lm.z:.3f}) "
                            f"vis={lm.visibility:.2f} | "
                            f"filtered=({fx:.3f}, {fy:.3f}, {fz:.3f})"
                        )

                    # 손가락 구부림 각도 계산 및 출력
                    angles = compute_finger_angles(filtered)
                    print("\n--- 손가락 구부림 각도 ---")
                    for finger, angle in angles.items():
                        print(f"  {finger:6s}: {angle:6.1f}°")

            cv2.imshow("Hand Tracker", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("[INFO] 종료합니다.")
                break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    run_tracker()
