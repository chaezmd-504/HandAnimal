# AnimalControllerEditor.cs — Unity Inspector 자동 설정 도구

## 개요

`AnimalController`의 Custom Inspector.  
버튼 하나로 `bone_map_{animal}.json`과 `skeleton.json`을 읽어 JointEntry 리스트를  
자동으로 생성하고 Transform을 연결한다.

---

## Inspector 버튼

```
동물 이름 입력: [ spider ]

[JointEntry 전체 자동 생성 (bone_map.json)]  ← 이 버튼
```

---

## 버튼 클릭 시 흐름

### 1단계: bone_map 파싱

```csharp
string bmpRaw = File.ReadAllText("bone_map_spider.json");
var idToInfo = ParseBoneMapFull(bmpRaw);
// {"l_leg": {unityPath:"Spider/Bone/L.Leg", axis:"Z"}, ...}
```

### 2단계: allowed 관절 필터링

```csharp
var allowed = ParseMappingJointIds("spider_mapping.json");
// → {"l_leg", "r_leg", "l_leg_001", ..., "l_bone_013", ...}  (24개)

// ★ 수정: body_mapping도 합산
if (File.Exists("spider_body_mapping.json"))
    foreach (var id in ParseMappingJointIds("spider_body_mapping.json"))
        allowed.Add(id);
// → 추가: {"bone", "bodyik_001", "atack1"}  (총 27개)
```

**이전 버그**: `spider_mapping.json`만 읽어 body 관절 3개가 항상 제외됨.  
**수정**: `spider_body_mapping.json`도 읽어 allowed에 합산.

### 3단계: ModelReviewWindow 열기

필터된 관절 목록을 검수 창에 표시. 체인 루트 관절에는 손가락 자동 감지.

```csharp
ModelReviewWindow.Open(_animal, reviewEntries, included =>
{
    // 사용자가 체크한 관절만 → AutoGenerateEntriesFromMap() 호출
});
```

### 4단계: AutoGenerateEntriesFromMap() — JointEntry 생성

```csharp
entriesProp.ClearArray();  // 기존 목록 전체 삭제

foreach (var kv in idToInfo)
{
    string jointId   = kv.Key;
    string unityPath = kv.Value.unityPath;

    // Transform 탐색: "Spider/Bone/L.Leg" → root.Find("Spider/Bone/L.Leg")
    Transform bone = root.Find(unityPath);
    if (bone == null)
    {
        // 첫 세그먼트(모델 루트 이름) 제거 후 재시도
        int slash = unityPath.IndexOf('/');
        if (slash >= 0) bone = root.Find(unityPath.Substring(slash + 1));
    }

    // axis/ROM: skeleton.json 우선, 없으면 bone_map axis 사용
    bool axX=false, axY=false, axZ=false;
    float minA=-45f, maxA=45f;

    if (skelData.TryGetValue(jointId, out var jd))
    {
        axX = jd.axis == "X";   // spider.json의 axis 필드
        axY = jd.axis == "Y";
        axZ = jd.axis == "Z";
        minA = jd.minAngle;     // spider.json의 min_angle
        maxA = jd.maxAngle;     // spider.json의 max_angle
    }

    // SerializedProperty로 Inspector 값 세팅
    entry.FindPropertyRelative("axisX").boolValue = axX;
    entry.FindPropertyRelative("minAngle").floatValue = minA;
    ...
}
```

---

## ParseMappingJointIds() — JSON 파서

Newtonsoft 없이 간이 파서로 `"mapping"` 블록의 키를 추출:

```csharp
// {"mapping": {"l_leg": {...}, "r_leg": {...}, ...}} 에서 depth=1 레벨 키만 추출
int depth = 0;
while (pos < json.Length)
{
    if      (c == '{') depth++;
    else if (c == '}') { depth--; if (depth == 0) break; }
    else if (c == '"' && depth == 1)
    {
        // 따옴표 사이 문자열 = 관절 ID
        ids.Add(json.Substring(pos+1, q2-pos-1));
    }
}
```

depth == 1 인 `"` = 관절 ID (depth == 2는 관절 속성들이므로 무시).

---

## ParseSkeletonJson() — skeleton 파서

`spider.json`의 `"joints"` 배열에서 각 관절의 axis, min_angle, max_angle 추출:

```csharp
// "joints": [ {"id": "bone", "axis": "X", "min_angle": -180.1, "max_angle": 184.1}, ... ]
for (int i = arrStart; i < json.Length; i++)
{
    if (c == '{') { if (depth==0) objStart = i; depth++; }
    else if (c == '}')
    {
        depth--;
        if (depth == 0)
        {
            string obj = json.Substring(objStart, ...);
            string id      = ExtractString(obj, "id");
            string axis    = ExtractString(obj, "axis");
            float  minAngle = ExtractFloat(obj, "min_angle");
            float  maxAngle = ExtractFloat(obj, "max_angle");
            result[id] = new JointData { axis, minAngle, maxAngle };
        }
    }
}
```

---

## ParseChainRoots() — 체인 루트 탐색

`spider.json`의 `"chains"` 배열에서 각 체인의 첫 번째 관절 ID 추출.  
ModelReviewWindow에서 "이 관절이 체인 루트인가?"를 판단하는 데 사용.

```csharp
// "chains": [ ["l_leg", "l_bone_012", "l_bone_013"], [...] ]
// → chainRoots = {"l_leg", "l_leg_001", "l_leg_002", "l_leg_003",
//                  "r_leg", "r_leg_001", "r_leg_002", "r_leg_003"}
int depth = 0;
bool inInner = false;
for (int i = arrStart; i < json.Length; i++)
{
    if (c == '[') { depth++; if (depth == 2) inInner = true; }
    else if (c == ']') { depth--; inInner = false; }
    else if (inInner && c == '"')
    {
        roots.Add(첫 번째 문자열);
        inInner = false;   // 내부 배열의 첫 항목만 취함
    }
}
```

---

## 경로 계산 헬퍼들

```csharp
private static string ResolveJsonPath(string animal)
    // → Application.dataPath/../../python/data/animal_skeletons/{animal}.json

private static string ResolveBoneMapPath(string animal)
    // → Application.dataPath/../../python/data/animal_skeletons/bone_map_{animal}.json

private static string ResolveMappingPath(string animal)
    // → Application.dataPath/../../python/data/mappings/{animal}_mapping.json

private static string ResolveBodyMappingPath(string animal)
    // → Application.dataPath/../../python/data/mappings/{animal}_body_mapping.json
```

모두 `Application.dataPath`(= `unity/Assets`) 기준 상대 경로로 Python 폴더를 찾는다.

---

## 버튼 클릭 전 확인사항

1. `bone_map_spider.json` 존재 여부 → 없으면 버튼 비활성화
2. `spider_mapping.json` 존재 → 필터링에 사용 (없으면 필터링 스킵)
3. `spider_body_mapping.json` 존재 → 있으면 body 관절도 포함

---

## 완료 후 출력

```
[AnimalControllerEditor] 매핑 필터: 27개 관절
[AnimalController] 관절 27개 로드됨

JointEntry 27개 생성
  Transform 연결됨: 27개
  Transform 없음: 0개
```

Transform 없음이 뜨면 bone_map의 `unity_path`가 실제 계층 구조와 다른 것.
