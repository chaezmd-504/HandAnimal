import numpy as np
from filterpy.kalman import KalmanFilter


class JointKalmanFilter:
    """단일 관절의 x, y, z 좌표를 추적하는 칼만 필터."""

    def __init__(self):
        # 상태: [x, y, z, vx, vy, vz] (위치 + 속도)
        kf = KalmanFilter(dim_x=6, dim_z=3)

        dt = 1.0  # 프레임 간격 (정규화된 시간 단위)

        # 상태 전이 행렬 (등속 운동 모델)
        kf.F = np.array([
            [1, 0, 0, dt, 0,  0 ],
            [0, 1, 0, 0,  dt, 0 ],
            [0, 0, 1, 0,  0,  dt],
            [0, 0, 0, 1,  0,  0 ],
            [0, 0, 0, 0,  1,  0 ],
            [0, 0, 0, 0,  0,  1 ],
        ], dtype=float)

        # 측정 행렬 (위치만 관측)
        kf.H = np.array([
            [1, 0, 0, 0, 0, 0],
            [0, 1, 0, 0, 0, 0],
            [0, 0, 1, 0, 0, 0],
        ], dtype=float)

        # 측정 노이즈 공분산
        kf.R = np.eye(3) * 0.01

        # 프로세스 노이즈 공분산
        kf.Q = np.eye(6) * 0.001

        # 초기 공분산
        kf.P = np.eye(6) * 1.0

        # 초기 상태
        kf.x = np.zeros((6, 1))

        self.kf = kf
        self.initialized = False

    def update(self, x: float, y: float, z: float, visibility: float) -> tuple[float, float, float]:
        """
        관절 좌표를 업데이트하고 필터링된 좌표를 반환한다.

        Args:
            x, y, z: MediaPipe에서 측정된 관절 좌표
            visibility: 관절 신뢰도 (0.0 ~ 1.0)

        Returns:
            필터링된 (x, y, z) 좌표
        """
        if not self.initialized:
            self.kf.x[:3] = np.array([[x], [y], [z]])
            self.initialized = True

        self.kf.predict()

        if visibility >= 0.5:
            # 측정값으로 필터 업데이트
            self.kf.update(np.array([[x], [y], [z]]))

        fx, fy, fz = float(self.kf.x[0]), float(self.kf.x[1]), float(self.kf.x[2])
        return fx, fy, fz


class OcclusionHandler:
    """21개 손 관절 각각에 독립적인 칼만 필터를 적용하는 Occlusion 핸들러."""

    NUM_JOINTS = 21

    def __init__(self):
        self.filters = [JointKalmanFilter() for _ in range(self.NUM_JOINTS)]

    def process(self, landmarks) -> list[tuple[float, float, float]]:
        """
        MediaPipe NormalizedLandmarkList를 받아 필터링된 좌표 리스트를 반환한다.

        Args:
            landmarks: mediapipe.framework.formats.landmark_pb2.NormalizedLandmarkList

        Returns:
            List of (x, y, z) tuples, length 21
        """
        result = []
        for i, lm in enumerate(landmarks.landmark):
            fx, fy, fz = self.filters[i].update(lm.x, lm.y, lm.z, lm.visibility)
            result.append((fx, fy, fz))
        return result
