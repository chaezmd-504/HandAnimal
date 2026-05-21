// AnimPoseExporter.cs
// 에디터 전용 — FBX 하나로 skeleton.json + bone_map + poses.json 전부 자동 생성.
//
// 메뉴: HandAvatar > Export Animation Poses
//
// 동작 순서:
//   1. FBX AnimationClip 전체 로드
//   2. 모든 프레임에서 회전 샘플링 → 실제로 움직이는 bone만 추출
//   3. 축별 분산 계산 → 주 회전축 자동 감지
//   4. 키프레임 전체에서 min/max → ROM 자동 측정
//   5. skeleton.json, bone_map_{animal}.json, {animal}_poses.json 저장

using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEngine;

public class AnimPoseExporter : EditorWindow
{
    // ──────────────────────────────────────────────────────────
    // 설정
    // ──────────────────────────────────────────────────────────

    private string _animalName     = "butterfly";
    private string _fbxPath        = "";
    private string _animFolder     = "";   // 별도 .anim 파일 폴더 (선택)
    private string _outputDir      = "";
    private int    _samplesPerClip = 15;
    private string _restClipName   = "Idle";
    private float  _minVarianceDeg = 3f;   // 이보다 움직임 적은 bone 제외
    private float  _marginDeg      = 5f;   // ROM 마진
    private bool   _bilateral      = true; // 디자이너 지정: 양손(true) / 한 손(false)
    private string _log            = "";

    // ──────────────────────────────────────────────────────────
    // 메뉴 진입점
    // ──────────────────────────────────────────────────────────

    [MenuItem("HandAvatar/Export Animation Poses")]
    public static void ShowWindow()
    {
        var w = GetWindow<AnimPoseExporter>("Anim Pose Exporter");
        w.minSize = new Vector2(520, 480);
        w._outputDir = Path.GetFullPath(
            Path.Combine(Application.dataPath, "../../python/data/animal_skeletons"));
    }

    // ──────────────────────────────────────────────────────────
    // GUI
    // ──────────────────────────────────────────────────────────

