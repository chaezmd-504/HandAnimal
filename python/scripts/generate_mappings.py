"""
generate_mappings.py
---------------------
BETA_MAP 에 등록된 동물 전체에 대해 매핑 최적화를 실행하고
data/mappings/<animal>_mapping.json 을 생성한다.

실행 순서:
  1. python scripts/generate_hand_poses.py       # poses_10k.npy
  2. python scripts/subsample_hand_poses.py      # poses_100_comfortable.npy
  3. Unity: HandAvatar > Export Animation Poses  # skeleton.json + {animal}_poses.json
  4. python scripts/generate_mappings.py         # {animal}_mapping.json

실행:
    conda activate capstone_env
    pip install pulp        (최초 1회)
    python scripts/generate_mappings.py
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PYTHON_DIR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from mapping.mapping_optimizer import MappingOptimizer

# ── bilateral/unilateral ────────────────────────────────────────────────────
# 논문 설계상 양손/한손 여부는 디자이너가 지정하는 입력값이다.
# AnimPoseExporter 에서 "양손 매핑 (Bilateral)" 체크박스로 설정하면
# skeleton.json 의 "bilateral" 필드에 저장되어 이 스크립트에서 자동으로 읽힌다.
#
# ── beta ────────────────────────────────────────────────────────────────────
# S̄ 분산 페널티 강도 (논문에 없는 선택 항목).
#   0.0  → 논문 원래 수식. 모든 DOF(손목 포함) 공평하게 경쟁.
#   5.0  → 손목처럼 G_sub 에서 잘 안 쓰이는 DOF 에 추가 페널티.
#          다리가 많은 동물에서 wrist→leg 배정을 줄이고 싶을 때.
#   동물에 따라 wrist 를 머리/코 등 제어에 쓰려면 0.0 권장.
#
# beta 기본값 오버라이드 (등록 안 된 동물은 0.0 사용)
# 새 동물을 추가하면 자동으로 감지되므로 등록 안 해도 무방.
# 손목 DOF 를 다리에 배정하지 않으려면 beta=5.0 권장.
BETA_MAP = {
    # animal      beta
    "spider":     5.0,
    "butterfly":  0.0,
    "fish":       0.0,
}

SKELETONS_DIR = os.path.join(PYTHON_DIR, "data", "animal_skeletons")
POSES_10K     = os.path.join(PYTHON_DIR, "data", "hand_poses", "poses_10k.npy")
POSES_SUB     = os.path.join(PYTHON_DIR, "data", "hand_poses", "poses_100_comfortable.npy")
MAPPINGS_DIR  = os.path.join(PYTHON_DIR, "data", "mappings")


def _discover_animals() -> dict[str, float]:
    """
    data/animal_skeletons/ 에서 *.json 을 자동 탐색하고
    BETA_MAP 오버라이드를 적용한 {animal: beta} dict 반환.
    bone_map_*.json 과 *_poses.json 은 제외.
    """
    animals: dict[str, float] = {}
    if not os.path.isdir(SKELETONS_DIR):
        return animals
    for fname in os.listdir(SKELETONS_DIR):
        if not fname.endswith(".json"):
            continue
        if fname.startswith("bone_map_") or fname.endswith("_poses.json"):
            continue
        name = fname[:-5]  # strip .json
        animals[name] = BETA_MAP.get(name, 0.0)
    return animals


def main():
    # ── 사전 조건 확인 ──────────────────────────────────────
    if not os.path.exists(POSES_10K):
        print(f"[WARN] 포즈 파일 없음: {POSES_10K}")
        print("       generate_hand_poses.py 를 먼저 실행하세요. 합성 데이터로 대체합니다.")

    if not os.path.exists(POSES_SUB):
        print(f"[INFO] G_sub 없음: {POSES_SUB}")
        print("       subsample_hand_poses.py 를 먼저 실행하거나,")
        print("       이번 실행 중 on-the-fly 서브샘플링으로 대체합니다.")

    discovered = _discover_animals()
    if not discovered:
        print("[ERROR] data/animal_skeletons/ 에 skeleton JSON 이 없습니다.")
        print("        Unity HandAvatar > Export Animation Poses 를 먼저 실행하세요.")
        return

    print(f"[자동 탐색] {len(discovered)}개 동물: {list(discovered.keys())}")
    for animal, beta in discovered.items():
        skeleton_path     = os.path.join(SKELETONS_DIR, f"{animal}.json")
        avatar_poses_path = os.path.join(SKELETONS_DIR, f"{animal}_poses.json")
        out_path          = os.path.join(MAPPINGS_DIR,  f"{animal}_mapping.json")

        if not os.path.exists(skeleton_path):
            print(f"[SKIP] 골격 파일 없음: {skeleton_path}")
            continue

        # bilateral 여부는 skeleton.json 에서 읽음 (AnimPoseExporter 에서 디자이너가 지정)
        with open(skeleton_path, encoding="utf-8") as f:
            skeleton_data = json.load(f)
        bilateral = skeleton_data.get("bilateral", True)

        if not os.path.exists(avatar_poses_path):
            print(f"[WARN] 아바타 포즈 없음: {avatar_poses_path}")
            print("       AnimPoseExporter (Unity) 를 먼저 실행하세요.")
            print("       ROM 기반 플레이스홀더 포즈로 대체 진행합니다.")

        print(f"\n{'='*55}")
        print(f" {animal.upper()} 매핑 생성")
        print(f"{'='*55}")
        print(f"  모드: {'bilateral (양손)' if bilateral else 'unilateral (한 손)'}  beta={beta}")

        try:
            opt = MappingOptimizer(skeleton_path, POSES_10K)

            poses_arg = avatar_poses_path if os.path.exists(avatar_poses_path) else None
            sub_arg   = POSES_SUB if os.path.exists(POSES_SUB) else None

            if bilateral:
                result = opt.run_bilateral(
                    avatar_poses_path=poses_arg,
                    poses_sub_path=sub_arg,
                    beta=beta,
                )
            else:
                result = opt.run(
                    avatar_poses_path=poses_arg,
                    poses_sub_path=sub_arg,
                    beta=beta,
                )
            opt.save(result, out_path)

        except Exception as e:
            print(f"[ERROR] {animal}: {e}")
            import traceback; traceback.print_exc()

    print("\n[완료] 모든 동물 매핑 생성이 끝났습니다.")
    print(f"       결과 폴더: {MAPPINGS_DIR}")


if __name__ == "__main__":
    main()
