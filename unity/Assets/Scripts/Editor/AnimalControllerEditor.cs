// AnimalControllerEditor.cs
// AnimalController Inspector에 버튼 추가.
//
// ① 전체 자동 생성 (bone_map.json)
//      bone_map_{animal}.json 을 읽어 JointEntry 리스트 전체를 한 번에 생성한다.
//      Transform.Find() 로 각 본을 자동으로 연결하고 skeleton.json 에서 ROM/축도 채운다.
//      → 새 동물 에셋을 넣을 때 이 버튼 하나로 AnimalController 설정 완료.
//
// ② 축 / ROM 채우기 (skeleton.json)
//      기존 JointEntry 의 axis/ROM 만 갱신한다.
//
// ③ joint ID 자동 매핑 (bone_map.json)
//      이미 jointTransform 이 연결된 항목의 jointName 을 bone_map 으로 채운다.
//
// 사용법 (신규 동물):
//   1. AnimPoseExporter 로 skeleton.json + bone_map_{animal}.json + {animal}_poses.json 생성
//   2. 동물 이름 입력 후 "① 전체 자동 생성" 클릭
//   3. generate_mappings.py 실행 → {animal}_mapping.json 자동 생성

using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

[CustomEditor(typeof(AnimalController))]
public class AnimalControllerEditor : Editor
{
    private string _animal = "spider";

