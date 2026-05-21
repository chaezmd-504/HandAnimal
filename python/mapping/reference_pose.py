"""
reference_pose.py  —  §8 Step 2: 기준 포즈 쌍 (g*, p*) 최적화
----------------------------------------------------------------
논문 수식 11:
    (g*, p*) = argmax_{g∈G_sub, p∈P} Q_M

확정된 매핑 M(assignment)을 고정한 채,
G_sub × P 전체를 순회하여 Q_M 이 최대인 (g, p) 쌍 선택.

출력:
    ref_H[j]    = g*[j]
        → Step 2 argmax 에서 선택된 최적 손 포즈의 DOF 값
    ref_A[id]   = a_min + (g*[M(i)] − h_min) / (h_max − h_min) × (a_max − a_min)
        → g* 의 손 DOF 값이 아바타 ROM 상에서 어느 위치인지 선형 변환
"""

import numpy as np

from .constants import HAND_DOFS, W_F, W_S, W_C, _DOF_MINS, _DOF_MAXS
from .compute_q import compute_Q


# ──────────────────────────────────────────────────────────────
# Step 2: (g*, p*) argmax 순회
# ──────────────────────────────────────────────────────────────

def optimize_reference_pose(
    assignment: np.ndarray,
    G_sub: np.ndarray,
    P: list[dict],
    C: np.ndarray,
    joints: list[dict],
) -> tuple[np.ndarray, dict, float]:
    """
    논문 수식 11: (g*, p*) = argmax_{g∈G_sub, p∈P} Q_M

    assignment : Step 1 에서 확정된 매핑 M
    G_sub      : (100 × 20) 편안함 상위 손 포즈 집합
    P          : [{joint_id: angle}] 아바타 대표 포즈 목록
    C          : (n_animal × N_HAND) 제어 점수 행렬
    joints     : skeleton JSON 관절 목록
    반환: (g_star, p_star_dict, best_Q_score)
    """
    best_score = -np.inf
    best_g: np.ndarray | None = None
    best_p: dict | None = None

    n_pairs = len(G_sub) * len(P)
    print(f"  [Step 2] 기준 포즈 탐색: {len(G_sub)} × {len(P)} = {n_pairs}쌍 순회 중...")

    for g in G_sub:
        for p_dict in P:
            score = compute_Q(g, p_dict, assignment, C, joints)
            if score > best_score:
                best_score = score
                best_g = g.copy()
                best_p = dict(p_dict)

    # ROM 중간값 기존 방식과 비교 출력
    old_H, old_A = _legacy_reference(assignment, joints)
    old_p = {aj["id"]: float(old_A[i]) for i, aj in enumerate(joints)}
    old_score = compute_Q(old_H, old_p, assignment, C, joints)
    improvement = (best_score - old_score) / (abs(old_score) + 1e-8) * 100

    print(f"  [Step 2] ROM 중간값 Q_M: {old_score:+.4f}")
    print(f"  [Step 2] argmax Q_M:    {best_score:+.4f}  (개선: {improvement:+.1f}%)")

    return best_g, best_p, best_score


def _legacy_reference(
    assignment: np.ndarray,
    joints: list[dict],
) -> tuple[np.ndarray, np.ndarray]:
    """비교용 기존 방식 — 매핑된 DOF 의 ROM 중간값."""
    H = np.array([float(d["rest"]) for d in HAND_DOFS])
    for i, aj in enumerate(joints):
        j = int(assignment[i])
        hd = HAND_DOFS[j]
        H[j] = (float(hd["min"]) + float(hd["max"])) / 2.0

    A = np.zeros(len(joints))
    for i, aj in enumerate(joints):
        j = int(assignment[i])
        hd = HAND_DOFS[j]
        h_min, h_max = float(hd["min"]), float(hd["max"])
        h_range = h_max - h_min + 1e-8
        h_norm = (H[j] - h_min) / h_range
        A[i] = aj["min_angle"] + h_norm * (aj["max_angle"] - aj["min_angle"])

    return H, A


