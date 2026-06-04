# keyframe_engine.py — 키프레임 블렌딩 매핑 엔진

## 개요

`MappingEngine`(direct)의 떨림(wobble) 문제를 해결하기 위한 대체 엔진.  
동물 애니메이션 포즈들을 키프레임으로 사용하고, 현재 손 포즈와의 거리를 기반으로 softmax 블렌딩한다.  
`main.py`의 blend 모드에서 Sequential 재생(get_sequential_pose)도 담당한다.

---

## 핵심 원리

### 오프라인 준비 (set_animal 호출 시 1회)
각 동물 키프레임 포즈 `P_i`에 대해 **역변환**으로 손 트리거 포즈 `G_i`를 계산:

```
원래 변환:  a = a_ref + (h - h_ref) * scale
역변환:     h = h_ref + (a - a_ref) / scale
```

즉 "이 동물 포즈가 나오려면 손이 어떤 포즈여야 하는가"를 저장.

### 런타임 (transform_bilateral 호출 시)
```
1. 현재 손 포즈 h
2. 각 G_i와 L2 거리 계산: d_i = ||h - G_i||
3. softmax 가중치: w_i = softmax(-d_i * temperature)
4. 최종 동물 포즈: Σ w_i * P_i
```

`temperature`가 높을수록 가장 가까운 키프레임 하나로 snap, 낮을수록 여러 키프레임이 부드럽게 블렌딩.

---

## 클래스 구조

```python
class KeyframeMappingEngine:
    mappings_dir     # {animal}_mapping.json 폴더
    poses_dir        # {animal}_poses.json, bone_map_{animal}.json 폴더
    temperature      # softmax 온도
    _cache           # {animal: {animal_poses, triggers, anim_groups, ...}}
    _bone_axes       # {animal: {joint_id: ("x"/"y"/"z", sign)}}
    current_animal   # 현재 로드된 동물
```

---

## set_animal() 흐름

```python
engine.set_animal("spider")
```

1. `spider_mapping.json` 로드 → `mapping_data` (reference_pose_H, scale_factors 등)
2. `spider_poses.json` 로드 → `animal_poses` (각 프레임의 관절 값들)
3. `_build_keyframes()` 호출:
   - 각 포즈에 대해 `_compute_trigger()` → 손 트리거 포즈 G_i 계산
   - `anim_groups` 구성: `{"Attack1": [(trigger, pose_idx), ...], "Death": [...]}`
4. `bone_map_spider.json` 로드 → `_bone_axes`

---

## _build_keyframes() 내부

```python
def _build_keyframes(self, animal):
    # spider_poses.json에서 각 프레임 로드
    for pose in animal_poses:
        trigger = self._compute_trigger(pose, mapping_data, mode, mapping)
        # trigger = {"left": np.array([...20 DOF...]), "right": np.array([...])}
    
    # _anim, _frame 메타데이터로 그룹화
    anim_groups = {"Attack1": [...], "Death": [...], ...}
```

### _compute_trigger() 역변환
```python
for jid, map_entry in mapping.items():
    a_val = pose.get(jid, 0.0)          # 동물 관절 값 (°)
    scale = map_entry["scale_factor"]
    dof_idx = map_entry["hand_dof_idx"]
    hand = map_entry["hand"]            # "left" or "right"
    
    # 역변환: h = h_ref + (a - a_ref) / scale
    h_trigger[hand][dof_idx] = h_ref[hand][dof_idx] + (a_val - a_ref) / scale
```

---

## transform_bilateral() — 런타임 블렌딩

```python
result = engine.transform_bilateral(hands_angles)
# → {"l_leg": {"x":0, "y":0, "z":-32.5}, "r_leg": {...}, ...}
```

내부 흐름:
```python
# 1. 현재 손 포즈 벡터화
h_left  = _dof_dict_to_vec(hands_angles["left"])   # shape (20,)
h_right = _dof_dict_to_vec(hands_angles["right"])

# 2. 각 키프레임과 거리 계산
distances = [_distance(h_left, h_right, trig, mode, l_idx, r_idx) for trig in triggers]

# 3. softmax 가중치
weights = softmax(-np.array(distances) * temperature)

# 4. 가중 평균
for jid in all_joints:
    blended_val = sum(w * pose[jid] for w, pose in zip(weights, animal_poses))
```

