// TripleModeSceneSetup.cs
// HandAvatar/Setup Triple Demo Scene 메뉴:
//   Direct / Keyframe / Blend 거미 세 마리를 나란히 배치하고
//   TripleModeClient 를 씬에 추가한다.
//
// 실행 후 해야 할 일:
//   각 거미 선택 → AnimalController Inspector → "JointEntry 전체 자동 생성" 클릭

using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.UI;
using TMPro;

public static class TripleModeSceneSetup
{
    private const string PREFAB_PATH      = "Assets/Spiders/Prefabs/spider_black.prefab";
    private const string CONTROLLER_PATH  = "Assets/Spiders/Animations/Spider.controller";
    private const string WALK_ANIM        = "Walk";

    private static readonly (string name, Vector3 pos, Color labelColor)[] _configs =
    {
        ("Spider_Direct",   new Vector3(-6f, 0f, 0f), new Color(0f,   0.78f, 1f)),
        ("Spider_Keyframe", new Vector3( 0f, 0f, 0f), new Color(0f,   1f,   0.39f)),
        ("Spider_Blend",    new Vector3( 6f, 0f, 0f), new Color(1f,   0.63f, 0f)),
    };

    [MenuItem("HandAvatar/Setup Triple Demo Scene")]
    public static void SetupTripleDemo()
    {
        // ── MainThreadDispatcher ──────────────────────────────────
        if (Object.FindAnyObjectByType<MainThreadDispatcher>() == null)
        {
            var disp = new GameObject("MainThreadDispatcher");
            disp.AddComponent<MainThreadDispatcher>();
            Undo.RegisterCreatedObjectUndo(disp, "Create MainThreadDispatcher");
        }

        // ── 프리팹 로드 ────────────────────────────────────────────
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(PREFAB_PATH);
        if (prefab == null)
        {
            EditorUtility.DisplayDialog("오류",
                $"프리팹을 찾을 수 없습니다:\n{PREFAB_PATH}", "OK");
            return;
        }
        var controller = AssetDatabase.LoadAssetAtPath<RuntimeAnimatorController>(CONTROLLER_PATH);

        var ctrlRefs = new AnimalController[3];

        // ── 거미 세 마리 배치 ─────────────────────────────────────
        for (int i = 0; i < _configs.Length; i++)
        {
            var (goName, pos, _) = _configs[i];

            // 기존 오브젝트 제거
            var existing = GameObject.Find(goName);
            if (existing != null)
                Undo.DestroyObjectImmediate(existing);

            var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
            go.name            = goName;
            go.transform.position = pos;
            go.transform.rotation = Quaternion.identity;
            Undo.RegisterCreatedObjectUndo(go, $"Create {goName}");

            // Animator controller — 없으면 루트에 추가
            var animator = go.GetComponentInChildren<Animator>(true);
            if (animator == null)
            {
                animator = Undo.AddComponent<Animator>(go);
                Debug.Log($"[TripleModeSceneSetup] Animator 추가: {goName}");
            }
            if (controller != null)
            {
                Undo.RecordObject(animator, "Set Animator Controller");
                animator.runtimeAnimatorController = controller;
                animator.enabled = false;
            }

            // AnimalController
            var ctrl = go.GetComponent<AnimalController>()
                    ?? Undo.AddComponent<AnimalController>(go);
            var so = new SerializedObject(ctrl);
            // Direct(i=0): Animator 없이 뼈 직접 (jd, 2x 부스트)
            // Keyframe(i=1): Walk/Attack 애니메이션 100% (blend=0)
            // Blend(i=2):   Walk 중 Animator 50% + hand 50% (true 50:50 blend)
            so.FindProperty("walkAnimName").stringValue          = (i == 0) ? "" : WALK_ANIM;
            so.FindProperty("idleAnimator").objectReferenceValue = animator;
            // Blend: walkHandBlend=0.65 → Walk 중 Animator 35% + hand 65% (애니메이션 강도 낮춤)
            // Keyframe: 둘 다 0 → 100% 순수 애니메이션
            so.FindProperty("walkHandBlend").floatValue          = (i == 2) ? 0.65f : 0f;
            so.FindProperty("triggerHandBlend").floatValue       = (i == 2) ? 0.5f : 0f;
            so.ApplyModifiedProperties();

            ctrlRefs[i] = ctrl;
        }

        // ── JointEntry 자동 생성 (세 마리 모두) ──────────────────────
        foreach (var ctrl in ctrlRefs)
            AutoGenerateJoints(ctrl, "spider");

        // ── TripleModeClient ───────────────────────────────────────
        var clientGo = GameObject.Find("TripleModeClient")
                    ?? new GameObject("TripleModeClient");
        Undo.RegisterCreatedObjectUndo(clientGo, "Create TripleModeClient");

        var client = clientGo.GetComponent<TripleModeClient>()
                  ?? Undo.AddComponent<TripleModeClient>(clientGo);
        var clientSO = new SerializedObject(client);
        clientSO.FindProperty("directCtrl").objectReferenceValue   = ctrlRefs[0];
        clientSO.FindProperty("keyframeCtrl").objectReferenceValue = ctrlRefs[1];
        clientSO.FindProperty("blendCtrl").objectReferenceValue    = ctrlRefs[2];
        clientSO.ApplyModifiedProperties();

        // ── 슬라이더 UI 잔재 제거 ────────────────────────────────────
        var oldDebugUI = clientGo.GetComponent("TripleModeDebugUI");
        if (oldDebugUI != null) Undo.DestroyObjectImmediate(oldDebugUI);

        // ── World Space 라벨 캔버스 ───────────────────────────────
        SetupLabels();

        // ── 카메라 위치 ───────────────────────────────────────────
        var cam = Camera.main;
        if (cam != null)
        {
            cam.transform.position = new Vector3(0f, 4f, -14f);
            cam.transform.rotation = Quaternion.Euler(10f, 0f, 0f);
            EditorUtility.SetDirty(cam);
        }

        // ── 씬 저장 ───────────────────────────────────────────────
        EditorSceneManager.MarkSceneDirty(
            UnityEngine.SceneManagement.SceneManager.GetActiveScene());

        EditorUtility.DisplayDialog(
            "Triple Demo 씬 세팅 완료",
            "세 마리 거미 + TripleModeClient 배치 완료!\n\n" +
            "다음 단계:\n" +
            "  1. 각 거미(Spider_Direct / _Keyframe / _Blend) 선택\n" +
            "  2. AnimalController Inspector\n" +
            "     → 동물 이름 = 'spider' 입력\n" +
            "     → 'JointEntry 전체 자동 생성' 클릭\n\n" +
            "실행:\n" +
            "  python scripts/demo_triple.py --animal spider\n" +
            "  Unity Play",
            "OK");
    }

