// AnimalControllerEditor.cs
// AnimalController Inspector에 버튼 추가.
//
// ① 전체 자동 생성 (bone_map.json)
//      bone_map_{animal}.json 을 읽어 JointEntry 리스트 전체를 한 번에 생성한다.
//      Transform.Find() 로 각 본을 자동으로 연결하고 skeleton.json 에서 ROM/축도 채운다.
//      → 새 동물 에셋을 넣을 때 이 버튼 하나로 AnimalController 설정 완료.
//
// 사용법 (신규 동물):
//   1. AnimPoseExporter 로 skeleton.json + bone_map_{animal}.json 생성
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

        EditorGUILayout.Space(4);
        EditorGUILayout.HelpBox(
            "bone_map_{animal}.json + skeleton.json 을 읽어\n" +
            "JointEntry 리스트 전체를 자동 생성합니다.\n" +
            "Transform.Find() 로 본을 자동 연결하고 축/ROM 도 채웁니다.\n\n" +
            "⚠ 기존 리스트가 모두 지워집니다.",
            MessageType.Info);

        EditorGUI.BeginDisabledGroup(!boneMapExists);
        if (GUILayout.Button("JointEntry 전체 자동 생성 (bone_map.json)", GUILayout.Height(35)))
        {
            string bmpRaw   = File.ReadAllText(boneMapPath);
            var    idToInfo = ParseBoneMapFull(bmpRaw);

            // 매핑 JSON 에 있는 관절만 남기기
            string mappingPath = ResolveMappingPath(_animal);
            if (File.Exists(mappingPath))
            {
                var allowed = ParseMappingJointIds(File.ReadAllText(mappingPath));
                var filtered = new Dictionary<string, BoneMapEntry>();
                foreach (var kv in idToInfo)
                    if (allowed.Contains(kv.Key)) filtered[kv.Key] = kv.Value;
                idToInfo = filtered;
                Debug.Log($"[AnimalControllerEditor] 매핑 필터: {idToInfo.Count}개 관절");
            }

            var    reviewEntries = BuildSideReviewEntries(idToInfo, jsonExists ? jsonPath : null);
            var    capturedInfo  = idToInfo;
            string finalSkel     = jsonExists ? jsonPath : null;

            ModelReviewWindow.Open(_animal, reviewEntries, included =>
            {
                // included 리스트 기준으로 idToInfo 재필터
                var filteredInfo = new Dictionary<string, BoneMapEntry>();
                foreach (var e in included)
                    if (capturedInfo.TryGetValue(e.jointId, out var bme))
                        filteredInfo[e.jointId] = bme;
                AutoGenerateEntriesFromMap(filteredInfo, finalSkel);
            });
        }
        EditorGUI.EndDisabledGroup();

        if (!boneMapExists)
            EditorGUILayout.HelpBox("bone_map 없음: " + boneMapPath +
                                    "\n먼저 AnimPoseExporter 실행", MessageType.Warning);
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

        // 매핑 JSON 에 있는 관절만 포함
        var mappingPath = Path.GetFullPath(
            Path.Combine(Application.dataPath,
                         $"../../python/data/mappings/{_animal}_mapping.json"));
        var allowedJoints = new HashSet<string>();
        if (File.Exists(mappingPath))
        {
            string mapRaw = File.ReadAllText(mappingPath);
            // "mapping" 키 아래 joint_id 추출 (간이 파싱)
            int mStart = mapRaw.IndexOf("\"mapping\"");
            int mBrace = mapRaw.IndexOf('{', mStart + 9);
            int depth2 = 0; int pos = mBrace;
            while (pos < mapRaw.Length)
            {
                if (mapRaw[pos] == '{') depth2++;
                else if (mapRaw[pos] == '}') { depth2--; if (depth2 == 0) break; }
                else if (mapRaw[pos] == '"' && depth2 == 1)
                {
                    int q2 = mapRaw.IndexOf('"', pos + 1);
                    if (q2 > pos) { allowedJoints.Add(mapRaw.Substring(pos + 1, q2 - pos - 1)); pos = q2; }
                }
                pos++;
            }
            Debug.Log($"[AnimalControllerEditor] 매핑 기반 필터: {allowedJoints.Count}개 관절");
        }

        int created = 0, notFound = 0;
        var missing = new List<string>();

        foreach (var kv in idToInfo)
        {
            string jointId  = kv.Key;
            // 매핑에 없는 관절은 스킵
            if (allowedJoints.Count > 0 && !allowedJoints.Contains(jointId))
                continue;

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

    // idToInfo 를 직접 받아 JointEntry 생성 (필터링 후 호출)
    private void AutoGenerateEntriesFromMap(
        Dictionary<string, BoneMapEntry> idToInfo,
        string skeletonPath)
    {
        if (idToInfo.Count == 0)
        {
            EditorUtility.DisplayDialog("오류", "생성할 관절이 없습니다.", "OK");
            return;
        }

        Dictionary<string, JointData> skelData = null;
        if (skeletonPath != null && File.Exists(skeletonPath))
            skelData = ParseSkeletonJson(File.ReadAllText(skeletonPath));

        var ctrl       = (AnimalController)target;
        Transform root = ctrl.transform;

        SerializedObject   so          = new SerializedObject(target);
        SerializedProperty entriesProp = so.FindProperty("jointEntries");
        entriesProp.ClearArray();

        int created = 0, notFound = 0;
        var missing = new List<string>();

        foreach (var kv in idToInfo)
        {
            string jointId   = kv.Key;
            string unityPath = kv.Value.unityPath;
            string axisHint  = kv.Value.axis;

            Transform bone = root.Find(unityPath);
            if (bone == null)
            {
                int slash = unityPath.IndexOf('/');
                if (slash >= 0) bone = root.Find(unityPath.Substring(slash + 1));
            }

            bool  axX = false, axY = false, axZ = false;
            float minA = -45f, maxA = 45f;

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

            entriesProp.InsertArrayElementAtIndex(entriesProp.arraySize);
            SerializedProperty entry = entriesProp.GetArrayElementAtIndex(entriesProp.arraySize - 1);
            entry.FindPropertyRelative("jointName").stringValue                = jointId;
            entry.FindPropertyRelative("jointTransform").objectReferenceValue  = bone;
            entry.FindPropertyRelative("axisX").boolValue                      = axX;
            entry.FindPropertyRelative("axisY").boolValue                      = axY;
            entry.FindPropertyRelative("axisZ").boolValue                      = axZ;
            entry.FindPropertyRelative("minAngle").floatValue                  = minA;
            entry.FindPropertyRelative("maxAngle").floatValue                  = maxA;

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

    private static string ResolveMappingPath(string animal)
    {
        return Path.GetFullPath(
            Path.Combine(Application.dataPath,
                         $"../../python/data/mappings/{animal}_mapping.json"));
    }

    private static HashSet<string> ParseMappingJointIds(string json)
    {
        var ids = new HashSet<string>();
        int mStart = json.IndexOf("\"mapping\"");
        if (mStart < 0) return ids;
        int braceStart = json.IndexOf('{', mStart + 9);
        if (braceStart < 0) return ids;

        int depth = 0, pos = braceStart;
        while (pos < json.Length)
        {
            char c = json[pos];
            if      (c == '{') depth++;
            else if (c == '}') { depth--; if (depth == 0) break; }
            else if (c == '"' && depth == 1)
            {
                int q2 = json.IndexOf('"', pos + 1);
                if (q2 > pos) { ids.Add(json.Substring(pos + 1, q2 - pos - 1)); pos = q2; }
            }
            pos++;
        }
        return ids;
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

    // ── 관절 좌/우 + 체인 루트 손가락 자동 감지 → 검수 엔트리 생성 ───
    private static List<ModelReviewWindow.JointSideEntry> BuildSideReviewEntries(
        Dictionary<string, BoneMapEntry> idToInfo,
        string skeletonPath)
    {
        // skeleton.json 의 chains 파싱 → chain root ID 집합
        var chainRoots = new HashSet<string>();
        if (skeletonPath != null && File.Exists(skeletonPath))
            chainRoots = ParseChainRoots(File.ReadAllText(skeletonPath));

        var entries = new List<ModelReviewWindow.JointSideEntry>();
        foreach (var kv in idToInfo)
        {
            string jid    = kv.Key;
            string jidLow = jid.ToLower();
            string detected;

            if (jidLow.StartsWith("l_") || jidLow.EndsWith("_l") || jidLow.Contains("left"))
                detected = "left";
            else if (jidLow.StartsWith("r_") || jidLow.EndsWith("_r") || jidLow.Contains("right"))
                detected = "right";
            else
                detected = "center";

            // 체인 루트이면 손가락 자동 감지
            string detectedFinger = chainRoots.Contains(jid)
                ? ModelReviewWindow.DetectFinger(jid)
                : null;

            entries.Add(new ModelReviewWindow.JointSideEntry
            {
                jointId        = jid,
                detectedSide   = detected,
                overrideSide   = null,
                detectedFinger = detectedFinger,
                overrideFinger = null,
            });
        }
        return entries;
    }

    // skeleton.json "chains" 에서 각 체인의 첫 번째 관절 ID 집합을 추출
    private static HashSet<string> ParseChainRoots(string json)
    {
        var roots   = new HashSet<string>();
        int chainsStart = json.IndexOf("\"chains\"");
        if (chainsStart < 0) return roots;
        int arrStart = json.IndexOf('[', chainsStart);
        if (arrStart < 0) return roots;

        // "chains": [ [ "root", ...], [...] ]
        // 각 내부 배열의 첫 번째 문자열을 추출
        int depth = 0;
        bool inInner = false;
        for (int i = arrStart; i < json.Length; i++)
        {
            char c = json[i];
            if (c == '[') { depth++; if (depth == 2) inInner = true; }
            else if (c == ']') { depth--; inInner = false; if (depth == 0) break; }
            else if (inInner && c == '"')
            {
                int q2 = json.IndexOf('"', i + 1);
                if (q2 > i)
                {
                    roots.Add(json.Substring(i + 1, q2 - i - 1));
                    inInner = false; // 이 내부 배열의 첫 번째만
                    i = q2;
                }
            }
        }
        return roots;
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
