"""
lda_triggers.py
---------------
Idle vs 각 action 애니메이션을 구분하는 LDA 판별 방향을 자동 계산.

원리:
  동물 포즈 → 역변환 → 손 DOF shift 벡터 (캘리브레이션 불변)
  LDA: Idle 클러스터 vs Action 클러스터 사이 최대 분리 방향 찾기
  → 런타임: score = w · (h_current - ref_H) > threshold → action 발동

실행:
  python mapping/lda_triggers.py --animal spider
  python mapping/lda_triggers.py --animal spider --action-anims Attack1,Attack2,Death
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_DIR))

_HAND_DOF_NAMES = [
    "wrist_flex", "wrist_dev", "wrist_rot",
    "thumb_cmc", "thumb_abd", "thumb_mcp", "thumb_ip",
    "index_mcp", "index_pip", "index_dip",
    "middle_mcp", "middle_pip", "middle_dip",
    "ring_mcp",  "ring_pip",  "ring_dip",
    "pinky_cmc", "pinky_mcp", "pinky_pip", "pinky_dip",
]
_DOF_IDX = {name: i for i, name in enumerate(_HAND_DOF_NAMES)}


def _shift_vecs(pose: dict, mapping: dict, ref_A: dict) -> tuple[np.ndarray, np.ndarray]:
    """동물 포즈 1프레임 → 좌/우 손 shift 벡터 (각 20-DOF)."""
    vl = np.zeros(len(_HAND_DOF_NAMES))
    vr = np.zeros(len(_HAND_DOF_NAMES))
    for jid, info in mapping.items():
        a_ref = float(ref_A.get(jid, 0.0))
        raw   = pose.get(jid, a_ref)
        a_val = max(raw.values(), key=abs) if isinstance(raw, dict) else float(raw)
        scale = float(info["scale_factor"])
        dof   = info["hand_dof_name"]
        if abs(scale) < 1e-6 or dof not in _DOF_IDX:
            continue
        shift = (a_val - a_ref) / scale
        idx   = _DOF_IDX[dof]
        if info.get("hand", "right") == "left":
            vl[idx] = shift
        else:
            vr[idx] = shift
    return vl, vr


def _lda(X_neg: np.ndarray, X_pos: np.ndarray, reg_ratio: float = 0.05):
    """
    2-class LDA.
    반환: w (판별 방향), threshold, proj_neg_mean, proj_pos_mean
    """
    mu_neg = X_neg.mean(axis=0)
    mu_pos = X_pos.mean(axis=0)

    def _cov(X):
        if len(X) < 2:
            return np.eye(X.shape[1])
        return np.cov(X.T, bias=False)

    S_w = _cov(X_neg) + _cov(X_pos)
    # Tikhonov 정규화
    reg = reg_ratio * np.trace(S_w) / max(S_w.shape[0], 1)
    S_w += reg * np.eye(S_w.shape[0])

    try:
        w = np.linalg.solve(S_w, mu_pos - mu_neg)
    except np.linalg.LinAlgError:
        w = mu_pos - mu_neg

    norm = np.linalg.norm(w)
    if norm > 1e-8:
        w /= norm

    p_neg = float(w @ mu_neg)
    p_pos = float(w @ mu_pos)
    threshold = (p_neg + p_pos) / 2.0
    return w, threshold, p_neg, p_pos


def generate(animal: str, action_anims: list[str],
             mappings_dir: str, poses_dir: str) -> dict:
    # ── 로드 ──────────────────────────────────────────────────────
    with open(os.path.join(mappings_dir, f"{animal}_mapping.json"), encoding="utf-8") as f:
        mdata = json.load(f)
    with open(os.path.join(poses_dir, f"{animal}_poses.json"), encoding="utf-8") as f:
        poses = json.load(f)

    mapping = mdata["mapping"]
    ref_A   = mdata["reference_pose_A"]

    # 매핑된 DOF 인덱스
    left_idx  = sorted({_DOF_IDX[info["hand_dof_name"]] for info in mapping.values()
                         if info.get("hand", "right") == "left"
                         and info["hand_dof_name"] in _DOF_IDX})
    right_idx = sorted({_DOF_IDX[info["hand_dof_name"]] for info in mapping.values()
                         if info.get("hand", "right") == "right"
                         and info["hand_dof_name"] in _DOF_IDX})

    if not left_idx:
        left_idx = list(range(len(_HAND_DOF_NAMES)))
    if not right_idx:
        right_idx = list(range(len(_HAND_DOF_NAMES)))

    l_idx = np.array(left_idx,  dtype=int)
    r_idx = np.array(right_idx, dtype=int)

    # ── Idle shift 행렬 ───────────────────────────────────────────
    idle_frames = [p for p in poses if p.get("_anim") == "Idle"]
    if not idle_frames:
        raise ValueError("Idle 프레임이 없습니다. _anim 메타데이터를 확인하세요.")
    print(f"[{animal}] Idle {len(idle_frames)}프레임")

    idle_vecs = np.array([
        np.concatenate([_shift_vecs(p, mapping, ref_A)[0][l_idx],
                        _shift_vecs(p, mapping, ref_A)[1][r_idx]])
        for p in idle_frames
    ])  # (N_idle, D)  D = len(l_idx)+len(r_idx)

    # ── 각 action anim LDA ────────────────────────────────────────
    result = {
        "animal":           animal,
        "left_dof_indices":  left_idx,
        "right_dof_indices": right_idx,
        "left_dof_names":   [_HAND_DOF_NAMES[i] for i in left_idx],
        "right_dof_names":  [_HAND_DOF_NAMES[i] for i in right_idx],
        "triggers":         {},
    }

    for anim in action_anims:
        anim_frames = [p for p in poses if p.get("_anim") == anim]
        if not anim_frames:
            print(f"  [WARN] {anim} 프레임 없음, 스킵")
            continue

        action_vecs = np.array([
            np.concatenate([_shift_vecs(p, mapping, ref_A)[0][l_idx],
                            _shift_vecs(p, mapping, ref_A)[1][r_idx]])
            for p in anim_frames
        ])  # (N_action, D)

        w, thr, p_idle, p_action = _lda(idle_vecs, action_vecs)
        sep = abs(p_action - p_idle)
        direction = "above" if p_action > p_idle else "below"

        print(f"  {anim}: separation={sep:.2f}  idle_proj={p_idle:.2f}"
              f"  action_proj={p_action:.2f}  dir={direction}")

        # 중요 DOF 출력
        dof_labels = ([f"L:{_HAND_DOF_NAMES[i]}" for i in left_idx] +
                      [f"R:{_HAND_DOF_NAMES[i]}" for i in right_idx])
        top = sorted(zip(np.abs(w).tolist(), dof_labels), reverse=True)[:4]
        print(f"    top DOFs: {[(f'{v:.3f}', n) for v, n in top]}")

        n_l = len(left_idx)
        result["triggers"][anim] = {
            "w_left":       w[:n_l].tolist(),
            "w_right":      w[n_l:].tolist(),
            "threshold":    float(thr),
            "direction":    direction,
            "separation":   float(sep),
            "proj_idle":    float(p_idle),
            "proj_action":  float(p_action),
        }

    # ── 저장 ──────────────────────────────────────────────────────
    out_path = os.path.join(mappings_dir, f"{animal}_triggers_lda.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"  → 저장: {out_path}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--animal",       required=True)
    parser.add_argument("--action-anims", default="Attack1,Attack2,Death")
    parser.add_argument("--data-dir",     default=None)
    args = parser.parse_args()

    base = args.data_dir or os.path.join(os.path.dirname(_DIR), "data")
    generate(
        animal       = args.animal,
        action_anims = [a.strip() for a in args.action_anims.split(",")],
        mappings_dir = os.path.join(base, "mappings"),
        poses_dir    = os.path.join(base, "animal_skeletons"),
    )


if __name__ == "__main__":
    main()
