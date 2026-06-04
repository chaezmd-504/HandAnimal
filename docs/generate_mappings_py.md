# generate_mappings.py — 동물 매핑 자동 생성 스크립트

## 개요

`data/animal_skeletons/`를 스캔해 skeleton JSON이 있는 동물을 자동 탐색하고,  
각 동물에 대해 최적화 매핑(`{animal}_mapping.json`)을 생성한다.

---

## 실행

```bash
conda activate capstone_env
python scripts/generate_mappings.py               # 전체 동물
python scripts/generate_mappings.py --animal spider   # 특정 동물만
```

---

## 실행 순서 (전체 파이프라인)

```
1. python scripts/generate_hand_poses.py        → poses_10k.npy
2. python scripts/subsample_hand_poses.py       → poses_1000_comfortable.npy
3. Unity AnimPoseExporter 실행                  → skeleton.json + {animal}_poses.json
4. python scripts/auto_chains.py --animal spider → chains 추가
5. python scripts/generate_mappings.py          → {animal}_mapping.json  ← 이 스크립트
6. Unity AnimalControllerEditor 버튼 클릭       → Inspector 갱신
```

---

## BETA_MAP

```python
BETA_MAP = {
    "spider":     5.0,
    "butterfly":  0.0,
    "fish":       0.0,
}
```

β는 손 DOF의 분산 페널티 강도 (논문의 선택 파라미터):
- β=0.0: 논문 원래 수식. wrist 포함 모든 DOF 공평하게 경쟁
- β=5.0: 잘 안 쓰이는 DOF(wrist 등)에 추가 페널티 → 다리 제어에 손가락 DOF 우선 배정

spider는 다리가 8개로 많아 wrist가 다리에 배정되는 것을 막기 위해 β=5.0.

---

## EXCLUDE_EXTRA (신규 추가)

```python
EXCLUDE_EXTRA = {
    "spider": {
        # IK 타겟 본 (역운동학 타겟, 손가락으로 직접 제어하면 안 됨)
        "l_leg_ik_005", "l_leg_ik_008", "l_leg_ik_011", "l_legik_015",
        "r_leg_ik_005", "r_leg_ik_008", "r_leg_ik_011",
        # 보조 뼈 (몸통 세그먼트)
        "ass", "bone_002", "l_bone_014", "r_bone_014",
    },
}
```

### 왜 필요한가?

이 관절들이 메인 매핑에 포함되면:
- `bone` → `wrist_rot`에 매핑됨 → LDA 분리도가 음수(-0.31)
- `atack1` → `thumb_abd`에 매핑됨 → 손가락을 가만히 있어도 스코어가 임계값 초과
- → **Attack 애니메이션이 계속 발동되는 버그**

EXCLUDE_EXTRA에 등록하면 `MappingOptimizer` 최적화 전에 미리 제거.

---

## body_mapping 자동 exclude

```python
body_map_path = os.path.join(MAPPINGS_DIR, f"{animal}_body_mapping.json")
exclude = set(EXCLUDE_EXTRA.get(animal, set()))

if os.path.exists(body_map_path):
    with open(body_map_path) as _f:
        _bm = json.load(_f)
    exclude |= set(_bm.get("mapping", {}).keys())
    # → {"bone", "bodyik_001", "atack1"} 추가
```

`spider_body_mapping.json`이 있으면 거기 등록된 관절은 자동으로 메인 매핑에서 제외.  
body_mapping에서 이미 따로 제어하므로 중복 배정 방지.

---

## 관절 제외 적용

```python
if exclude:
    opt.joints = [j for j in opt.joints if j["id"] not in exclude]
    opt.n_animal = len(opt.joints)
    opt._joint_idx = {j["id"]: i for i, j in enumerate(opt.joints)}
    print(f"  제외 관절 ({len(exclude)}개): {sorted(exclude)}")
```

`MappingOptimizer` 생성 후 monkey-patch로 joints 목록을 교체.  
optimizer 내부 인덱스(`_joint_idx`)도 함께 갱신해야 최적화 시 범위 오류가 나지 않는다.

---

## _discover_animals()

```python
def _discover_animals() -> dict[str, float]:
    for fname in os.listdir(SKELETONS_DIR):
        if not fname.endswith(".json"): continue
        if fname.startswith("bone_map_"): continue     # bone_map 제외
        if fname.endswith("_poses.json"): continue      # poses 제외
        name = fname[:-5]
        animals[name] = BETA_MAP.get(name, 0.0)        # BETA_MAP 없으면 0.0
    return animals
```

`data/animal_skeletons/` 폴더에 `spider.json`이 있으면 자동 탐지.  
새 동물 추가 시 skeleton.json만 넣으면 자동으로 처리됨.

---

## MappingOptimizer 호출

```python
opt = MappingOptimizer(skeleton_path, POSES_10K)

# bilateral 여부는 skeleton.json에서 읽음
bilateral = skeleton_data.get("bilateral", True)

if bilateral:
    result = opt.run_bilateral(
        avatar_poses_path=poses_arg,   # spider_poses.json
        poses_sub_path=sub_arg,        # poses_1000_comfortable.npy
        beta=beta,
    )
else:
    result = opt.run(...)

opt.save(result, out_path)
```

bilateral=True (spider): 양손을 동시에 사용해 매핑 최적화.  
각 동물 관절을 어느 손의 어느 DOF에 배정할지 ILP(정수 선형 계획)로 최적화.

---

## 출력 파일 구조 (spider_mapping.json)

```json
{
  "animal": "spider",
  "mode": "bilateral",
  "mapping": {
    "l_leg": {
      "hand_dof_idx": 7,
      "hand_dof_name": "index_mcp",
      "scale_factor": 1.95,
      "Q_score": 8.2,
      "hand": "left"
    },
    ...
  },
  "reference_pose_H": {
    "left":  {"wrist_flex": 12.3, "index_mcp": -5.1, ...},
    "right": {"wrist_flex": 11.8, ...}
  },
  "Q_score_reference": 9.45
}
```

- `scale_factor`: 손 DOF 변화량 → 관절 각도 변환 계수
- `Q_score`: 이 매핑의 품질 점수 (높을수록 좋음)
- `reference_pose_H`: 캘리브레이션 기준 손 포즈 (이 포즈 = 동물 기본 자세)

---

## 실행 예시 출력

```
[자동 탐색] 3개 동물: ['spider', 'fish', 'butterfly']

=======================================================
 SPIDER 매핑 생성
=======================================================
  모드: bilateral (양손)  beta=5.0
  제외 관절 (11개): ['ass', 'atack1', 'bodyik_001', 'bone', 'bone_002',
                     'l_bone_014', 'l_leg_ik_005', ...]

  최적화 완료: Q_score=9.2
  저장: data/mappings/spider_mapping.json

[완료] 모든 동물 매핑 생성이 끝났습니다.
```