---

## get_sequential_pose() — Sequential 재생 (blend 모드용)

```python
joints = engine.get_sequential_pose("Death", cursor=5.3)
# cursor=5.3 → frame5와 frame6 사이를 t=0.3으로 선형 보간
```

### cursor 보간
```python
lo_idx = int(cursor)          # 5
hi_idx = lo_idx + 1           # 6
t      = cursor - lo_idx      # 0.3

# 두 프레임 포즈 로드
pose_lo = animal_poses[group[lo_idx][1]]
pose_hi = animal_poses[group[hi_idx][1]]
```

### to_xyz() — scalar → xyz 변환
`spider_poses.json`의 값은 1D scalar(각도°). `bone_map_spider.json`의 axis 정보로 올바른 축에 배치:

```python
def to_xyz(pose, jid):
    v = pose.get(jid, 0.0)
    ax, sign = axes.get(jid, ("z", 1))   # bone_map에서 로드
    fv = float(v) * sign
    if ax == "x": return (fv, 0.0, 0.0)
    elif ax == "y": return (0.0, fv, 0.0)
    else:          return (0.0, 0.0, fv)
```

### _short() — Euler 최단경로 보간

Death 애니메이션에서 `bone.z = 179 → -175` 처럼 ±180° 경계를 넘을 때 반대 방향으로 빙글 도는 버그를 방지:

```python
def _short(a: float, b: float) -> float:
    d = (b - a + 180.0) % 360.0 - 180.0   # 항상 [-180, 180] 범위의 차이
    return a + d * t
```

예시:
- a=179, b=-175 → d=(−175−179+180)%360−180 = (−174)%360−180 = 186−180 = 6
- 즉 179 → 179+6*t (6° 앞으로 이동) ✓
- 단순 보간했다면: 179 → −175 (354° 역방향 회전) ✗

---

## _distance() — 손-키프레임 거리 계산

```python
def _distance(h_left, h_right, trigger, mode, left_idx=None, right_idx=None):
    # left_idx/right_idx: 비교에 사용할 DOF 인덱스 (매핑된 DOF만 비교)
    if mode == "bilateral":
        lh = h_left[left_idx]    if left_idx  is not None else h_left
        lt = trigger["left"][left_idx]  ...
        return norm(lh - lt) + norm(rh - rt)
    else:
        return norm(rh - rt)
```

`left_idx`, `right_idx`는 `{animal}_triggers_lda.json`에서 로드된 "매핑에 사용된 DOF 인덱스"로, 관련 없는 DOF를 거리 계산에서 제외해 노이즈를 줄인다.

---

## _load_bone_axes()

```python
def _load_bone_axes(poses_dir, animal):
    # bone_map_spider.json 읽기
    # joint_map의 각 joint에서 "axis" 필드 추출
    return {jid: (info["axis"].lower(), 1) for jid, info in joint_map.items()}
```

반환값: `{"bone": ("x", 1), "l_leg": ("z", 1), ...}`

---

## anim_groups 구조

```python
anim_groups = {
    "Attack1": [
        (trigger_0, pose_idx_0),   # (numpy trigger, spider_poses.json 인덱스)
        (trigger_1, pose_idx_1),
        ...
    ],
    "Death": [...],
    "Walk": [...],
}
```

- `trigger`: 이 포즈가 나오려면 손이 있어야 할 위치 (역변환된 손 벡터)
- `pose_idx`: `animal_poses` 리스트에서의 인덱스

---

## transform_clamped() — MappingEngine 호환 메서드

`main.py`에서 `MappingEngine`과 동일한 API로 호출 가능하도록 래핑:

```python
def transform_clamped(self, hands_angles, skeleton=None):
    result = self.transform_bilateral(hands_angles)
    # keyframe 결과는 이미 xyz dict이므로 추가 클리핑 불필요
    return result
```

---

## 데이터 의존성

```
spider_mapping.json          → mapping, reference_pose_H, scale_factors
spider_poses.json            → animal_poses (각 애니메이션 프레임 데이터)
bone_map_spider.json         → 각 관절의 Unity 축 (x/y/z)
spider_triggers_lda.json     → LDA 트리거 (main.py에서 직접 로드)
```
