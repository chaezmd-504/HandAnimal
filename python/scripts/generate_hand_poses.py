"""
generate_hand_poses.py
----------------------
MANO 모델에서 손 포즈 10,000개를 샘플링하여
data/hand_poses/poses_10k.npy 로 저장한다.

출력 형식: (N, 15) numpy array
  - 15개 DOF = 5손가락 × 3관절(MCP, PIP, DIP) 구부림 각도 (degrees)
  - 열 순서: thumb_cmc, thumb_mcp, thumb_ip,
             index_mcp, index_pip, index_dip,
             middle_mcp, middle_pip, middle_dip,
             ring_mcp, ring_pip, ring_dip,
             pinky_mcp, pinky_pip, pinky_dip

실행:
    conda activate capstone_env
    python scripts/generate_hand_poses.py
"""

import os
import sys
import pickle
import numpy as np

# ──────────────────────────────────────────────────────────────
# 1. 경로 설정
# ──────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SCRIPT_DIR)
MANO_PATH  = os.path.join(PYTHON_DIR, "data", "mano", "MANO_RIGHT.pkl")
OUT_DIR    = os.path.join(PYTHON_DIR, "data", "hand_poses")
OUT_PATH   = os.path.join(OUT_DIR, "poses_10k.npy")
META_PATH  = os.path.join(OUT_DIR, "joint_names.txt")

N_SAMPLES  = 10_000
RANDOM_SEED = 42

# ──────────────────────────────────────────────────────────────
# 2. 각 DOF 의 가동 범위 (ROM) 정의 — 해부학적 근거
#    (min_deg, max_deg, rest_deg)
# ──────────────────────────────────────────────────────────────
DOF_DEFS = [
    # name              min   max  rest
    ("thumb_cmc",        0,   60,  20),
    ("thumb_mcp",        0,   60,  20),
    ("thumb_ip",         0,   80,  15),
    ("index_mcp",        0,   90,  10),
    ("index_pip",        0,  110,  10),
    ("index_dip",        0,   90,  10),
    ("middle_mcp",       0,   90,  10),
    ("middle_pip",       0,  110,  10),
    ("middle_dip",       0,   90,  10),
    ("ring_mcp",         0,   90,  10),
    ("ring_pip",         0,  110,  10),
    ("ring_dip",         0,   90,  10),
    ("pinky_mcp",        0,   80,  10),
    ("pinky_pip",        0,  100,  10),
    ("pinky_dip",        0,   80,  10),
]

DOF_NAMES = [d[0] for d in DOF_DEFS]
DOF_MIN   = np.array([d[1] for d in DOF_DEFS], dtype=float)
DOF_MAX   = np.array([d[2] for d in DOF_DEFS], dtype=float)
DOF_REST  = np.array([d[3] for d in DOF_DEFS], dtype=float)


# ──────────────────────────────────────────────────────────────
# 3. MANO 모델 기반 FK 전처리 (가능한 경우)
# ──────────────────────────────────────────────────────────────
def rodrigues(r: np.ndarray) -> np.ndarray:
    """Rodrigues 공식으로 axis-angle → 3×3 rotation matrix 변환."""
    theta = np.linalg.norm(r)
    if theta < 1e-8:
        return np.eye(3)
    axis = r / theta
    K = np.array([
        [ 0,       -axis[2],  axis[1]],
        [ axis[2],  0,       -axis[0]],
        [-axis[1],  axis[0],  0      ],
    ])
    return np.eye(3) + np.sin(theta) * K + (1 - np.cos(theta)) * (K @ K)


def mano_fk(pose_params: np.ndarray, model: dict) -> np.ndarray:
    """
    MANO 포즈 파라미터(45-dim axis-angle)에서 관절 위치(16, 3)를 계산한다.
    pose_params: (45,) — wrist 제외 손가락 관절만
    """
    v_template  = model["v_template"]                   # (778, 3)
    J_regressor = np.array(model["J_regressor"].todense()
                           if hasattr(model["J_regressor"], "todense")
                           else model["J_regressor"])   # (16, 778)
    kintree     = model["kintree_table"]                # (2, 16)
    shapedirs   = model["shapedirs"]                    # (778, 3, 10)
    posedirs    = model["posedirs"]                     # (778, 3, 135)

    # 중립 형태 관절 위치 (shape = 0 가정)
    J = J_regressor @ v_template                        # (16, 3)

    # 전체 pose = [wrist(3), fingers(45)] → 48
    full_pose = np.concatenate([np.zeros(3), pose_params])  # (48,)

    # 각 관절 회전행렬 계산
    R = [rodrigues(full_pose[i*3 : i*3+3]) for i in range(16)]

    # 전역 변환 계산 (순방향 기구학)
    G = [None] * 16
    G[0] = np.eye(4)
    G[0][:3, :3] = R[0]
    G[0][:3,  3] = J[0]

    for i in range(1, 16):
        parent = kintree[0, i]
        T_local = np.eye(4)
        T_local[:3, :3] = R[i]
        T_local[:3,  3] = J[i] - J[parent]
        G[i] = G[parent] @ T_local

    joint_pos = np.array([G[i][:3, 3] for i in range(16)])  # (16, 3)
    return joint_pos