def optimize_reference_pose_paper(
    assignment: np.ndarray,
    G_sub: np.ndarray,
    P: list[dict],
    C: np.ndarray,
    joints: list[dict],
) -> tuple[np.ndarray, dict, float]:
    """
    논문 Eq. 11 정확한 구현:
      (g*, p*) = argmax_{g∈G, p∈P} Q_M

    Q_M = Σ_{(g', p') ∈ (G_g, P_p)} Q(g', p')

    G_g 도출 방법 (논문 §4.2 설명):
      후보 기준 포즈 (g, p)가 주어지면,
      아바타가 각 p' ∈ P로 이동할 때 런타임 역산으로 필요한 손 포즈 g'를 계산:
        런타임 정방향: a_out = p[i] + (g_curr[j] - g[j]) × scale[i]
        역산:          g'[j] = g[j] + (p'[i] - p[i]) / scale[i]

    → 단순 Q(g,p) 평가(single)와 달리,
      "이 기준 포즈를 쓰면 모든 아바타 동작에 필요한 손 포즈가 얼마나 편안한가"를 평가

    복잡도: O(|G_sub| × |P| × |P|)
      G_sub=100, P=147: 100×147×147 ≈ 2,160,900 Q 계산
      G=10,000, P=147:  10,000×147×147 ≈ 216,090,000 Q 계산 (논문 기준 ~1시간)
    """
    n_pairs = len(G_sub) * len(P)
    print(f"  [Step 2 PAPER] 기준 포즈 탐색: {len(G_sub)} × {len(P)} = {n_pairs}쌍 후보")
    print(f"  [Step 2 PAPER] 후보당 {len(P)}개 역산 → 총 {n_pairs * len(P):,}번 Q 계산")

    # scale_factor[i] = (a_max - a_min) / (h_max - h_min)
    scale_factors = []
    for i, aj in enumerate(joints):
        j  = int(assignment[i])
        hd = HAND_DOFS[j]
        h_range = float(hd["max"]) - float(hd["min"]) + 1e-8
        a_range = float(aj["max_angle"]) - float(aj["min_angle"]) + 1e-8
        scale_factors.append(a_range / h_range)

    best_score = -np.inf
    best_g: np.ndarray | None = None
    best_p: dict | None = None

    for g in G_sub:
        for p_dict in P:
            q_total = 0.0
            for p_prime in P:
                # 역산: 아바타가 p'로 이동할 때 필요한 손 포즈 g' 계산
                g_prime = g.copy()
                for i, aj in enumerate(joints):
                    j = int(assignment[i])
                    p_val       = float(p_dict.get(aj["id"], 0.0))
                    p_prime_val = float(p_prime.get(aj["id"], 0.0))
                    g_prime[j] = g[j] + (p_prime_val - p_val) / scale_factors[i]
                # 손 ROM 범위로 클리핑
                g_prime = np.clip(g_prime, _DOF_MINS, _DOF_MAXS)
                q_total += compute_Q(g_prime, p_prime, assignment, C, joints)

            if q_total > best_score:
                best_score = q_total
                best_g = g.copy()
                best_p = dict(p_dict)

    best_q_single = compute_Q(best_g, best_p, assignment, C, joints)
    q_avg = best_score / len(P)
    print(f"  [Step 2 PAPER] g*의 Q_M 합산/|P|:  {q_avg:+.4f}")
    print(f"  [Step 2 PAPER] g*의 단일 Q(g*,p*): {best_q_single:+.4f}")

    return best_g, best_p, best_q_single  # 비교용으로 단일 Q 반환


