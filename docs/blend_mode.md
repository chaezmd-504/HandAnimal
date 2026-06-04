# Blend 모드 — 완전 상세 문서

> **대상**: 발표용. 수식 + 단계별 설명 + 이해를 돕는 해설을 함께 제공.

---

## 목차

1. [Blend 모드란?](#1-blend-모드란)
2. [전체 구조 개요](#2-전체-구조-개요)
3. [두 엔진: Direct vs Sequential](#3-두-엔진-direct-vs-sequential)
4. [캘리브레이션 (양쪽 엔진 동기화)](#4-캘리브레이션-양쪽-엔진-동기화)
5. [런타임 흐름 — 단계별 상세](#5-런타임-흐름--단계별-상세)
   - [Step 1: Direct 관절 각도 계산](#step-1-direct-관절-각도-계산)
   - [Step 2: 데드존 적용](#step-2-데드존-적용)
   - [Step 3: 포즈 거리 트리거 판단](#step-3-포즈-거리-트리거-판단)
   - [Step 4: Sequential 키프레임 보간](#step-4-sequential-키프레임-보간)
   - [Step 5: Alpha 블렌딩](#step-5-alpha-블렌딩)
   - [Step 6: Body 관절 처리](#step-6-body-관절-처리)
   - [Step 7: Unity WebSocket 전송](#step-7-unity-websocket-전송)
6. [상태머신 상세](#6-상태머신-상세)
7. [Alpha 커브 수식](#7-alpha-커브-수식)
8. [KeyframeMappingEngine 내부 구조](#8-keyframemappingengine-내부-구조)
9. [MappingEngine (Direct) 내부 구조](#9-mappingengine-direct-내부-구조)
10. [파라미터 목록](#10-파라미터-목록)

---

## 1. Blend 모드란?

### 핵심 아이디어

손 동작으로 동물을 조종할 때 두 가지 요구사항이 충돌한다:

| 요구사항 | 설명 |
|---------|------|
| **연속성 (Continuous)** | 손가락 굽힘 → 다리가 즉시 따라 움직여야 함 (반응성) |
| **특수 동작 (Action)** | 주먹 쥐기, 손목 비틀기 → 미리 디자인된 애니메이션이 부드럽게 재생돼야 함 |

**Blend 모드**는 두 가지를 동시에 처리한다:
- 평소에는 **Direct 엔진**이 연속적으로 관절을 제어
- 특정 손 제스처가 감지되면 **Sequential 엔진**의 키프레임 애니메이션이 `alpha` 값에 따라 부드럽게 섞임(blend)

```
α = 0   → joints = joints_direct   (연속 모드, 손 제어 100%)
α = 1   → joints = joints_keyframe (애니메이션 100%)
0<α<1   → 두 결과의 가중 혼합
```

---

## 2. 전체 구조 개요

```
웹캠 프레임
    │
    ▼ MediaPipe HandLandmarker
손 랜드마크 (21점 × 양손)
    │
    ▼ compute_dof_angles() + EMA 스무딩
hands_angles = { "left": {dof: val}, "right": {dof: val} }
  각 손 20개 DOF (wrist_flex, wrist_dev, ..., pinky_dip)
    │
    ├──────────────────────────────────────────────────────────┐
    ▼ MappingEngine.transform_clamped()                       │
joints_d (direct 결과)                                        │
  {"l_leg": {"x":0,"y":0,"z":-32.5}, "r_leg":{"z":28.1}...} │
    │                                                          │
    ▼ 3° 데드존 필터                                           │
    │                                                          │
    ▼ 포즈 거리 트리거 체크 / wrist_rot 체크                         │
    │   ↓ 발동                                                 │
    │   KeyframeMappingEngine.get_sequential_pose(anim, cursor)│
    │   joints_kf = 키프레임 선형 보간 결과                    │
    │   alpha = sin curve (0→1→0 또는 0→1 유지)               │
    │   joints = (1-α)×joints_d + α×joints_kf                 │
    │                                                          │
    └────────────── joints_d (α=0, 연속 상태) ────────────────┘
    │
    ▼ body joints 명시 전송 (bone, bodyik_001, atack1 → 0)
    │
    ▼ WebSocket {"l_leg":{...}, "r_leg":{...}, "bone":{...}...}
    │
    ▼ Unity AnimalController.ApplyJoints()
  Quaternion.Lerp → Transform.localRotation
```

---

## 3. 두 엔진: Direct vs Sequential

### 3-1. MappingEngine (Direct 엔진)

**역할**: 손 DOF → 관절 각도를 즉각적으로, 선형으로 변환  

**수식**:
```
joint_angle = (hand_dof - hand_dof_ref) × scale_factor
```

- `hand_dof`: 현재 손 DOF 값 (예: index_mcp = 45°)
- `hand_dof_ref`: 캘리브레이션 기준 DOF 값 (예: index_mcp_ref = 12°)
- `scale_factor`: 손 DOF 변화량 → 관절 각도 변환 계수 (예: 1.95)

**예시**:
```
index_mcp = 45°,  index_mcp_ref = 12°,  scale_factor = 1.95
→ l_leg = (45 - 12) × 1.95 = 64.35°
```

**특징**: 프레임마다 즉시 계산, 노이즈가 있으면 떨림 발생 가능 → 데드존으로 보완

---

### 3-2. KeyframeMappingEngine (Sequential 엔진)

**역할**: 사전 녹화된 애니메이션 키프레임을 cursor 위치에서 선형 보간하여 반환  

```
시퀀스:  [frame_0] → [frame_1] → [frame_2] → ... → [frame_N-1]
cursor:   0.0          1.0         2.0               N-1
```

cursor가 2.3이면 frame_2와 frame_3을 `t=0.3`으로 선형 보간.

**또한** blend 모드가 아닌 keyframe 모드에서는 손 포즈와 가장 가까운 키프레임들을 소프트맥스 가중치로 블렌딩하는 기능도 있다. (이 문서는 blend 모드만 다룸)

---

## 4. 캘리브레이션 (양쪽 엔진 동기화)

### 왜 캘리브레이션이 필요한가?

각 사용자마다 손 크기, 관절 가동범위가 다르다. 또한 손을 완전히 펴도 손가락은 약간 구부러진 상태(자연 자세)이기 때문에 "아이들 포즈 = 손 완전 이완 상태"를 기준으로 삼아야 한다.

캘리브레이션은 **지금 이 손 포즈가 동물의 기본 자세(Idle)**임을 시스템에 알려준다.

### 캘리브레이션 흐름

```
1. 프로그램 시작 후 N초 카운트다운
2. 카운트다운 종료 시 양 손이 감지되면 → 현재 DOF 값을 기준으로 설정
3. 두 엔진 모두 동시에 캘리브레이션

   engine.calibrate(hands_angles)          ← KeyframeMappingEngine
   _engine_direct.calibrate(hands_angles)  ← MappingEngine (blend 모드 전용)
```

### 수식으로 보는 캘리브레이션

캘리브레이션 전:
```
joint_angle = (hand_dof - h_ref_original) × scale
```
`h_ref_original`은 generate_mappings.py가 생성한 통계적 기준 포즈

캘리브레이션 후:
```
joint_angle = (hand_dof - h_ref_new) × scale
h_ref_new = 캘리브레이션 시점의 실제 손 DOF 값
```

이제 캘리브레이션 포즈에서는 항상 `joint_angle = 0` → 동물이 Idle 자세를 유지.

### 버그: Direct 엔진 미캘리브레이션 (수정됨)

이전 버전에서는 `engine`(KeyframeMappingEngine)만 캘리브레이션되고 `_engine_direct`(MappingEngine)는 원본 `h_ref`를 그대로 사용했다. 그 결과:

```
캘리브레이션 포즈에서:
  engine → joint_angle = 0  (정상)
  _engine_direct → joint_angle ≠ 0  (이상: 캘리브 포즈인데 다리가 움직임)

blend 수식: joints = (1-0)*joints_d + 0*joints_kf = joints_d ≠ 0
→ 아이들 상태인데 다리가 기울어져 있는 버그
```

**수정**: 캘리브레이션 성공 시 두 엔진 모두 동시에 업데이트:

```python
if args.mapping == "blend":
    _engine_direct.calibrate(hands_angles)  # 추가된 줄
```

---

## 5. 런타임 흐름 — 단계별 상세

### Step 1: Direct 관절 각도 계산

```python
joints_d = _engine_direct.transform_clamped(hands_angles, _skeleton)
joints_d = _float_joints_to_xyz(joints_d, _engine_direct.current_animal)
```

**`transform_clamped` 내부**:
1. 각 관절에 대해 배정된 손 DOF 읽기
2. `joint_angle = (hand_dof - h_ref) × scale_factor`
3. `_skeleton`의 `min_angle`, `max_angle`으로 클리핑 (ROM 제한)

**`_float_joints_to_xyz` 내부**:  
scalar 각도값을 `{"x": 0, "y": 0, "z": angle}` 형태로 변환 (bone_map.json의 axis 기준)

예:
```
l_leg: -32.5  (axis=Z)  →  {"x": 0.0, "y": 0.0, "z": -32.5}
bone:  15.3   (axis=X)  →  {"x": 15.3, "y": 0.0, "z": 0.0}
```

---

### Step 2: 데드존 적용

```python
_DEAD = 3.0
for _jid in joints_d:
    _v = joints_d[_jid]
    joints_d[_jid] = {
        ax: (v if abs(v) >= _DEAD else 0.0)
        for ax, v in _v.items()
    }
```

**이유**: 캘리브레이션 포즈에서 손을 완전히 정지해도 센서 노이즈로 인해 ±1~2° 수준의 미세한 각도가 계산된다. 3° 미만은 0으로 처리하여 아이들 상태에서 다리가 미세하게 떨리는 현상을 방지.

---

### Step 3: 포즈 거리 트리거 판단

**목표**: 현재 손 포즈가 attack 애니메이션의 포즈에 충분히 가까운가를 거리로 판별

#### 3-1. Attack 포즈 역변환 (초기화 시 1회)

각 애니메이션의 키프레임마다 "이 동물 포즈를 만들려면 손이 어떤 모양이어야 하는가"를 역변환으로 미리 계산해둔다.

```
forward:  a = a_ref + (h - h_ref) × scale
inverse:  h = h_ref + (a - a_ref) / scale
```

예:
```
Attack1 frame3: l_leg = -30°, scale = 1.95
→ 이 포즈를 만드는 손: index_mcp = h_ref + (-30 - 0) / 1.95 = h_ref - 15.4°
```

이렇게 계산된 손 포즈를 **trigger 포즈 G_i** 라고 한다.

#### 3-2. 현재 손과 trigger 포즈의 L2 거리 계산

```python
dist = engine.min_distance_to_anim(h_left, h_right, "Attack1")
```

내부 수식:
```
dist = min over all frames i of:
    ‖h_left  - G_i_left‖₂  +  ‖h_right - G_i_right‖₂
```

- `‖ · ‖₂`: L2 norm (유클리드 거리) — 각 DOF 차이의 제곱합의 제곱근
- 모든 Attack1 프레임 중 **가장 가까운** 프레임과의 거리를 반환

**직관적 의미**: "지금 손 모양이 Attack1의 어느 프레임이라도 충분히 닮았는가"

```
거리 큼 → 현재 손이 attack 포즈와 많이 다름 (idle 상태)
거리 작음 → 현재 손이 attack 포즈에 가까움 → 트리거
```

#### 3-3. 임계값 비교 및 트리거

```python
if dist < args.attack_distance_threshold:
    _anim_state  = "action"
    _action_anim = "Attack1"
    _cursor      = 0.0
```

**수식**:
```
트리거 조건:  dist < threshold
```

캘리브레이션 후 `engine.calibrate()`가 trigger 포즈 G_i를 사용자 기준으로 재계산하므로,
개인차가 자동으로 반영된다.

```
거리:  [멀다]─────────────threshold─────[가깝다]
                              ↑
                          여기서 트리거
       (idle 상태)                   (attack 포즈에 가까운 상태)
```

#### 3-4. wrist_rot 직접 체크 (Death 전용, 옵션)

손목 비틀기(wrist_rot)의 변화량을 직접 임계값과 비교:

```python
_delta_wr_r = abs(wrist_rot_right - wrist_rot_right_ref)
if _delta_wr_r >= args.wrist_death:  # 예: 30°
    → Death 트리거
```

**이유**: Death는 손을 뒤집는 명확한 동작이라 단순 임계값이 더 직관적이고 안정적.

---

### Step 4: Sequential 키프레임 보간

트리거가 발동되면 cursor가 0에서 시작하고 매 프레임 `cursor_speed`만큼 증가:

```python
_cursor += _CURSOR_SPEED   # 기본값: 1.0 (프레임당 1 스텝)
_total   = engine.anim_frame_count(_action_anim)  # 애니메이션 총 프레임 수
```

현재 cursor 위치의 포즈를 `get_sequential_pose()`로 가져옴:

```python
joints_kf = engine.get_sequential_pose(_action_anim, _cursor)
```

**`get_sequential_pose` 내부 수식**:

```
lo = floor(cursor)       # 이전 프레임 인덱스
hi = min(lo+1, N-1)      # 다음 프레임 인덱스
t  = cursor - lo         # 보간 비율 [0.0, 1.0)

각 관절 각도 = lerp(pose[lo], pose[hi], t)
```

**Euler 최단 경로 보간 (`_short` 함수)**:

일반 선형 보간의 문제: 179° → -179°를 직선으로 보간하면 -358°를 통과 (반대 방향으로 돌아감)

```python
def _short(a: float, b: float) -> float:
    d = (b - a + 180.0) % 360.0 - 180.0  # [-180, 180] 범위의 최단 차이
    return a + d * t
```

**예시**:
```
a=170°, b=-170°, t=0.5

일반 보간:  (170 + (-170))/2 = 0°  ← 반대 방향으로 돌아감
최단 경로:  d = (-170 - 170 + 180) % 360 - 180 = -20°
            result = 170 + (-20)*0.5 = 160°  ← 올바른 방향 (단 20° 이동)
```

---

### Step 5: Alpha 블렌딩

트리거 이후 매 프레임, 두 결과를 alpha로 혼합:

```python
all_jids = set(joints_d) | set(joints_kf)   # 두 엔진의 관절 합집합

for jid in all_jids:
    d = joints_d.get(jid,  {"x": 0, "y": 0, "z": 0})
    a = joints_kf.get(jid, {"x": 0, "y": 0, "z": 0})
    joints[jid] = {
        ax: round((1 - alpha)*d[ax] + alpha*a[ax], 2)
        for ax in ("x", "y", "z")
    }
```

**수식**:
```
joints_final[jid][ax] = (1 - α) × joints_direct[jid][ax]
                       +      α  × joints_keyframe[jid][ax]

α ∈ [0.0, 1.0]
```

| α 값 | 의미 |
|------|------|
| 0.0 | 100% Direct (손 제어) |
| 0.5 | 50% Direct + 50% 애니메이션 |
| 1.0 | 100% 애니메이션 |

---

### Step 6: Body 관절 처리

```python
if _body_mapping:
    for _bj in _body_mapping:
        if _bj not in joints:
            joints[_bj] = {"x": 0.0, "y": 0.0, "z": 0.0}
```

`bone`, `bodyik_001`, `atack1`은 손과 직접 연결되지 않는 몸통 관절이다.  
아무 값도 보내지 않으면 Unity가 이전 프레임 값을 유지하므로 명시적으로 0을 전송.

단, action 상태에서 keyframe 데이터가 이미 값을 채웠다면 덮어쓰지 않음 (`if _bj not in joints`).

`atack1`은 특수 처리: 항상 keyframe 데이터에서도 제거:
```python
joints_kf.pop("atack1", None)   # IK 제어본 — 직접 적용 시 좌우 회전 유발
```

---

### Step 7: Unity WebSocket 전송

```python
server.send(json.dumps(joints))
```

Unity `AnimalController.ApplyJoints()`가 수신:
```csharp
_targetAngles[jointId] = new Vector3(x, y, z);
// Update()에서 매 프레임 Lerp로 부드럽게 적용
Quaternion.Lerp(current, restRotation * BuildRotation(entry, target), lerpSpeed * dt)
```

---

## 6. 상태머신 상세

```
┌─────────────────────────────────────────────────────┐
│                   CONTINUOUS 상태                    │
│   joints = joints_direct (α = 0)                    │
│   손 제어 100%                                       │
└──────────┬──────────────────────────────────────────┘
           │
           │  dist_to_attack < threshold  (Attack1/Attack2)
           │  또는 Δwrist_rot > wrist_death (Death, 옵션)
           │  AND cooldown == 0
           ▼
┌─────────────────────────────────────────────────────┐
│                    ACTION 상태                       │
│   cursor: 0.0 → frame_count                         │
│   매 프레임: cursor += cursor_speed                  │
│   joints = (1-α)×joints_direct + α×joints_kf        │
└──────────┬──────────────────────────────────────────┘
           │
           │  cursor >= frame_count
           ▼
┌─────────────────────────────────────────────────────┐
│                   COOLDOWN 상태                      │
│   cooldown_frames = 45 (~1.5초 @ 30fps)             │
│   joints = joints_direct                             │
│   (cooldown 동안 재트리거 방지)                       │
└──────────┬──────────────────────────────────────────┘
           │
           │  cooldown_frames == 0
           ▼
        CONTINUOUS 복귀
```

**쿨다운의 이유**: action이 끝난 직후 손이 아직 attack 포즈에 가까울 수 있어 거리가 여전히 작을 수 있다. 45프레임(~1.5초) 동안 재트리거를 막아 연속 발동을 방지.

---

## 7. Alpha 커브 수식

### 7-1. 일반 Action (Attack1, Attack2)

```python
alpha = math.sin(_cursor / _total * math.pi)
```

```
cursor/total:  0.0 ─── 0.5 ─── 1.0
alpha:          0   →   1   →   0

α
1.0 │       ╭───╮
    │      ╱     ╲
0.5 │     ╱       ╲
    │    ╱           ╲
0.0 ┼───╱─────────────╲─── cursor/total
    0                  1
```

**의미**: 애니메이션이 시작될 때 자연스럽게 진입하고, 끝날 때 손 제어로 부드럽게 복귀.

---

### 7-2. Death 액션

```python
alpha = math.sin(min(_cursor / _total, 0.5) * math.pi)
```

```
cursor/total:  0.0 ─── 0.5 ─── 1.0
alpha:          0   →   1   →   1  (1.0 유지)

α
1.0 │       ╭─────────────
    │      ╱
0.5 │     ╱
    │    ╱
0.0 ┼───╱──────────────── cursor/total
    0  0.5              1
```

**의미**: Death는 한 번 발동하면 되돌아오지 않는다. alpha가 1.0에 도달한 후 계속 유지 → 애니메이션 끝까지 keyframe이 100% 적용됨.

---

## 8. KeyframeMappingEngine 내부 구조

### 8-1. 초기화 (`_build_keyframes`)

```
set_animal("spider") 호출 시 1회 실행:

  spider_mapping.json 로드   → mapping, reference_pose_H, reference_pose_A
  spider_poses.json 로드    → animal_poses (모든 애니메이션 프레임 목록)
      │
      ▼
  각 키프레임 → 손 트리거 포즈 역변환 (_compute_trigger)
      │
      ▼
  anim_groups 구성 (anim별 프레임 그룹화)
  {"Attack1": [(trigger_0, idx_0), (trigger_1, idx_1), ...], "Death": [...]}
```

### 8-2. 역변환 수식 (`_compute_trigger`)

forward transform: `a = a_ref + (h - h_ref) × scale`  
inverse transform: `h = h_ref + (a - a_ref) / scale`

```python
h_val = ref_h + (a_val - a_ref) / scale
```

**의미**: "이 동물 포즈를 만들려면 손이 어떤 DOF 값을 가져야 하는가"를 역으로 계산.

이렇게 계산된 trigger 포즈 G_i가 **포즈 거리 트리거**에 직접 사용된다.  
`min_distance_to_anim()`이 현재 손과 모든 G_i 사이의 최소 L2 거리를 계산한다.  
캘리브레이션 시 `engine.calibrate()`가 G_i를 사용자 기준으로 재계산한다.

---

## 9. MappingEngine (Direct) 내부 구조

### 9-1. Direct 매핑 수식

```
각 관절 jid에 대해:
  - 배정된 손: hand ∈ {"left", "right"}
  - 배정된 DOF: dof_name (예: "index_mcp")
  - scale_factor: 최적화로 계산된 배율

joint_angle = (hands_angles[hand][dof_name] - reference_pose_H[hand][dof_name])
              × scale_factor
```

캘리브레이션 후:
- `reference_pose_H[hand][dof_name]`이 현재 손 값으로 업데이트됨
- 캘리브레이션 포즈 = `joint_angle = 0` 보장

---

## 10. 파라미터 목록

| 파라미터 | 기본값 | 설명 |
|---------|-------|------|
| `--mapping blend` | — | Blend 모드 활성화 |
| `--animal spider` | — | 동물 선택 |
| `--action-anims` | `Attack1,Attack2` | Action으로 처리할 애니메이션 |
| `--threshold` | `1.0` | 포즈 거리 임계값 (낮을수록 예민) |
| `--cursor-speed` | `1.0` | 프레임당 cursor 이동 속도 |
| `--temperature` | `8.0` | Keyframe blend 온도 (blend 모드 내 keyframe 엔진용) |
| `--wrist-death` | `None` | Death용 wrist_rot 임계값 (설정 시 LDA 대신 직접 체크) |
| `--dist-log` | `0` | 포즈 거리 로그 출력 간격 (0=비활성) |

---

## 정리: Blend 모드의 핵심 공식 3가지

### ① Direct 변환
```
joint[jid] = (hand_dof[assigned_dof] - h_ref[assigned_dof]) × scale_factor
```

### ② 포즈 거리 트리거
```
dist = min over frames i:  ‖h_left - G_i_left‖₂ + ‖h_right - G_i_right‖₂

트리거 조건:  dist < threshold
```

### ③ 알파 블렌딩
```
joints_final = (1 - α) × joints_direct  +  α × joints_keyframe

α = sin(cursor/total × π)           [Attack: 0→1→0]
α = sin(min(cursor/total, 0.5) × π) [Death:  0→1→유지]
```