def joint_positions_to_angles(joint_pos: np.ndarray) -> np.ndarray:
    """
    MANO 16-관절 위치 → 15-DOF 구부림 각도 변환
    MANO 관절 인덱스:
      0: Wrist
      1-3: Index (MCP, PIP, DIP)
      4-6: Middle (MCP, PIP, DIP)
      7-9: Ring (MCP, PIP, DIP)
      10-12: Pinky (MCP, PIP, DIP)
      13-15: Thumb (CMC, MCP, IP)
    """
    def angle_deg(a, b, c):
        v1 = a - b
        v2 = c - b
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 < 1e-8 or n2 < 1e-8:
            return 0.0
        cos_a = np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0)
        return float(np.degrees(np.arccos(cos_a)))

    W  = joint_pos[0]
    angles = np.zeros(15)

    # Thumb: CMC(13), MCP(14), IP(15)
    angles[0] = angle_deg(W,             joint_pos[13], joint_pos[14])  # thumb_cmc
    angles[1] = angle_deg(joint_pos[13], joint_pos[14], joint_pos[15])  # thumb_mcp
    angles[2] = 0.0  # thumb_ip: no tip landmark in MANO 16-joint model

    # Index: MCP(1), PIP(2), DIP(3)
    angles[3] = angle_deg(W,            joint_pos[1], joint_pos[2])
    angles[4] = angle_deg(joint_pos[1], joint_pos[2], joint_pos[3])
    angles[5] = 0.0

    # Middle: MCP(4), PIP(5), DIP(6)
    angles[6] = angle_deg(W,            joint_pos[4], joint_pos[5])
    angles[7] = angle_deg(joint_pos[4], joint_pos[5], joint_pos[6])
    angles[8] = 0.0

    # Ring: MCP(7), PIP(8), DIP(9)
    angles[9]  = angle_deg(W,            joint_pos[7], joint_pos[8])
    angles[10] = angle_deg(joint_pos[7], joint_pos[8], joint_pos[9])
    angles[11] = 0.0

    # Pinky: MCP(10), PIP(11), DIP(12)
    angles[12] = angle_deg(W,             joint_pos[10], joint_pos[11])
    angles[13] = angle_deg(joint_pos[10], joint_pos[11], joint_pos[12])
    angles[14] = 0.0

    return angles


def sample_with_mano(n: int, rng: np.random.Generator) -> np.ndarray:
    """MANO FK를 사용한 포즈 샘플링."""
    print(f"[INFO] MANO 모델 로드 중: {MANO_PATH}")
    with open(MANO_PATH, "rb") as f:
        model = pickle.load(f, encoding="latin1")

    # MANO 손 포즈 PCA 공간에서 샘플링
    hands_mean = model.get("hands_mean", np.zeros(45))          # (45,)
    hands_comps = model.get("hands_components", None)

    poses = np.zeros((n, 15))
    for i in range(n):
        if hands_comps is not None and hands_comps.shape[0] >= 20:
            # PCA 공간에서 샘플링 (상위 20개 성분)
            n_comp = min(20, hands_comps.shape[0])
            coeff  = rng.normal(0, 1, n_comp)
            pose45 = hands_mean + hands_comps[:n_comp].T @ coeff
        else:
            # 직접 axis-angle 샘플링
            pose45 = rng.uniform(-0.5, 0.5, 45)

        joint_pos = mano_fk(pose45, model)
        angles    = joint_positions_to_angles(joint_pos)
        # ROM 범위로 클리핑
        poses[i] = np.clip(angles, DOF_MIN, DOF_MAX)

        if (i + 1) % 1000 == 0:
            print(f"  {i+1}/{n} 샘플링 완료...")

    return poses


# ──────────────────────────────────────────────────────────────
# 4. 합성 샘플링 (MANO 없을 때 폴백)
# ──────────────────────────────────────────────────────────────
def sample_synthetic(n: int, rng: np.random.Generator) -> np.ndarray:
    """
    각 DOF의 ROM 범위 내에서 Beta 분포로 샘플링.
    실제 손 포즈는 극단값보다 중간 범위에 몰려있으므로 Beta(2,2) 사용.
    """
    print("[INFO] 합성 샘플링 모드 (MANO FK 없이)")
    u     = rng.beta(2, 2, size=(n, len(DOF_DEFS)))     # (N, 15), 0~1
    poses = DOF_MIN + u * (DOF_MAX - DOF_MIN)            # ROM으로 스케일
    return poses.astype(np.float32)


# ──────────────────────────────────────────────────────────────
# 5. 단일 포즈 테스트 출력
# ──────────────────────────────────────────────────────────────
def print_single_pose(poses: np.ndarray):
    print("\n=== 손 포즈 샘플 1개 (관절 각도, degrees) ===")
    for name, val in zip(DOF_NAMES, poses[0]):
        print(f"  {name:15s}: {val:6.1f}°")
    print()


# ──────────────────────────────────────────────────────────────
# 6. 메인
# ──────────────────────────────────────────────────────────────
def main():
    rng = np.random.default_rng(RANDOM_SEED)

    # MANO 시도 → 실패하면 합성 모드
    try:
        if not os.path.exists(MANO_PATH):
            raise FileNotFoundError(f"MANO 모델 없음: {MANO_PATH}")
        poses = sample_with_mano(N_SAMPLES, rng)
        method = "MANO FK"
    except Exception as e:
        print(f"[WARN] MANO 샘플링 실패 ({e}), 합성 모드로 전환합니다.")
        poses = sample_synthetic(N_SAMPLES, rng)
        method = "합성(Synthetic)"

    # 단일 포즈 출력 (테스트)
    print_single_pose(poses)

    # 저장
    os.makedirs(OUT_DIR, exist_ok=True)
    np.save(OUT_PATH, poses)
    with open(META_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(DOF_NAMES))

    print(f"[OK] {N_SAMPLES}개 포즈 저장 완료 ({method})")
    print(f"     경로: {OUT_PATH}")
    print(f"     shape: {poses.shape}  dtype: {poses.dtype}")
    print(f"     각도 통계 — min: {poses.min():.1f}°  max: {poses.max():.1f}°  mean: {poses.mean():.1f}°")


if __name__ == "__main__":
    main()