    // ── JointEntry 자동 생성 ─────────────────────────────────────
    private static void AutoGenerateJoints(AnimalController ctrl, string animal)
    {
        string boneMapPath = Path.GetFullPath(
            Path.Combine(Application.dataPath,
                         $"../../python/data/animal_skeletons/bone_map_{animal}.json"));
        string mappingPath = Path.GetFullPath(
            Path.Combine(Application.dataPath,
                         $"../../python/data/mappings/{animal}_mapping.json"));

        if (!File.Exists(boneMapPath))
        {
            Debug.LogWarning($"[TripleModeSceneSetup] bone_map 없음: {boneMapPath}");
            return;
        }

        // bone_map 파싱: joint_id → unity_path
        var idToPath = ParseBoneMap(File.ReadAllText(boneMapPath));

        // 매핑 필터
        var allowed = new HashSet<string>();
        if (File.Exists(mappingPath))
            ParseMappingIds(File.ReadAllText(mappingPath), allowed);

        var so          = new SerializedObject(ctrl);
        var entriesProp = so.FindProperty("jointEntries");
        entriesProp.ClearArray();

        Transform root = ctrl.transform;
        int created = 0;

        foreach (var kv in idToPath)
        {
            string jointId   = kv.Key;
            string unityPath = kv.Value;

            if (allowed.Count > 0 && !allowed.Contains(jointId)) continue;

            Transform bone = root.Find(unityPath);
            if (bone == null)
            {
                int slash = unityPath.IndexOf('/');
                if (slash >= 0) bone = root.Find(unityPath.Substring(slash + 1));
            }

            entriesProp.InsertArrayElementAtIndex(entriesProp.arraySize);
            var entry = entriesProp.GetArrayElementAtIndex(entriesProp.arraySize - 1);
            entry.FindPropertyRelative("jointName").stringValue               = jointId;
            entry.FindPropertyRelative("jointTransform").objectReferenceValue = bone;
            entry.FindPropertyRelative("axisX").boolValue = true;
            entry.FindPropertyRelative("axisY").boolValue = true;
            entry.FindPropertyRelative("axisZ").boolValue = true;
            entry.FindPropertyRelative("minAngle").floatValue = -180f;
            entry.FindPropertyRelative("maxAngle").floatValue =  180f;
            // -1 = 전역 walkHandBlend 사용. 미설정 시 Unity 기본값 0 → jBlend=0 → blend 무효화
            entry.FindPropertyRelative("walkBlendOverride").floatValue = -1f;

            if (bone != null) created++;
            else Debug.LogWarning($"[TripleModeSceneSetup] Transform 없음: {jointId} ({unityPath}) on {ctrl.gameObject.name}");
        }

        so.FindProperty("autoInferAxes").boolValue = false;
        so.ApplyModifiedProperties();
        EditorUtility.SetDirty(ctrl);
        Debug.Log($"[TripleModeSceneSetup] {ctrl.gameObject.name}: JointEntry {created}개 생성");
    }

