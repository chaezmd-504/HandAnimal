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

# MediaPipe landmark 인덱스 (참고용)
# 0=wrist, 1=thumb_cmc, 2=thumb_mcp, 3=thumb_ip, 4=thumb_tip
# 5=index_mcp, 6=index_pip, 7=index_dip, 8=index_tip
# 9=middle_mcp, 10=middle_pip, 11=middle_dip, 12=middle_tip
# 13=ring_mcp, 14=ring_pip, 15=ring_dip, 16=ring_tip
# 17=pinky_mcp, 18=pinky_pip, 19=pinky_dip, 20=pinky_tip

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


def _palm_local_landmarks(lm: list) -> list:
    """
    21개 world-frame 랜드마크를 palm-local 3D 좌표계로 변환한다.
    엄지 각도 계산에 사용 (엄지는 palm 평면 밖으로 움직임).

    palm-local 좌표계:
      origin : wrist(0)
      x축    : wrist → index_MCP(5)
      z축    : palm 법선
      y축    : z × x

    변환 실패(특이행렬 등) 시 원본 lm 반환.
    """
    origin = lm[0]
    v_x = lm[5] - lm[0]
    x_len = np.linalg.norm(v_x)
    if x_len < 1e-8:
        return lm

    v_x = v_x / x_len
    v_z = np.cross(lm[5] - lm[0], lm[17] - lm[0])
    z_len = np.linalg.norm(v_z)
    if z_len < 1e-8:
        return lm

    v_z = v_z / z_len
    v_y = np.cross(v_z, v_x)
    R = np.column_stack([v_x, v_y, v_z])
    return [R.T @ (p - origin) for p in lm]


def _img_palm_local_2d(img_lm: list) -> list:
    """
    2D 이미지 좌표 랜드마크를 palm-local 2D 좌표계로 변환 후 (x, y, 0) 반환.

    2D 이미지 좌표(screen space)는 3D world 추정보다 훨씬 안정적이다.
    손가락 굴신각(MCP/PIP/DIP)은 이 2D palm-local 좌표에서 계산한다.

    palm-local 2D:
      origin : wrist(0)의 이미지 xy
      x축    : wrist → index_MCP(5) 방향 (이미지 평면)
      y축    : x축에 수직 (CCW 90°)

    변환 실패 시 원본 xy를 그대로 (x, y, 0)으로 반환.
    """
    pts = [np.array([float(p[0]), float(p[1])]) for p in img_lm]
    origin = pts[0]
    v_x = pts[5] - pts[0]
    x_len = np.linalg.norm(v_x)
    if x_len < 1e-8:
        return [(float(p[0]), float(p[1]), 0.0) for p in img_lm]

    v_x /= x_len
    v_y = np.array([-v_x[1], v_x[0]])   # CCW 90°
    R2 = np.column_stack([v_x, v_y])     # (2, 2)

    result = []
    for p in pts:
        local = R2.T @ (p - origin)
        result.append((float(local[0]), float(local[1]), 0.0))
    return result