def optimize_reference_pose_sum(
    assignment: np.ndarray,
    G_sub: np.ndarray,
    P: list[dict],
    C: np.ndarray,
    joints: list[dict],
) -> tuple[np.ndarray, dict, float]:
    """
    논문 방식: g* = argmax_{g∈G_sub} Σ_{p∈P} Q_M(g, p)
    이후 p* = argmax_{p∈P} Q_M(g*, p)

    현재 구현(argmax 단일 쌍)과의 차이:
      - 단일 쌍: 특정 아바타 포즈 1개에 최적인 g* 선택
      - 합산:   P 전체에서 평균적으로 가장 좋은 g* 선택
               → 어떤 동물 포즈로 이동하든 잘 대응되는 손 기준 포즈

    반환: (g_star, p_star_dict, best_single_Q)
      * 반환 Q score는 (g*, p*)의 단일 Q (기존 방식과 비교 가능하도록)
    """
    n_pairs = len(G_sub) * len(P)
    print(f"  [Step 2 SUM] 기준 포즈 탐색: {len(G_sub)} × {len(P)} = {n_pairs}쌍 순회 중...")

    # Step A: g* = argmax Σ_p Q
    best_sum = -np.inf
    best_g: np.ndarray | None = None

    for g in G_sub:
        q_sum = sum(compute_Q(g, p_dict, assignment, C, joints) for p_dict in P)
        if q_sum > best_sum:
            best_sum = q_sum
            best_g = g.copy()

    # Step B: p* = argmax_p Q(g*, p)
    best_p_score = -np.inf
    best_p: dict | None = None
    for p_dict in P:
        score = compute_Q(best_g, p_dict, assignment, C, joints)
        if score > best_p_score:
            best_p_score = score
            best_p = dict(p_dict)

    # 기존 단일 argmax 방식과 비교 출력
    single_best = -np.inf
    for g in G_sub:
        for p_dict in P:
            s = compute_Q(g, p_dict, assignment, C, joints)
            if s > single_best:
                single_best = s

    q_avg = best_sum / len(P)
    print(f"  [Step 2 SUM] g* 평균 Q (Σ/|P|): {q_avg:+.4f}")
    print(f"  [Step 2 SUM] g*의 최적 p* Q:    {best_p_score:+.4f}")
    print(f"  [Step 2 단일] argmax (g,p) Q:    {single_best:+.4f}")

    return best_g, best_p, best_p_score


# ──────────────────────────────────────────────────────────────
# 결과 JSON 구조 생성
# ──────────────────────────────────────────────────────────────

def build_result(
    assignment: np.ndarray,
    g_star: np.ndarray,
    p_star: dict,
    q_best: float,
    C: np.ndarray,
    S_bar: np.ndarray,
    F_bar: np.ndarray,
    joints: list[dict],
    animal_name: str,
) -> dict:
    """
    최적화 결과를 매핑 JSON 구조로 변환.

    ref_H[j]  = g*[j]
        → Step 2 argmax에서 선택된 최적 기준 손 포즈의 DOF 값
    ref_A[i]  = p*[i]
        → Step 2 argmax에서 선택된 최적 기준 아바타 포즈의 관절 각도를 그대로 사용
        → 아바타의 실제 대표 포즈(e.g. snake는 펴진 자세, spider는 보행 자세)를
          기준점으로 삼아 자연스러운 제어 기준 확보
    scale_factor = (a_max − a_min) / (h_max − h_min)  (변경 없음)
    """
    mapping: dict[str, dict] = {}
    for i, aj in enumerate(joints):
        j = int(assignment[i])
        hd = HAND_DOFS[j]
        a_range = aj["max_angle"] - aj["min_angle"] + 1e-8
        h_range = float(hd["max"]) - float(hd["min"]) + 1e-8
        scale   = a_range / h_range
        q_val   = float(W_S * (-S_bar[i, j]) + W_C * C[i, j] + W_F * F_bar[j])
        mapping[aj["id"]] = {
            "hand_dof_idx":  j,
            "hand_dof_name": hd["name"],
            "scale_factor":  round(scale, 4),
            "Q_score":       round(q_val, 4),
        }

    # ref_H: Step 2 최적 손 포즈 g* 값 그대로 사용
    # 매핑된 DOF는 g*[j], 나머지는 rest 값
    ref_H: dict[str, float] = {hd["name"]: float(hd["rest"]) for hd in HAND_DOFS}
    for i, aj in enumerate(joints):
        j = int(assignment[i])
        hd = HAND_DOFS[j]
        ref_H[hd["name"]] = round(float(g_star[j]), 2)

    # ref_A: Step 2 argmax에서 선택된 최적 아바타 포즈 p* 값 그대로 사용
    # 아바타 고유 특성(snake=펴진 자세, spider=보행 자세 등)을 기준점에 반영
    ref_A: dict[str, float] = {}
    for i, aj in enumerate(joints):
        ref_A[aj["id"]] = round(float(p_star.get(aj["id"], 0.0)), 2)

    return {
        "animal":              animal_name,
        "mapping":             mapping,
        "reference_pose_H":    ref_H,
        "reference_pose_A":    ref_A,
        "Q_score_reference":   round(q_best, 4),
    }
