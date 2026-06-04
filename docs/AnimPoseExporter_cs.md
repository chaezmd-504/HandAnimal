# AnimPoseExporter.cs — Unity 애니메이션 데이터 추출기

## 개요

Unity Editor 전용 창(EditorWindow).  
동물 모델의 애니메이션 클립들을 샘플링해서 Python 파이프라인이 필요로 하는 세 가지 파일을 생성한다.

```
출력 파일:
  python/data/animal_skeletons/{animal}.json       ← skeleton (관절 목록, ROM, axis)
  python/data/animal_skeletons/bone_map_{animal}.json  ← Unity Transform 경로 매핑
  python/data/animal_skeletons/{animal}_poses.json ← 애니메이션 프레임별 관절 값
```

---

## 열기

Unity 메뉴: **Window → HandAvatar → Anim Pose Exporter**

---

## 설정 항목

| 항목 | 설명 |
|------|------|
| Animal Name | 출력 파일 이름에 사용될 동물 이름 |
| Root Transform | 동물 모델의 최상위 Transform |
| Animator | 애니메이션 클립이 있는 Animator 컴포넌트 |
| Rest Animation | 기준 포즈 클립 이름 (보통 "Idle") |
| Samples Per Clip | 클립당 샘플 수 (권장 30) |
| Margin Deg | ROM 마진 (양끝에 추가되는 여유 °, 기본 5°) |
| Output Dir | 출력 폴더 경로 |

---

## 실행 흐름

### Step 1: Rest 포즈 캡처

```csharp
// "Idle" 클립의 첫 프레임을 샘플링
AnimationMode.SampleAnimationClip(go, restClip, 0f);
// 모든 Transform의 localRotation 저장 → restRotations[transform]
```

### Step 2: 각 애니메이션 클립 샘플링

```csharp
foreach (AnimationClip clip in allClips)
{
    for (int s = 0; s < samplesPerClip; s++)
    {
        float t = (float)s / (samplesPerClip - 1) * clip.length;
        AnimationMode.SampleAnimationClip(go, clip, t);
        
        // 각 관절의 delta 회전 계산
        Quaternion delta = Quaternion.Inverse(restRot) * currentRot;
        eulerDelta = delta.eulerAngles;  // 0~360 → -180~180 변환
        
        // 샘플 저장: {_anim: "Attack1", _frame: 3, "l_leg": -27.5, "r_leg": 15.2, ...}
    }
}
```

### Step 3: 관절별 통계 계산

모든 샘플에서 각 관절의 최대 변화가 일어난 축을 찾아 **axis** 결정:

```csharp
foreach (var joint in allJoints)
{
    float stdX = StdDev(joint.xSamples);
    float stdY = StdDev(joint.ySamples);
    float stdZ = StdDev(joint.zSamples);
    
    // 가장 많이 변한 축이 이 관절의 axis
    joint.axis = stdX > stdY && stdX > stdZ ? "X"
               : stdY > stdZ               ? "Y"
               :                             "Z";
    
    joint.minAngle = Min(dominantSamples) - marginDeg;
    joint.maxAngle = Max(dominantSamples) + marginDeg;
}
```

### Step 4: skeleton.json 저장 (chains 보존 포함)

**이전 버그**: 매번 `"chains": []`로 덮어쓰던 문제.  
**수정**: 기존 파일의 chains 배열을 먼저 읽은 뒤 새 파일에 그대로 삽입.

```csharp
string existingChains = "[]";  // 기본값
if (File.Exists(skelPath))
{
    string existing = File.ReadAllText(skelPath);
    int ci = existing.IndexOf("\"chains\"");
    if (ci >= 0)
    {
        int arrStart = existing.IndexOf('[', ci);
        // ★ depth-tracking 루프로 중첩 배열의 정확한 끝 위치 탐색
        int depth = 0, arrEnd = -1;
        for (int k = arrStart; k < existing.Length; k++)
        {
            if      (existing[k] == '[') depth++;
            else if (existing[k] == ']') { depth--; if (depth == 0) { arrEnd = k; break; } }
        }
        if (arrEnd > arrStart)
            existingChains = existing.Substring(arrStart, arrEnd - arrStart + 1);
    }
}

// BuildSkeletonJson에 기존 chains 전달
File.WriteAllText(skelPath, BuildSkeletonJson(jointInfos, existingChains));
```