def compute_dof_angles(filtered_coords: list[tuple],
                       img_coords: list[tuple] | None = None) -> dict[str, float]:
    """
    필터링된 21개 관절 좌표에서 20-DOF 각도를 직접 계산한다.

    Args:
        filtered_coords : world-frame 3D 좌표 (21개 tuple). 손목 DOF 계산에 사용.
        img_coords      : 2D 이미지 좌표 (21개 tuple, optional).
                          제공 시 손가락 굴신각(엄지 제외)을 2D palm-local에서 계산.
                          3D world 추정보다 훨씬 안정적이므로 항상 제공 권장.

    반환 키는 mapping_optimizer.HAND_DOFS 의 name 필드와 1:1 대응:
      wrist_flex, wrist_dev, wrist_rot          (손목 방향 3)
      thumb_cmc, thumb_abd, thumb_mcp, thumb_ip (엄지 4)
      index_mcp/pip/dip                          (검지 3)
      middle_mcp/pip/dip                         (중지 3)
      ring_mcp/pip/dip                           (약지 3)
      pinky_cmc, pinky_mcp/pip/dip               (소지 4)

    굴신 DOF: 180 - geometric_angle (0°=완전 신전, 양수=굴신)
    손목 DOF: 양방향 (음수~양수, 0°=중립)
    """
    lm_w = [np.array(p) for p in filtered_coords]  # world frame (21, 3)
    result: dict[str, float] = {}

    # ── 1. 손목 방향 (3 DOF) — world frame ───────────────────
    # 손바닥 법선: wrist(0), index_MCP(5), pinky_MCP(17)
    v_idx = lm_w[5]  - lm_w[0]
    v_pnk = lm_w[17] - lm_w[0]
    palm_n = np.cross(v_idx, v_pnk)
    n_len = np.linalg.norm(palm_n)
    if n_len > 1e-8:
        palm_n /= n_len

    # wrist_flex: 손바닥 법선의 y성분 → 손목 전후 굴신
    result["wrist_flex"] = float(np.clip(
        np.degrees(np.arcsin(np.clip(palm_n[1], -1.0, 1.0))) * (70.0 / 90.0),
        -70, 70
    ))
    # wrist_dev: 손바닥 법선의 x성분 → 손목 좌우 편위
    result["wrist_dev"] = float(np.clip(
        np.degrees(np.arcsin(np.clip(palm_n[0], -1.0, 1.0))) * (25.0 / 90.0),
        -25, 25
    ))
    # wrist_rot: index_MCP-pinky_MCP 벡터의 이미지 평면 회전각 → 전완 회전 근사
    v_side = lm_w[5][:2] - lm_w[17][:2]
    result["wrist_rot"] = float(np.clip(
        np.degrees(np.arctan2(-v_side[1], v_side[0])),
        -90, 90
    ))

    # ── 손가락 각도용 좌표 준비 ──────────────────────────────
    # 엄지: 3D palm-local (엄지는 palm 평면 밖으로 움직임)
    lm_3d = _palm_local_landmarks(lm_w)
    c3 = [tuple(p.tolist()) for p in lm_3d]

    # 검지~소지: 2D 이미지 좌표 palm-local (img_coords 제공 시)
    #   → 3D world 깊이 추정 오차 완전 제거, 손목 방향 무관하게 안정적
    # img_coords 없으면 3D palm-local → xy 투영으로 fallback
    if img_coords is not None:
        c2 = _img_palm_local_2d(img_coords)
    else:
        c2 = [tuple(float(v) for v in (p[0], p[1], 0.0)) for p in lm_3d]

    # ── 2. 엄지 (4 DOF) — 3D ─────────────────────────────────
    result["thumb_cmc"] = float(np.clip(
        180.0 - calculate_bend_angle(c3[0], c3[1], c3[2]), 0, 60
    ))
    result["thumb_abd"] = float(np.clip(
        calculate_bend_angle(c3[5], c3[0], c3[1]), 0, 70
    ))
    result["thumb_mcp"] = float(np.clip(
        180.0 - calculate_bend_angle(c3[1], c3[2], c3[3]), 0, 60
    ))
    result["thumb_ip"] = float(np.clip(
        180.0 - calculate_bend_angle(c3[2], c3[3], c3[4]), 0, 80
    ))

    # ── 3. 검지 (3 DOF) — palm 평면 투영 ────────────────────
    result["index_mcp"] = float(np.clip(
        180.0 - calculate_bend_angle(c2[0], c2[5], c2[6]), 0, 90
    ))
    result["index_pip"] = float(np.clip(
        180.0 - calculate_bend_angle(c2[5], c2[6], c2[7]), 0, 110
    ))
    result["index_dip"] = float(np.clip(
        180.0 - calculate_bend_angle(c2[6], c2[7], c2[8]), 0, 90
    ))

    # ── 4. 중지 (3 DOF) — palm 평면 투영 ────────────────────
    result["middle_mcp"] = float(np.clip(
        180.0 - calculate_bend_angle(c2[0], c2[9], c2[10]), 0, 90
    ))
    result["middle_pip"] = float(np.clip(
        180.0 - calculate_bend_angle(c2[9], c2[10], c2[11]), 0, 110
    ))
    result["middle_dip"] = float(np.clip(
        180.0 - calculate_bend_angle(c2[10], c2[11], c2[12]), 0, 90
    ))

    # ── 5. 약지 (3 DOF) — palm 평면 투영 ────────────────────
    result["ring_mcp"] = float(np.clip(
        180.0 - calculate_bend_angle(c2[0], c2[13], c2[14]), 0, 90
    ))
    result["ring_pip"] = float(np.clip(
        180.0 - calculate_bend_angle(c2[13], c2[14], c2[15]), 0, 110
    ))
    result["ring_dip"] = float(np.clip(
        180.0 - calculate_bend_angle(c2[14], c2[15], c2[16]), 0, 90
    ))

    # ── 6. 소지 (4 DOF) — palm 평면 투영 ────────────────────
    result["pinky_cmc"] = float(np.clip(
        180.0 - calculate_bend_angle(c2[13], c2[17], c2[18]), 0, 30
    ))
    result["pinky_mcp"] = float(np.clip(
        180.0 - calculate_bend_angle(c2[0], c2[17], c2[18]), 0, 80
    ))
    result["pinky_pip"] = float(np.clip(
        180.0 - calculate_bend_angle(c2[17], c2[18], c2[19]), 0, 100
    ))
    result["pinky_dip"] = float(np.clip(
        180.0 - calculate_bend_angle(c2[18], c2[19], c2[20]), 0, 80
    ))

    # ── 손가락 커플링 적용 (검지~소지) ──────────────────────
    # 생체역학적으로 MCP/PIP/DIP는 힘줄로 연결되어 독립적으로 움직이지 않음.
    # 세 관절 측정값을 weighted composite로 합산 후 커플링 비율로 재분배.
    # 효과: ① 독립 노이즈 평균화  ② 물리적으로 일관된 관절각 보장
    #
    # 자연 굴신 기준 커플링 비율 (Power grip 연구 기반):
    #   PIP ≈ MCP × 1.5   |   DIP ≈ MCP × 0.8
    _PIP_K = 1.5
    _DIP_K = 0.8
    for _fn in ("index", "middle", "ring", "pinky"):
        _mcp = result[f"{_fn}_mcp"]
        _pip = result[f"{_fn}_pip"]
        _dip = result[f"{_fn}_dip"]
        # MCP 스케일로 정규화 후 weighted 평균
        _flex = (_mcp        * 0.55
                 + _pip / _PIP_K * 0.30
                 + _dip / _DIP_K * 0.15)
        result[f"{_fn}_mcp"] = float(np.clip(_flex,            0,  90))
        result[f"{_fn}_pip"] = float(np.clip(_flex * _PIP_K,   0, 110))
        result[f"{_fn}_dip"] = float(np.clip(_flex * _DIP_K,   0,  90))

    return result