    private void OnGUI()
    {
        GUILayout.Label("Animation Pose Exporter  (완전 자동)", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox(
            "FBX 하나로 skeleton.json + bone_map + poses.json 전부 자동 생성합니다.",
            MessageType.Info);
        EditorGUILayout.Space();

        _animalName     = EditorGUILayout.TextField("동물 이름", _animalName);
        _restClipName   = EditorGUILayout.TextField("Rest 클립 이름 (없으면 첫 클립)", _restClipName);
        _samplesPerClip = EditorGUILayout.IntSlider("클립당 샘플 수", _samplesPerClip, 5, 60);
        _minVarianceDeg = EditorGUILayout.FloatField("최소 움직임 (deg) — 이하 bone 제외", _minVarianceDeg);
        _marginDeg      = EditorGUILayout.FloatField("ROM 마진 (deg)", _marginDeg);

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("별도 .anim 파일 (선택)", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox(
            "FBX 외부에 .anim 파일이 따로 있으면 폴더를 지정하세요.\n" +
            "해당 폴더의 모든 .anim 클립을 FBX 클립과 합산해 축/ROM 분석합니다.",
            MessageType.None);
        EditorGUILayout.BeginHorizontal();
        _animFolder = EditorGUILayout.TextField(".anim 폴더 (Assets/...)", _animFolder);
        if (GUILayout.Button("찾기", GUILayout.Width(50)))
        {
            string abs = EditorUtility.OpenFolderPanel(".anim 폴더 선택", Application.dataPath, "");
            if (!string.IsNullOrEmpty(abs))
                _animFolder = "Assets" + abs.Substring(Application.dataPath.Length);
        }
        if (GUILayout.Button("지우기", GUILayout.Width(50)))
            _animFolder = "";
        EditorGUILayout.EndHorizontal();
        _bilateral      = EditorGUILayout.Toggle(
            new GUIContent("양손 매핑 (Bilateral)",
                "체크: 양손을 사용해 동물을 조종 (관절이 많을 때 권장)\n" +
                "해제: 한 손만 사용 (단순한 동물, 또는 한 손으로 충분한 경우)"),
            _bilateral);
        EditorGUILayout.Space();

        EditorGUILayout.BeginHorizontal();
        _fbxPath = EditorGUILayout.TextField("FBX 경로 (Assets/...)", _fbxPath);
        if (GUILayout.Button("찾기", GUILayout.Width(50)))
        {
            string abs = EditorUtility.OpenFilePanel("FBX 선택", Application.dataPath, "fbx,FBX");
            if (!string.IsNullOrEmpty(abs))
                _fbxPath = "Assets" + abs.Substring(Application.dataPath.Length);
        }
        EditorGUILayout.EndHorizontal();

        EditorGUILayout.BeginHorizontal();
        _outputDir = EditorGUILayout.TextField("출력 폴더", _outputDir);
        if (GUILayout.Button("찾기", GUILayout.Width(50)))
        {
            string abs = EditorUtility.OpenFolderPanel("출력 폴더", _outputDir, "");
            if (!string.IsNullOrEmpty(abs)) _outputDir = abs;
        }
        EditorGUILayout.EndHorizontal();

        EditorGUILayout.Space();
        GUI.backgroundColor = new Color(0.4f, 0.9f, 0.4f);
        if (GUILayout.Button("▶  Export All (skeleton + bone_map + poses)", GUILayout.Height(40)))
            RunExport();
        GUI.backgroundColor = Color.white;

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("로그", EditorStyles.boldLabel);
        EditorGUILayout.TextArea(_log, GUILayout.Height(200));
    }

    // ──────────────────────────────────────────────────────────
    // 핵심 로직
    // ──────────────────────────────────────────────────────────

    private void RunExport()
    {
        _log = "";

        // ── 1. FBX 로드 ───────────────────────────────────────
        var clips = LoadClipsFromFbx(_fbxPath);

        GameObject prefabRoot = AssetDatabase.LoadAssetAtPath<GameObject>(_fbxPath);
        if (prefabRoot == null) { Log($"[ERROR] FBX Prefab 로드 실패: {_fbxPath}"); return; }

        Log($"[OK] FBX 로드: {prefabRoot.name}  |  FBX 내 클립 {clips.Count}개" +
            (clips.Count > 0 ? $": {string.Join(", ", clips.Keys)}" : " (없음 — .anim 폴더에서 로드)"));

        // ── 1b. 별도 .anim 파일 로드 (폴더 지정 시) ─────────────
        if (!string.IsNullOrEmpty(_animFolder))
        {
            var extraClips = LoadClipsFromFolder(_animFolder);
            foreach (var kv in extraClips)
            {
                if (!clips.ContainsKey(kv.Key))
                    clips[kv.Key] = kv.Value;
            }
            Log($"[OK] .anim 폴더 추가: {extraClips.Count}개 → 총 {clips.Count}개 클립: {string.Join(", ", clips.Keys)}");
        }

        if (clips.Count == 0)
        {
            Log($"[ERROR] 클립 없음. FBX에 임베드된 애니메이션이 없으면 '.anim 폴더'를 지정하세요.");
            return;
        }

        // ── 2. temp 오브젝트 + rest pose 기록 ────────────────
        GameObject go = (GameObject)PrefabUtility.InstantiatePrefab(prefabRoot);
        go.hideFlags = HideFlags.HideAndDontSave;

        AnimationClip restClip = FindRestClip(clips);
        restClip.SampleAnimation(go, 0f);
        var restEulers = RecordAllEulers(go);   // path → Vector3
        Log($"[OK] Rest pose 기록 ({restEulers.Count}개 bone)");

        // ── 3. 모든 클립 × 모든 프레임 샘플링 ────────────────
        // bone path → 각 axis(X/Y/Z) 의 angle 목록
        var anglesByBone = new Dictionary<string, List<Vector3>>();

        foreach (var kv in clips)
        {
            float dur = kv.Value.length;
            if (dur <= 0f) continue;

            for (int i = 0; i < _samplesPerClip; i++)
            {
                float t = dur * i / Mathf.Max(1, _samplesPerClip - 1);
                kv.Value.SampleAnimation(go, t);

                // 현재 프레임의 모든 bone 오일러 읽기
                CollectEulers(go, restEulers, anglesByBone);
            }
        }

        DestroyImmediate(go);
        Log($"[OK] 샘플링 완료: {anglesByBone.Count}개 bone");

        // ── 4. 움직이는 bone 필터 + 주 축 감지 + ROM 측정 ───
        var jointInfos = new List<JointInfo>();

        foreach (var kv in anglesByBone)
        {
            string bonePath = kv.Key;
            var angles = kv.Value;  // List<Vector3> delta angles

            float varX = Variance(angles, 0);
            float varY = Variance(angles, 1);
            float varZ = Variance(angles, 2);
            float maxVar = Mathf.Max(varX, varY, varZ);

            // 최소 움직임 미만 bone 제외
            if (Mathf.Sqrt(maxVar) < _minVarianceDeg) continue;

            string axis = varX >= varY && varX >= varZ ? "X"
                        : varY >= varZ                 ? "Y"
                                                       : "Z";
            int axIdx = axis == "X" ? 0 : axis == "Y" ? 1 : 2;

            float minA = float.MaxValue, maxA = float.MinValue;
            foreach (var v in angles)
            {
                float a = axIdx == 0 ? v.x : axIdx == 1 ? v.y : v.z;
                if (a < minA) minA = a;
                if (a > maxA) maxA = a;
            }
            minA -= _marginDeg;
            maxA += _marginDeg;

            string jointId = BonePathToJointId(bonePath);
            jointInfos.Add(new JointInfo
            {
                jointId    = jointId,
                unityPath  = bonePath,
                axis       = axis,
                minAngle   = Mathf.Round(minA * 10f) / 10f,
                maxAngle   = Mathf.Round(maxA * 10f) / 10f,
                stdDev     = Mathf.Round(Mathf.Sqrt(maxVar) * 10f) / 10f,
            });
        }

        // 계층 순서 정렬 (path 깊이 → 알파벳)
        jointInfos.Sort((a, b) => {
            int da = a.unityPath.Split('/').Length;
            int db = b.unityPath.Split('/').Length;
            return da != db ? da.CompareTo(db) : string.Compare(a.unityPath, b.unityPath, StringComparison.Ordinal);
        });

        Log($"[OK] 유효 관절: {jointInfos.Count}개 (분산 >= {_minVarianceDeg}°)");
        foreach (var j in jointInfos)
            Log($"     {j.jointId:30} axis={j.axis}  ROM=[{j.minAngle:F1}, {j.maxAngle:F1}]  stddev={j.stdDev:F1}°");

        // ── 5. skeleton.json 저장 ─────────────────────────────
        Directory.CreateDirectory(_outputDir);
        string skelPath = Path.Combine(_outputDir, $"{_animalName}.json");
        File.WriteAllText(skelPath, BuildSkeletonJson(jointInfos), new UTF8Encoding(false));
        Log($"\n[OK] skeleton.json 저장: {skelPath}");

        // ── 6. bone_map.json 저장 ─────────────────────────────
        string boneMapPath = Path.Combine(_outputDir, $"bone_map_{_animalName}.json");
        File.WriteAllText(boneMapPath, BuildBoneMapJson(jointInfos, prefabRoot.name), new UTF8Encoding(false));
        Log($"[OK] bone_map 저장: {boneMapPath}");

        // ── 7. poses.json 저장 ────────────────────────────────
        // rest 기준 delta 포즈 재샘플링 (이번엔 jointInfos 기준)
        go = (GameObject)PrefabUtility.InstantiatePrefab(prefabRoot);
        go.hideFlags = HideFlags.HideAndDontSave;
        restClip.SampleAnimation(go, 0f);
        var restEulers2 = RecordAllEulers(go);

        var allPoses = new List<Dictionary<string, float>>();
        foreach (var kv in clips)
        {
            float dur = kv.Value.length;
            if (dur <= 0f) continue;
            for (int i = 0; i < _samplesPerClip; i++)
            {
                float t = dur * i / Mathf.Max(1, _samplesPerClip - 1);
                kv.Value.SampleAnimation(go, t);
                var pose = SamplePose(go, jointInfos, restEulers2);
                allPoses.Add(pose);
            }
        }
        DestroyImmediate(go);

        var unique = DeduplicatePoses(allPoses);
        string posesPath = Path.Combine(_outputDir, $"{_animalName}_poses.json");
        File.WriteAllText(posesPath, PosesToJson(unique), new UTF8Encoding(false));
        Log($"[OK] poses.json 저장: {posesPath}  ({unique.Count}개 고유 포즈)");
        Log($"\n★ 완료! 이후: python scripts/generate_mappings.py");
    }

    // ──────────────────────────────────────────────────────────
    // 샘플링 헬퍼
    // ──────────────────────────────────────────────────────────

    /// 현재 씬 오브젝트의 모든 Transform path → 로컬 오일러 기록
    private Dictionary<string, Vector3> RecordAllEulers(GameObject root)
    {
        var result = new Dictionary<string, Vector3>();
        foreach (var t in root.GetComponentsInChildren<Transform>())
        {
            if (t == root.transform) continue;
            string path = GetRelativePath(root.transform, t);
            result[path] = NormalizeEuler(t.localEulerAngles);
        }
        return result;
    }

    /// 현재 프레임의 delta 오일러를 anglesByBone에 누적
    private void CollectEulers(
        GameObject root,
        Dictionary<string, Vector3> restEulers,
        Dictionary<string, List<Vector3>> anglesByBone)
    {
        foreach (var t in root.GetComponentsInChildren<Transform>())
        {
            if (t == root.transform) continue;
            string path = GetRelativePath(root.transform, t);
            if (!restEulers.TryGetValue(path, out Vector3 rest)) continue;
            Vector3 cur   = NormalizeEuler(t.localEulerAngles);
            Vector3 delta = NormalizeEuler(cur - rest);
            if (!anglesByBone.ContainsKey(path))
                anglesByBone[path] = new List<Vector3>();
            anglesByBone[path].Add(delta);
        }
    }

    /// jointInfos 기준으로 현재 프레임 포즈 dict 반환
    private Dictionary<string, float> SamplePose(
        GameObject root,
        List<JointInfo> joints,
        Dictionary<string, Vector3> restEulers)
    {
        var result = new Dictionary<string, float>();
        foreach (var j in joints)
        {
            string bonePath = StripRoot(j.unityPath, root.name);
            Transform bone  = root.transform.Find(bonePath)
                           ?? root.transform.Find(j.unityPath);
            if (bone == null) { result[j.jointId] = 0f; continue; }

            if (!restEulers.TryGetValue(j.unityPath, out Vector3 rest))
                rest = Vector3.zero;

            Vector3 cur   = NormalizeEuler(bone.localEulerAngles);
            Vector3 delta = NormalizeEuler(cur - rest);
            result[j.jointId] = Mathf.Round(GetAxis(delta, j.axis) * 100f) / 100f;
        }
        return result;
    }

    // ──────────────────────────────────────────────────────────
    // JSON 빌더
    // ──────────────────────────────────────────────────────────

    private string BuildSkeletonJson(List<JointInfo> joints)
    {
        var sb = new StringBuilder();
        sb.AppendLine("{");
        sb.AppendLine($"  \"animal_name\": \"{_animalName}\",");
        sb.AppendLine($"  \"description\": \"{_animalName} — auto-generated by AnimPoseExporter\",");
        sb.AppendLine($"  \"bilateral\": {(_bilateral ? "true" : "false")},");
        sb.AppendLine("  \"joints\": [");
        for (int i = 0; i < joints.Count; i++)
        {
            var j = joints[i];
            string comma = i < joints.Count - 1 ? "," : "";
            sb.AppendLine("    {");
            sb.AppendLine($"      \"id\": \"{j.jointId}\",");
            sb.AppendLine($"      \"parent\": \"body\",");
            sb.AppendLine($"      \"dof\": 1,");
            sb.AppendLine($"      \"min_angle\": {j.minAngle:F1},");
            sb.AppendLine($"      \"max_angle\": {j.maxAngle:F1},");
            sb.AppendLine($"      \"axis\": \"{j.axis}\",");
            sb.AppendLine($"      \"rest\": 0.0");
            sb.AppendLine($"    }}{comma}");
        }
        sb.AppendLine("  ],");
        sb.AppendLine("  \"chains\": []");
        sb.Append("}");
        return sb.ToString();
    }

    private string BuildBoneMapJson(List<JointInfo> joints, string fbxRootName)
    {
        var sb = new StringBuilder();
        sb.AppendLine("{");
        sb.AppendLine($"  \"_usage\": \"auto-generated by AnimPoseExporter. unity_path 검토 권장.\",");
        sb.AppendLine($"  \"animal\": \"{_animalName}\",");
        sb.AppendLine($"  \"rest_anim\": \"{_restClipName}\",");
        sb.AppendLine($"  \"margin_deg\": {_marginDeg:F1},");
        sb.AppendLine("  \"joint_map\": {");
        for (int i = 0; i < joints.Count; i++)
        {
            var j = joints[i];
            string comma = i < joints.Count - 1 ? "," : "";
            sb.AppendLine($"    \"{j.jointId}\": {{");
            sb.AppendLine($"      \"unity_path\": \"{j.unityPath}\",");
            sb.AppendLine($"      \"axis\": \"{j.axis}\"");
            sb.AppendLine($"    }}{comma}");
        }
        sb.AppendLine("  }");
        sb.Append("}");
        return sb.ToString();
    }

    private string PosesToJson(List<Dictionary<string, float>> poses)
    {
        var sb = new StringBuilder();
        sb.AppendLine("[");
        for (int i = 0; i < poses.Count; i++)
        {
            sb.Append("  {");
            bool first = true;
            foreach (var kv in poses[i])
            {
                if (!first) sb.Append(", ");
                sb.Append($"\"{kv.Key}\": {kv.Value:F2}");
                first = false;
            }
            sb.Append(i < poses.Count - 1 ? "},\n" : "}\n");
        }
        sb.Append("]");
        return sb.ToString();
    }

    // ──────────────────────────────────────────────────────────
    // 유틸리티
    // ──────────────────────────────────────────────────────────

    private struct JointInfo
    {
        public string jointId;
        public string unityPath;
        public string axis;
        public float  minAngle;
        public float  maxAngle;
        public float  stdDev;
    }

    private Dictionary<string, AnimationClip> LoadClipsFromFolder(string folderPath)
    {
        var result = new Dictionary<string, AnimationClip>(StringComparer.OrdinalIgnoreCase);
        if (string.IsNullOrEmpty(folderPath) || !AssetDatabase.IsValidFolder(folderPath))
        {
            Log($"[WARN] .anim 폴더 없음 또는 잘못된 경로: {folderPath}");
            return result;
        }

        string[] guids = AssetDatabase.FindAssets("t:AnimationClip", new[] { folderPath });
        foreach (string guid in guids)
        {
            string path = AssetDatabase.GUIDToAssetPath(guid);
            var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
            if (clip != null && !clip.name.StartsWith("__"))
                result[clip.name] = clip;
        }
        return result;
    }

    private Dictionary<string, AnimationClip> LoadClipsFromFbx(string fbxPath)
    {
        var result = new Dictionary<string, AnimationClip>(StringComparer.OrdinalIgnoreCase);
        foreach (var asset in AssetDatabase.LoadAllAssetsAtPath(fbxPath))
            if (asset is AnimationClip clip && !clip.name.StartsWith("__"))
                result[clip.name] = clip;
        return result;
    }

    private AnimationClip FindRestClip(Dictionary<string, AnimationClip> clips)
    {
        foreach (var kv in clips)
            if (kv.Key.IndexOf(_restClipName, StringComparison.OrdinalIgnoreCase) >= 0)
                return kv.Value;
        return new List<AnimationClip>(clips.Values)[0];
    }

    private string GetRelativePath(Transform root, Transform target)
    {
        var parts = new List<string>();
        Transform cur = target;
        while (cur != null && cur != root)
        {
            parts.Insert(0, cur.name);
            cur = cur.parent;
        }
        return string.Join("/", parts);
    }

    /// bone path leaf 이름을 snake_case joint ID로 변환
    private string BonePathToJointId(string bonePath)
    {
        string leaf = bonePath.Split('/')[^1];   // 마지막 컴포넌트
        // 특수문자 제거, 공백→_, 소문자
        leaf = Regex.Replace(leaf, @"[^\w]", "_");
        leaf = Regex.Replace(leaf, @"_+", "_").Trim('_').ToLower();
        return leaf;
    }

    private string StripRoot(string path, string rootName)
    {
        return path.StartsWith(rootName + "/") ? path.Substring(rootName.Length + 1) : path;
    }

    private Vector3 NormalizeEuler(Vector3 e)
        => new Vector3(Norm(e.x), Norm(e.y), Norm(e.z));

    private float Norm(float a)
    {
        while (a >  180f) a -= 360f;
        while (a < -180f) a += 360f;
        return a;
    }

    private float GetAxis(Vector3 v, string axis)
        => axis == "X" ? v.x : axis == "Y" ? v.y : v.z;

    private float Variance(List<Vector3> list, int axis)
    {
        if (list.Count == 0) return 0f;
        float mean = 0f;
        foreach (var v in list) mean += axis == 0 ? v.x : axis == 1 ? v.y : v.z;
        mean /= list.Count;
        float var = 0f;
        foreach (var v in list)
        {
            float d = (axis == 0 ? v.x : axis == 1 ? v.y : v.z) - mean;
            var += d * d;
        }
        return var / list.Count;
    }

    private List<Dictionary<string, float>> DeduplicatePoses(List<Dictionary<string, float>> poses)
    {
        var seen   = new HashSet<string>();
        var result = new List<Dictionary<string, float>>();
        foreach (var p in poses)
        {
            var sb = new StringBuilder();
            foreach (var kv in p) sb.Append($"{kv.Key}:{kv.Value:F1};");
            if (seen.Add(sb.ToString())) result.Add(p);
        }
        return result;
    }

    private void Log(string msg)
    {
        _log += msg + "\n";
        Debug.Log("[AnimPoseExporter] " + msg);
        Repaint();
    }
}