#### depth-tracking이 필요한 이유

chains의 구조:
```json
"chains": [
    ["l_leg", "l_bone_012", "l_bone_013"],
    ["l_leg_001", "l_bone_009", "l_bone_010"],
    ...
]
```

단순 `IndexOf(']')`를 사용하면 첫 번째 내부 배열 `["l_leg", "l_bone_012", "l_bone_013"]`의 `]`에서 멈춤 → chains가 잘림.  
depth counter를 이용해 `[` 만나면 +1, `]` 만나면 -1, depth가 0이 되는 `]` = 전체 배열의 끝.

### Step 5: bone_map.json 저장

```json
{
  "joint_map": {
    "l_leg": {
      "unity_path": "Spider/Bone/L.Leg",   ← Transform.GetPath() 결과
      "axis": "Z"
    },
    "bone": {
      "unity_path": "Spider/Bone",
      "axis": "Z"   ← 이후 수동으로 X로 변경됨 (bone 뒤집기 문제)
    }
  }
}
```

### Step 6: poses.json 저장

```json
[
  {"_anim": "Attack1", "_frame": 0, "l_leg": 0.0, "r_leg": 0.0, "bone": 0.0, ...},
  {"_anim": "Attack1", "_frame": 1, "l_leg": -17.2, "r_leg": 17.8, "bone": 0.0, ...},
  ...
  {"_anim": "Death", "_frame": 12, "bone": 171.54, "bodyik_001": -71.55, ...},
]
```

각 값은 **Rest 포즈 기준 delta(°)**, dominant axis 방향의 변화량.

---

## BuildSkeletonJson()

```csharp
private string BuildSkeletonJson(List<JointInfo> joints, string chainsJson = "[]")
{
    sb.AppendLine("{");
    sb.AppendLine($"  \"animal_name\": \"{_animalName}\",");
    sb.AppendLine($"  \"bilateral\": true,");
    sb.AppendLine("  \"joints\": [");
    
    foreach (var j in joints)
    {
        sb.AppendLine($"    {{");
        sb.AppendLine($"      \"id\": \"{j.jointId}\",");
        sb.AppendLine($"      \"axis\": \"{j.axis}\",");
        sb.AppendLine($"      \"min_angle\": {j.minAngle:F1},");
        sb.AppendLine($"      \"max_angle\": {j.maxAngle:F1},");
        sb.AppendLine($"    }},");
    }
    
    sb.AppendLine("  ],");
    sb.AppendLine($"  \"chains\": {chainsJson}");  // ← 기존 chains 삽입
    sb.Append("}");
}
```

---

## 주의사항

1. **AnimPoseExporter 실행 전에** `spider_body_mapping.json` 같은 수동 파일이 있어야  
   AnimalControllerEditor가 올바르게 bone/bodyik_001을 포함시킬 수 있다.

2. **chains는 AnimPoseExporter가 자동 생성하지 않는다.**  
   `python scripts/auto_chains.py --animal spider`로 별도 생성 후 spider.json에 병합.  
   AnimPoseExporter는 이제 기존 chains를 보존한다.

3. **샘플 수**: 클립당 30개 권장. 너무 적으면 ROM 계산이 부정확, 너무 많으면 파일 크기 증가.

4. **Rest Anim**: 반드시 T-포즈가 아닌 실제 기본 자세 클립 이름 입력 ("Idle" 등).  
   AnimalController의 restRotation 재캡처와 기준이 맞아야 한다.

---

## 실행 후 확인사항

```
[OK] skeleton.json 저장: .../spider.json
[OK] bone_map_spider.json 저장: .../bone_map_spider.json
[OK] spider_poses.json 저장: .../spider_poses.json  (N개 포즈)
```

생성 후 반드시:
1. `python scripts/auto_chains.py --animal spider` (chains 추가)
2. `python scripts/generate_mappings.py --animal spider` (매핑 재생성)
3. Unity AnimalControllerEditor "JointEntry 전체 자동 생성" 클릭