def draw_landmarks(frame, landmarks, h: int, w: int, color: tuple = (0, 255, 0)):
    """관절 점과 연결선을 프레임에 그린다."""
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for start, end in HAND_CONNECTIONS:
        cv2.line(frame, pts[start], pts[end], color, 2)
    for pt in pts:
        cv2.circle(frame, pt, 4, (0, 0, 255), -1)


def run_tracker():
    download_model()

    # 왼손/오른손 각각 독립적인 칼만 필터 유지 (이미지 좌표 / 월드 좌표 분리)
    occlusion_handlers = {
        "left":  OcclusionHandler(),
        "right": OcclusionHandler(),
    }
    occlusion_world = {
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

            if result.hand_landmarks and result.hand_world_landmarks:
                for landmarks, world_landmarks, handedness_list in zip(
                    result.hand_landmarks, result.hand_world_landmarks, result.handedness
                ):
                    side = handedness_list[0].category_name.lower()
                    color = HAND_COLOR.get(side, (0, 255, 0))
                    draw_landmarks(frame, landmarks, h, w, color=color)

                    img_filtered   = occlusion_handlers[side].process(landmarks)
                    world_filtered = occlusion_world[side].process(world_landmarks)
                    hands_angles[side] = compute_dof_angles(world_filtered, img_filtered)

                print("\n--- 20-DOF 각도 ---")
                for side in ("left", "right"):
                    if side not in hands_angles:
                        print(f"  [{side:5s}] 미감지")
                        continue
                    dofs = hands_angles[side]
                    wrist_str = (f"flex={dofs['wrist_flex']:+.0f}° "
                                 f"dev={dofs['wrist_dev']:+.0f}° "
                                 f"rot={dofs['wrist_rot']:+.0f}°")
                    finger_str = "  ".join(
                        f"{k}={v:.0f}°"
                        for k, v in dofs.items()
                        if not k.startswith("wrist_")
                    )
                    print(f"  [{side:5s}] wrist({wrist_str})  {finger_str}")

            cv2.imshow("Hand Tracker", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("[INFO] 종료합니다.")
                break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    run_tracker()