    public override void OnInspectorGUI()
    {
        DrawDefaultInspector();

        EditorGUILayout.Space(10);
        EditorGUILayout.LabelField("Joint 자동 설정", EditorStyles.boldLabel);

        _animal = EditorGUILayout.TextField("동물 이름", _animal);

        string jsonPath    = ResolveJsonPath(_animal);
        string boneMapPath = ResolveBoneMapPath(_animal);
        bool   jsonExists    = File.Exists(jsonPath);
        bool   boneMapExists = File.Exists(boneMapPath);

        // ── ① 전체 자동 생성 ─────────────────────────────────────────
        EditorGUILayout.Space(4);
        EditorGUILayout.LabelField("① 전체 자동 생성 (신규 동물 전용)", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox(
            "bone_map_{animal}.json + skeleton.json 을 읽어\n" +
            "JointEntry 리스트 전체를 자동 생성합니다.\n" +
            "Transform.Find() 로 본을 자동 연결하고 축/ROM 도 채웁니다.\n\n" +
            "⚠ 기존 리스트가 모두 지워집니다.",
            MessageType.Info);

        EditorGUI.BeginDisabledGroup(!boneMapExists);
        if (GUILayout.Button("① JointEntry 전체 자동 생성 (bone_map.json)", GUILayout.Height(35)))
            AutoGenerateEntries(boneMapPath, jsonExists ? jsonPath : null);
        EditorGUI.EndDisabledGroup();

        if (!boneMapExists)
            EditorGUILayout.HelpBox("bone_map 없음: " + boneMapPath +
                                    "\n먼저 AnimPoseExporter 실행", MessageType.Warning);

        // ── ② 축 / ROM 채우기 ────────────────────────────────────────
        EditorGUILayout.Space(6);
        EditorGUILayout.LabelField("② 축 / ROM 채우기 (기존 항목 갱신)", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox(
            "기존 JointEntry 의 axis/ROM 만 skeleton.json 값으로 덮어씁니다.",
            MessageType.None);

        EditorGUI.BeginDisabledGroup(!jsonExists);
        if (GUILayout.Button("② 축 / ROM 채우기 (skeleton.json)", GUILayout.Height(30)))
            ApplySkeletonJson(jsonPath);
        EditorGUI.EndDisabledGroup();

        if (!jsonExists)
            EditorGUILayout.HelpBox("skeleton.json 없음: " + jsonPath, MessageType.Warning);

        // ── ③ joint ID 매핑 ──────────────────────────────────────────
        EditorGUILayout.Space(6);
        EditorGUILayout.LabelField("③ joint ID 자동 매핑 (Transform 경로 → 이름)", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox(
            "jointTransform 이 이미 연결된 항목의 jointName 을\n" +
            "bone_map 경로 기반으로 채웁니다.",
            MessageType.None);

        EditorGUI.BeginDisabledGroup(!boneMapExists);
        if (GUILayout.Button("③ joint ID 자동 매핑 (bone_map.json)", GUILayout.Height(30)))
            AutoMatchJointNames(boneMapPath);
        EditorGUI.EndDisabledGroup();
    }

    // ── ① 전체 자동 생성 ─────────────────────────────────────────────
    private void AutoGenerateEntries(string boneMapPath, string skeletonPath)
    {
        // bone_map 파싱: joint_id → {unity_path, axis}
        string boneMapRaw = File.ReadAllText(boneMapPath);
        var idToInfo = ParseBoneMapFull(boneMapRaw);
        if (idToInfo.Count == 0)
        {
            EditorUtility.DisplayDialog("오류", "bone_map 파싱 실패", "OK");
            return;
        }

        // skeleton.json 파싱: joint_id → {axis, minAngle, maxAngle}
        Dictionary<string, JointData> skelData = null;
        if (skeletonPath != null && File.Exists(skeletonPath))
            skelData = ParseSkeletonJson(File.ReadAllText(skeletonPath));

        var ctrl       = (AnimalController)target;
        Transform root = ctrl.transform;

        SerializedObject   so          = new SerializedObject(target);
        SerializedProperty entriesProp = so.FindProperty("jointEntries");

        // 기존 리스트 초기화
        entriesProp.ClearArray();

        int created = 0, notFound = 0;
        var missing = new List<string>();

        foreach (var kv in idToInfo)
        {
            string jointId  = kv.Key;
            string unityPath = kv.Value.unityPath;
            string axisHint  = kv.Value.axis;   // from bone_map

            // Transform 탐색
            Transform bone = root.Find(unityPath);
            if (bone == null)
            {
                // unity_path 가 모델 루트 이름을 포함할 수 있으므로 첫 세그먼트를 건너뛰고 재시도
                int slash = unityPath.IndexOf('/');
                if (slash >= 0)
                    bone = root.Find(unityPath.Substring(slash + 1));
            }

            // skeleton.json 우선, 없으면 bone_map axis 사용
            bool   axX = false, axY = false, axZ = false;
            float  minA = -45f, maxA = 45f;

            if (skelData != null && skelData.TryGetValue(jointId, out var jd))
            {
                axX = jd.axis == "X"; axY = jd.axis == "Y"; axZ = jd.axis == "Z";
                minA = jd.minAngle; maxA = jd.maxAngle;
            }
            else if (!string.IsNullOrEmpty(axisHint))
            {
                string ax = axisHint.ToUpper();
                axX = ax == "X"; axY = ax == "Y"; axZ = ax == "Z";
            }

            // 새 항목 추가
            entriesProp.InsertArrayElementAtIndex(entriesProp.arraySize);
            SerializedProperty entry = entriesProp.GetArrayElementAtIndex(entriesProp.arraySize - 1);

            entry.FindPropertyRelative("jointName").stringValue           = jointId;
            entry.FindPropertyRelative("jointTransform").objectReferenceValue = bone;
            entry.FindPropertyRelative("axisX").boolValue                 = axX;
            entry.FindPropertyRelative("axisY").boolValue                 = axY;
            entry.FindPropertyRelative("axisZ").boolValue                 = axZ;
            entry.FindPropertyRelative("minAngle").floatValue             = minA;
            entry.FindPropertyRelative("maxAngle").floatValue             = maxA;

            if (bone != null) created++;
            else { notFound++; missing.Add($"{jointId} ({unityPath})"); }
        }

        so.FindProperty("autoInferAxes").boolValue = false;
        so.ApplyModifiedProperties();
        EditorUtility.SetDirty(target);

        string msg = $"JointEntry {created + notFound}개 생성\n" +
                     $"  Transform 연결됨: {created}개\n" +
                     $"  Transform 없음: {notFound}개";
        if (notFound > 0)
            msg += "\n\n경로 불일치:\n" + string.Join("\n", missing);

        Debug.Log($"[AnimalControllerEditor] {msg}");
        EditorUtility.DisplayDialog("완료", msg, "OK");
    }

    // ── JSON 경로 계산 ────────────────────────────────────────────
    private static string ResolveJsonPath(string animal)
    {
        return Path.GetFullPath(
            Path.Combine(Application.dataPath,
                         $"../../python/data/animal_skeletons/{animal}.json"));
    }

    private static string ResolveBoneMapPath(string animal)
    {
        return Path.GetFullPath(
            Path.Combine(Application.dataPath,
                         $"../../python/data/animal_skeletons/bone_map_{animal}.json"));
    }

    // ── JSON 읽어서 axis / ROM 적용 ───────────────────────────────
    private void ApplySkeletonJson(string jsonPath)
    {
        // 간단한 JSON 파싱 (Newtonsoft 없이 처리)
        string raw = File.ReadAllText(jsonPath);
        var jointData = ParseSkeletonJson(raw);
        if (jointData == null || jointData.Count == 0)
        {
            EditorUtility.DisplayDialog("오류", "joints 파싱 실패", "OK");
            return;
        }

        SerializedObject   so          = new SerializedObject(target);
        SerializedProperty entriesProp = so.FindProperty("jointEntries");
        SerializedProperty autoInfer   = so.FindProperty("autoInferAxes");

        // autoInferAxes 끄기
        autoInfer.boolValue = false;

        int updated = 0, notFound = 0;
        var missing = new List<string>();

        for (int i = 0; i < entriesProp.arraySize; i++)
        {
            SerializedProperty entry     = entriesProp.GetArrayElementAtIndex(i);
            string             jointName = entry.FindPropertyRelative("jointName").stringValue;

            if (!jointData.TryGetValue(jointName, out var jd))
            {
                notFound++;
                missing.Add(jointName);
                continue;
            }

            entry.FindPropertyRelative("axisX").boolValue    = jd.axis == "X";
            entry.FindPropertyRelative("axisY").boolValue    = jd.axis == "Y";
            entry.FindPropertyRelative("axisZ").boolValue    = jd.axis == "Z";
            entry.FindPropertyRelative("minAngle").floatValue = jd.minAngle;
            entry.FindPropertyRelative("maxAngle").floatValue = jd.maxAngle;
            updated++;
        }

        so.ApplyModifiedProperties();
        EditorUtility.SetDirty(target);

        string msg = $"{updated}개 관절 갱신 완료\nautoInferAxes 비활성화";
        if (notFound > 0)
            msg += $"\n\n매핑 없음 ({notFound}개):\n" + string.Join(", ", missing);

        Debug.Log($"[AnimalControllerEditor] {msg}");
        EditorUtility.DisplayDialog("완료", msg, "OK");
    }

    // ── joint ID 자동 매핑 ────────────────────────────────────────
    private void AutoMatchJointNames(string boneMapPath)
    {
        string raw = File.ReadAllText(boneMapPath);
        // bone_map: joint_id → unity_path 파싱
        var pathToId = ParseBoneMap(raw);
        if (pathToId.Count == 0)
        {
            EditorUtility.DisplayDialog("오류", "bone_map 파싱 실패", "OK");
            return;
        }

        var ctrl        = (AnimalController)target;
        Transform root  = ctrl.transform;

        SerializedObject   so          = new SerializedObject(target);
        SerializedProperty entriesProp = so.FindProperty("jointEntries");

        int matched = 0, skipped = 0;
        var missingPaths = new List<string>();

        for (int i = 0; i < entriesProp.arraySize; i++)
        {
            SerializedProperty entry       = entriesProp.GetArrayElementAtIndex(i);
            SerializedProperty tfProp      = entry.FindPropertyRelative("jointTransform");
            SerializedProperty nameProp    = entry.FindPropertyRelative("jointName");

            if (tfProp.objectReferenceValue == null) { skipped++; continue; }

            Transform bone = (Transform)tfProp.objectReferenceValue;
            string relPath = GetRelativePath(root, bone);

            if (pathToId.TryGetValue(relPath, out string jointId))
            {
                nameProp.stringValue = jointId;
                matched++;
            }
            else
            {
                missingPaths.Add($"{bone.name} ({relPath})");
                skipped++;
            }
        }

        so.ApplyModifiedProperties();
        EditorUtility.SetDirty(target);

        string msg = $"{matched}개 jointName 자동 매핑 완료";
        if (skipped > 0)
            msg += $"\n\n경로 불일치 {skipped}개:\n" + string.Join("\n", missingPaths);

        Debug.Log($"[AnimalControllerEditor] {msg}");
        EditorUtility.DisplayDialog("완료", msg, "OK");
    }

    // bone_map JSON 파싱: joint_id → {unity_path, axis}  (AutoGenerateEntries 용)
    private struct BoneMapEntry { public string unityPath; public string axis; }

    private static Dictionary<string, BoneMapEntry> ParseBoneMapFull(string json)
    {
        var result = new Dictionary<string, BoneMapEntry>();

        int mapStart  = json.IndexOf("\"joint_map\"");
        if (mapStart < 0) return result;
        int braceStart = json.IndexOf('{', mapStart + "\"joint_map\"".Length);
        if (braceStart < 0) return result;

        int depth = 0, objStart = -1;
        string currentId = null;

        for (int i = braceStart; i < json.Length; i++)
        {
            char c = json[i];
            if (c == '{')
            {
                if (depth == 1) objStart = i;
                depth++;
            }
            else if (c == '}')
            {
                depth--;
                if (depth == 1 && objStart >= 0 && currentId != null)
                {
                    string obj       = json.Substring(objStart, i - objStart + 1);
                    string unityPath = ExtractString(obj, "unity_path");
                    string axis      = ExtractString(obj, "axis");
                    if (!string.IsNullOrEmpty(unityPath))
                        result[currentId] = new BoneMapEntry { unityPath = unityPath, axis = axis ?? "X" };
                    objStart  = -1;
                    currentId = null;
                }
                else if (depth == 0) break;
            }
            else if (c == '"' && depth == 1)
            {
                int q2 = json.IndexOf('"', i + 1);
                if (q2 > i)
                {
                    string candidate = json.Substring(i + 1, q2 - i - 1);
                    if (!candidate.StartsWith("_"))
                        currentId = candidate;
                    i = q2;
                }
            }
        }
        return result;
    }

    // bone_map JSON 파싱: unity_path → joint_id
    private static Dictionary<string, string> ParseBoneMap(string json)
    {
        var result = new Dictionary<string, string>();

        int mapStart = json.IndexOf("\"joint_map\"");
        if (mapStart < 0) return result;
        int braceStart = json.IndexOf('{', mapStart + "\"joint_map\"".Length);
        if (braceStart < 0) return result;

        // 중괄호 매칭으로 각 joint 블록 추출
        int depth = 0, objStart = -1;
        string currentId = null;

        for (int i = braceStart; i < json.Length; i++)
        {
            char c = json[i];
            if (c == '{')
            {
                if (depth == 1) objStart = i;   // joint 오브젝트 시작
                depth++;
            }
            else if (c == '}')
            {
                depth--;
                if (depth == 1 && objStart >= 0 && currentId != null)
                {
                    string obj      = json.Substring(objStart, i - objStart + 1);
                    string unityPath = ExtractString(obj, "unity_path");
                    if (!string.IsNullOrEmpty(unityPath))
                        result[unityPath] = currentId;
                    objStart  = -1;
                    currentId = null;
                }
                else if (depth == 0) break;     // joint_map 블록 종료
            }
            else if (c == '"' && depth == 1)
            {
                // joint ID 문자열 읽기 (depth=1 에서의 key)
                int q2 = json.IndexOf('"', i + 1);
                if (q2 > i)
                {
                    string candidate = json.Substring(i + 1, q2 - i - 1);
                    if (!candidate.StartsWith("_"))     // "_usage" 등 메타 키 제외
                        currentId = candidate;
                    i = q2;
                }
            }
        }
        return result;
    }

    private static string GetRelativePath(Transform root, Transform target)
    {
        var parts = new List<string>();
        Transform cur = target;
        while (cur != null && cur != root)
        {
            parts.Insert(0, cur.name);
            cur = cur.parent;
        }
        return cur == root ? string.Join("/", parts) : null;
    }

    // ── 간단한 skeleton JSON 파서 ─────────────────────────────────
    // Newtonsoft 없이 정규식 없이 파싱하는 간이 버전
    private struct JointData { public string axis; public float minAngle, maxAngle; }

    private static Dictionary<string, JointData> ParseSkeletonJson(string json)
    {
        var result = new Dictionary<string, JointData>();

        // "joints" 배열 블록 추출
        int jointsStart = json.IndexOf("\"joints\"");
        if (jointsStart < 0) return result;
        int arrStart = json.IndexOf('[', jointsStart);
        if (arrStart < 0) return result;

        // 중괄호 매칭으로 각 오브젝트 추출
        int depth = 0, objStart = -1;
        for (int i = arrStart; i < json.Length; i++)
        {
            char c = json[i];
            if (c == '{')
            {
                if (depth == 0) objStart = i;
                depth++;
            }
            else if (c == '}')
            {
                depth--;
                if (depth == 0 && objStart >= 0)
                {
                    string obj = json.Substring(objStart, i - objStart + 1);
                    string id       = ExtractString(obj, "id");
                    string axis     = ExtractString(obj, "axis");
                    float  minAngle = ExtractFloat(obj, "min_angle");
                    float  maxAngle = ExtractFloat(obj, "max_angle");

                    if (!string.IsNullOrEmpty(id))
                        result[id] = new JointData
                        {
                            axis     = string.IsNullOrEmpty(axis) ? "X" : axis.ToUpper(),
                            minAngle = minAngle,
                            maxAngle = maxAngle
                        };
                    objStart = -1;
                }
            }
            else if (c == ']' && depth == 0)
                break;
        }

        return result;
    }

    private static string ExtractString(string obj, string key)
    {
        string search = $"\"{key}\"";
        int ki = obj.IndexOf(search);
        if (ki < 0) return null;
        int colon = obj.IndexOf(':', ki + search.Length);
        if (colon < 0) return null;
        int q1 = obj.IndexOf('"', colon + 1);
        if (q1 < 0) return null;
        int q2 = obj.IndexOf('"', q1 + 1);
        if (q2 < 0) return null;
        return obj.Substring(q1 + 1, q2 - q1 - 1);
    }

    private static float ExtractFloat(string obj, string key)
    {
        string search = $"\"{key}\"";
        int ki = obj.IndexOf(search);
        if (ki < 0) return 0f;
        int colon = obj.IndexOf(':', ki + search.Length);
        if (colon < 0) return 0f;

        int start = colon + 1;
        while (start < obj.Length && (obj[start] == ' ' || obj[start] == '\t' || obj[start] == '\n' || obj[start] == '\r'))
            start++;

        int end = start;
        while (end < obj.Length && (char.IsDigit(obj[end]) || obj[end] == '-' || obj[end] == '.' || obj[end] == '+'))
            end++;

        if (float.TryParse(obj.Substring(start, end - start),
                           System.Globalization.NumberStyles.Float,
                           System.Globalization.CultureInfo.InvariantCulture,
                           out float val))
            return val;
        return 0f;
    }
}
