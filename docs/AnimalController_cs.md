# AnimalController.cs — Unity 관절 제어기

## 개요

Python에서 WebSocket으로 받은 관절 회전값을 Unity의 Transform에 실제로 적용하는 컴포넌트.  
Spider 오브젝트의 최상위 GameObject에 부착되며, 각 관절(JointEntry)에 Lerp 스무딩을 적용해 부드럽게 움직인다.

---

## Inspector 설정

```
Joint Entries         ← Python에서 제어할 관절 목록 (bone, l_leg, r_leg, ...)
Lerp Speed            ← 12.0 (클수록 빠르게 추종, 60fps 기준)
Idle Animator         ← Idle 애니메이션을 재생하는 Animator 컴포넌트
Auto Infer Axes       ← true면 경고 출력 (Editor 버튼으로 세팅 필요)
```

---

## JointEntry 구조

```csharp
public class JointEntry
{
    public string    jointName;        // "bone", "l_leg", "r_bone_006" 등
    public Transform jointTransform;   // 실제 Unity Transform

    public bool axisX, axisY, axisZ;  // 활성화된 회전 축 (복수 가능)
    public float minAngle, maxAngle;  // ROM 클램프 범위 (°)

    [NonSerialized] public Quaternion restRotation;  // Idle 포즈 기준 초기 회전
}
```

`axisX/Y/Z`는 AnimalControllerEditor의 "JointEntry 전체 자동 생성" 버튼이 `spider.json`의 axis 필드를 읽어 자동으로 설정.

---

## Awake() — 초기화

```csharp
void Awake()
{
    foreach (var entry in jointEntries)
    {
        entry.restRotation = entry.jointTransform.localRotation;
        _jointMap[entry.jointName] = entry;
    }
}
```

**주의**: Awake 시점은 Animator가 아직 Idle 포즈를 적용하기 전 → **T-포즈 기준**으로 restRotation이 캡처됨.  
이 문제는 `ApplyJoints()`에서 첫 호출 시 재캡처로 해결.

---

## ApplyJoints() — 핵심 메서드

Python에서 관절 데이터가 도착할 때마다 호출됨.

```csharp
public void ApplyJoints(Dictionary<string, JointRotation> joints)
{
    bool wasIdle = _isIdle;
    _isIdle = false;

    if (idleAnimator != null && idleAnimator.enabled)
    {
        idleAnimator.enabled = false;

        if (wasIdle)
        {
            // ★ 핵심 수정: Idle → Active 첫 전환 시 restRotation 재캡처
            // Animator가 이미 Idle 포즈를 적용한 상태이므로
            // 이 시점의 localRotation = 올바른 Idle 포즈
            foreach (var entry in _jointMap.Values)
                if (entry.jointTransform != null)
                    entry.restRotation = entry.jointTransform.localRotation;
        }
    }

    // target 각도 업데이트 (실제 회전은 Update에서 Lerp로 적용)
    foreach (var kv in joints)
        _targetAngles[kv.Key] = new Vector3(kv.Value.x, kv.Value.y, kv.Value.z);
}
```

### 왜 재캡처가 필요한가?

```
Scene 시작
    │
    ▼
Awake()  ← Unity 초기화 순서상 Animator보다 먼저 실행
    │  restRotation = T-포즈 (Animator 미적용)
    ▼
Animator.Update() ← Idle 클립 적용 → 관절들이 Idle 포즈로 이동
    │
    ▼
Python 데이터 첫 도착 → ApplyJoints() 호출
    │  wasIdle=true → restRotation 재캡처 (현재 = Idle 포즈) ✓
    ▼
이후 모든 회전은 Idle 포즈 기준 delta로 적용
```

재캡처 없으면: T-포즈를 기준으로 delta 적용 → 관절이 Idle 위치에서 갑자기 엉뚱한 방향으로 이동.

---

## Update() — Lerp 적용

