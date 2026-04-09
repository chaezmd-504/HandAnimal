"""
gesture_detector.py
--------------------
손 제스처를 인식해 동물 전환 명령을 내린다.

지원 제스처:
  👍 엄지 올리기 (Thumbs Up) → 다음 동물
  ✊ 주먹 쥐기   (Fist)       → 이전 동물

오작동 방지: 동일 제스처가 인식되면 COOLDOWN 초간 재인식 안 함.

사용 예:
    detector = GestureDetector(cooldown=1.5)
    gesture = detector.detect(landmarks)
    # 반환값: "thumbs_up" | "fist" | None
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np

COOLDOWN = 1.5   # 초 (오작동 방지를 위해 늘림)

# y 좌표 차이 최소 마진 (0~1 정규화 기준, 클수록 엄격)
_FOLD_MARGIN  = 0.04   # 손가락 접힘 판별 마진
_THUMB_MARGIN = 0.06   # 엄지 올리기 판별 마진


def _landmark_to_array(landmarks) -> np.ndarray:
    """NormalizedLandmark 리스트 → (21, 3) numpy 배열."""
    return np.array([[lm.x, lm.y, lm.z] for lm in landmarks])


# ──────────────────────────────────────────────────────────────
# 제스처 판별 함수
# ──────────────────────────────────────────────────────────────

def _is_finger_folded(pts: np.ndarray, tip: int, pip: int, mcp: int) -> bool:
    """
    손가락이 확실히 접혀 있는지 확인한다.
    단순 y 비교 대신 마진을 적용해 오작동을 줄인다.
    tip이 pip보다 _FOLD_MARGIN 이상 아래에 있어야 접힌 것으로 간주.
    """
    return (pts[tip][1] > pts[pip][1] + _FOLD_MARGIN
            and pts[pip][1] > pts[mcp][1])


def _is_thumb_up(pts: np.ndarray) -> bool:
    """
    엄지 올리기 판별 (강화 버전).
    조건:
      1. 엄지 끝(4)이 검지 MCP(5) 보다 _THUMB_MARGIN 이상 위
      2. 엄지 끝(4)이 엄지 MCP(2) 보다 위
      3. 엄지 끝(4)이 검지 끝(8) 보다 _THUMB_MARGIN 이상 위
      4. 나머지 4손가락 모두 확실히 접혀 있음
    """
    thumb_above_knuckles    = pts[4][1] < pts[5][1] - _THUMB_MARGIN
    thumb_above_mcp         = pts[4][1] < pts[2][1]
    thumb_higher_than_index = pts[4][1] < pts[8][1] - _THUMB_MARGIN

    index_folded  = _is_finger_folded(pts, 8,  6,  5)
    middle_folded = _is_finger_folded(pts, 12, 10,  9)
    ring_folded   = _is_finger_folded(pts, 16, 14, 13)
    pinky_folded  = _is_finger_folded(pts, 20, 18, 17)

    return (thumb_above_knuckles and thumb_above_mcp
            and thumb_higher_than_index
            and index_folded and middle_folded and ring_folded and pinky_folded)


def _is_fist(pts: np.ndarray) -> bool:
    """
    주먹 쥐기 판별 (강화 버전).
    조건: 5손가락 모두 확실히 접혀 있음 + 모든 손가락 끝이 손목보다 위
    """
    thumb_folded  = pts[4][1] > pts[3][1]
    index_folded  = _is_finger_folded(pts, 8,  6,  5)
    middle_folded = _is_finger_folded(pts, 12, 10,  9)
    ring_folded   = _is_finger_folded(pts, 16, 14, 13)
    pinky_folded  = _is_finger_folded(pts, 20, 18, 17)

    # 손가락 끝들이 손목(0)보다 위에 있어야 함 (손 내린 상태 제외)
    fingers_above_wrist = all(
        pts[tip][1] < pts[0][1]
        for tip in [8, 12, 16, 20]
    )

    return (thumb_folded and index_folded and middle_folded
            and ring_folded and pinky_folded and fingers_above_wrist)


# ──────────────────────────────────────────────────────────────
# 메인 클래스
# ──────────────────────────────────────────────────────────────

class GestureDetector:
    """
    실시간 제스처 감지기.

    Args:
        cooldown: 동일 제스처 재인식 금지 시간(초). 기본값 1.5초.
    """

    def __init__(self, cooldown: float = COOLDOWN):
        self.cooldown = cooldown
        self._last_gesture: Optional[str] = None
        self._last_time: float = 0.0

    def detect(self, landmarks) -> Optional[str]:
        """
        MediaPipe Tasks API 의 NormalizedLandmark 리스트를 받아
        제스처 이름을 반환한다. 인식 안 되면 None.

        반환값: "thumbs_up" | "fist" | None
        """
        pts = _landmark_to_array(landmarks)
        now = time.time()

        gesture: Optional[str] = None
        if _is_fist(pts):
            gesture = "fist"
        elif _is_thumb_up(pts):
            gesture = "thumbs_up"

        if gesture is None:
            return None

        # 쿨다운 체크: 같은 제스처가 연속으로 인식되면 무시
        if gesture == self._last_gesture and (now - self._last_time) < self.cooldown:
            return None

        self._last_gesture = gesture
        self._last_time    = now
        return gesture

    def reset(self):
        """쿨다운 상태를 초기화한다."""
        self._last_gesture = None
        self._last_time    = 0.0


# ──────────────────────────────────────────────────────────────
# 콘솔 테스트 (웹캠 연결 후 제스처 출력)
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os, sys, time
    import cv2
    import mediapipe as mp

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tracking.hand_tracker import download_model, MODEL_PATH

    BaseOptions     = mp.tasks.BaseOptions
    HandLandmarker  = mp.tasks.vision.HandLandmarker
    HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
    VisionRunningMode     = mp.tasks.vision.RunningMode

    download_model()

    detector = GestureDetector(cooldown=1.5)
    cap = cv2.VideoCapture(0)

    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=VisionRunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    print("[INFO] 제스처 테스트 시작. 종료: q")
    print("  👍 엄지 올리기 → 다음 동물")
    print("  ✊ 주먹 쥐기   → 이전 동물")

    with HandLandmarker.create_from_options(options) as landmarker:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            h, w    = frame.shape[:2]
            ts_ms   = int(time.time() * 1000)
            mp_img  = mp.Image(image_format=mp.ImageFormat.SRGB,
                               data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            result  = landmarker.detect_for_video(mp_img, ts_ms)

            if result.hand_landmarks:
                gesture = detector.detect(result.hand_landmarks[0])
                if gesture == "thumbs_up":
                    print("[GESTURE] 👍 엄지 올리기 → 다음 동물")
                elif gesture == "fist":
                    print("[GESTURE] ✊ 주먹 쥐기 → 이전 동물")

                label = gesture or "none"
                cv2.putText(frame, f"Gesture: {label}", (10, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 200, 0), 2)

            cv2.imshow("Gesture Test", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    cap.release()
    cv2.destroyAllWindows()