    private static Dictionary<string, string> ParseBoneMap(string json)
    {
        var result = new Dictionary<string, string>();
        int mapStart = json.IndexOf("\"joint_map\"");
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
                    if (!string.IsNullOrEmpty(unityPath))
                        result[currentId] = unityPath;
                    objStart  = -1;
                    currentId = null;
                }
                else if (depth == 0) break;
            }
            else if (c == '"' && depth == 1)
            {
                int q2 = json.IndexOf('"', i + 1);
                if (q2 > i) { currentId = json.Substring(i + 1, q2 - i - 1); i = q2; }
            }
        }
        return result;
    }

    private static void ParseMappingIds(string json, HashSet<string> ids)
    {
        int mStart = json.IndexOf("\"mapping\"");
        if (mStart < 0) return;
        int braceStart = json.IndexOf('{', mStart + 9);
        if (braceStart < 0) return;
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
    }

    private static string ExtractString(string json, string key)
    {
        int k = json.IndexOf($"\"{key}\"");
        if (k < 0) return null;
        int c = json.IndexOf(':', k);
        if (c < 0) return null;
        int q1 = json.IndexOf('"', c + 1);
        if (q1 < 0) return null;
        int q2 = json.IndexOf('"', q1 + 1);
        if (q2 < 0) return null;
        return json.Substring(q1 + 1, q2 - q1 - 1);
    }

    // ── World Space 라벨 (TextMeshPro 3D) ───────────────────────
    private static void SetupLabels()
    {
        var labelRoot = GameObject.Find("TripleLabels")
                     ?? new GameObject("TripleLabels");
        Undo.RegisterCreatedObjectUndo(labelRoot, "Create TripleLabels");

        for (int i = labelRoot.transform.childCount - 1; i >= 0; i--)
            Undo.DestroyObjectImmediate(labelRoot.transform.GetChild(i).gameObject);

        string[] names  = { "Direct", "Keyframe", "Blend" };
        Color[]  colors = {
            new Color(0f,   0.78f, 1f),
            new Color(0f,   1f,   0.39f),
            new Color(1f,   0.63f, 0f),
        };
        float[] xPos = { -6f, 0f, 6f };

        for (int i = 0; i < 3; i++)
        {
            var go = new GameObject($"Label_{names[i]}");
            go.transform.SetParent(labelRoot.transform);
            // 각 스파이더 위 3D 공간에 가로 배치
            go.transform.position = new Vector3(xPos[i], 3.2f, 0f);
            go.transform.rotation = Quaternion.identity;
            Undo.RegisterCreatedObjectUndo(go, $"Create Label {names[i]}");

            var tmp = go.AddComponent<TextMeshPro>();
            tmp.text      = names[i];
            tmp.color     = colors[i];
            tmp.fontSize  = 3f;           // 3 world-unit 크기 텍스트
            tmp.fontStyle = FontStyles.Bold;
            tmp.alignment = TextAlignmentOptions.Center;
        }
    }
}