```csharp
void Update()
{
    if (_isIdle) return;

    foreach (var kv in _targetAngles)
    {
        var entry = _jointMap[kv.Key];
        Quaternion current = entry.jointTransform.localRotation;
        Quaternion target  = BuildRotation(entry, kv.Value);
        entry.jointTransform.localRotation =
            Quaternion.Lerp(current, target, lerpSpeed * Time.deltaTime);
    }
}
```

매 프레임마다 현재 회전 → 목표 회전으로 Lerp.  
`lerpSpeed=12`이면 60fps에서 `Lerp(current, target, 0.2/frame)` → 약 0.1초 내 90% 도달.

---

## BuildRotation() — 핵심 회전 계산

```csharp
private static Quaternion BuildRotation(JointEntry entry, Vector3 rot)
{
    // axisX/Y/Z 플래그에 따라 해당 성분만 사용
    float ax = entry.axisX ? Mathf.Clamp(rot.x, entry.minAngle, entry.maxAngle) : 0f;
    float ay = entry.axisY ? Mathf.Clamp(rot.y, entry.minAngle, entry.maxAngle) : 0f;
    float az = entry.axisZ ? Mathf.Clamp(rot.z, entry.minAngle, entry.maxAngle) : 0f;

    Quaternion delta = Quaternion.Euler(ax, ay, az);
    return entry.restRotation * delta;
}
```

### 계산 구조

```
restRotation      = Idle 포즈에서의 localRotation
delta             = Quaternion.Euler(0, 0, 171.54)   ← Python이 보낸 값 (Death bone.z)
result            = restRotation * delta

의미: "Idle 포즈를 기준으로, 거기서 추가로 171.54° 더 회전"
```

### ROM 클램핑

```
Python이 보낸 값   → Clamp(value, minAngle, maxAngle)
bone.z = 171.54   → spider.json의 bone min=-180.1, max=184.1 → 통과
일반 다리         → min=-69.3, max=46.6 → 이 범위 내로 제한
```

### axisX/Y/Z 플래그의 역할

하나의 관절이 여러 축을 가질 수도 있음 (spider 관절들은 대부분 1축).  
- axisZ=true이면 Python이 보낸 `rot.z` 값만 사용, `rot.x`, `rot.y`는 무시
- axisX=true이면 Python이 보낸 `rot.x` 값만 사용

**현재 bone/bodyik_001**: axisX=true (Z→X 변경 후, 미검증)

---

## SetIdle() / GetCurrentAngles()

```csharp
public void SetIdle()
{
    _isIdle = true;
    if (idleAnimator != null) idleAnimator.enabled = true;
}

public Dictionary<string, JointRotation> GetCurrentAngles()
{
    // 각 관절의 현재 localRotation에서 restRotation을 빼서 delta 반환
    Quaternion delta = Quaternion.Inverse(restRotation) * jointTransform.localRotation;
    Vector3 e = delta.eulerAngles;
    // 0~360 → -180~180 변환
    float x = e.x > 180f ? e.x - 360f : e.x;
    ...
}
```

`GetCurrentAngles()`는 `AvatarPoseSender`가 Unity → Python 방향으로 포즈를 전송할 때 사용.

---

## 로그 출력 (처음 5회만)

```
[AnimalController] 관절 27개 로드됨
  bone: 축=X  ROM=[-180°, 184°]  rest=(0.000,0.000,0.000,1.000)
  l_leg: 축=Z  ROM=[-69°, 47°]  rest=(...)
  ...

[AnimalController] restRotation을 Idle 포즈 기준으로 재캡처

[AnimalController] ApplyJoints #1
  ✅ 적용됨 (24개): l_leg=(-2.1,0.0,0.0) r_leg=(1.8,0.0,0.0) ...
  ❌ Transform 없음 (0개):
```

---

## 파일 의존성

```
AnimalController.cs
    ← bone_map_spider.json  (AnimalControllerEditor가 읽어 Inspector 세팅)
    ← spider.json           (axis, minAngle, maxAngle)
    ← spider_mapping.json   (어떤 관절이 포함되는지)
    ← spider_body_mapping.json (bone/bodyik_001/atack1 포함)
```
