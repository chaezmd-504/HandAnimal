"""
generate_mappings.py
---------------------
5종 동물 전체에 대해 매핑 최적화를 실행하고
data/mappings/<animal>_mapping.json 을 생성한다.

실행:
    conda activate capstone_env
    pip install pulp        (최초 1회)
    python scripts/generate_mappings.py

사전 조건:
    python scripts/generate_hand_poses.py  를 먼저 실행해 두어야 한다.
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PYTHON_DIR)

from mapping.mapping_optimizer import MappingOptimizer

ANIMALS = ["spider", "butterfly", "fish", "octopus", "snake"]

SKELETONS_DIR = os.path.join(PYTHON_DIR, "data", "animal_skeletons")
POSES_PATH    = os.path.join(PYTHON_DIR, "data", "hand_poses", "poses_10k.npy")
MAPPINGS_DIR  = os.path.join(PYTHON_DIR, "data", "mappings")


def main():
    if not os.path.exists(POSES_PATH):
        print(f"[WARN] 포즈 파일 없음: {POSES_PATH}")
        print("       generate_hand_poses.py 를 먼저 실행하세요. 합성 데이터로 대체합니다.")

    for animal in ANIMALS:
        skeleton_path = os.path.join(SKELETONS_DIR, f"{animal}.json")
        out_path      = os.path.join(MAPPINGS_DIR,  f"{animal}_mapping.json")

        if not os.path.exists(skeleton_path):
            print(f"[SKIP] 골격 파일 없음: {skeleton_path}")
            continue

        print(f"\n{'='*50}")
        print(f" {animal.upper()} 매핑 생성")
        print(f"{'='*50}")

        try:
            opt = MappingOptimizer(skeleton_path, POSES_PATH)
            opt.unit_test()
            # 양손 매핑 사용 (왼손/오른손 관절 자동 분리 + 균형 제약)
            result = opt.run_bilateral()
            opt.save(result, out_path)
        except Exception as e:
            print(f"[ERROR] {animal}: {e}")
            import traceback; traceback.print_exc()

    print("\n[완료] 모든 동물 매핑 생성이 끝났습니다.")
    print(f"       결과 폴더: {MAPPINGS_DIR}")


if __name__ == "__main__":
    main()
