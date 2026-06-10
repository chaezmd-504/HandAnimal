// GazeSceneSetup.cs
// HandAvatar > Setup Gaze Test Scene 메뉴 실행 시
// 현재 씬에 Gaze 테스트용 오브젝트를 자동 세팅한다.
//
// 사용 방법:
//   1. File > New Scene 으로 빈 씬 생성 (동물 씬과 분리)
//   2. HandAvatar > Setup Gaze Test Scene 실행
//   3. python scripts/gaze_sender.py
//   4. Unity Play → Unity 화면 안에서 9포인트 캘리브레이션 진행

using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class GazeSceneSetup
{
    [MenuItem("HandAvatar/Setup Gaze Test Scene")]
    public static void SetupGazeTestScene()
    {
        // ── 1. Ground Plane ───────────────────────────────────────────────────
        var plane = GetOrCreate("Ground_Plane", () =>
            GameObject.CreatePrimitive(PrimitiveType.Plane));
        plane.transform.position   = Vector3.zero;
        plane.transform.localScale = new Vector3(20f, 1f, 20f);  // 200m × 200m
        SetColor(plane, new Color(0.22f, 0.22f, 0.28f));          // 진회색

        // ── 2. 랜드마크 기둥 4개 (방향 감각용) ──────────────────────────────
        CreateLandmarkPillar("Pillar_NE", new Vector3( 40f, 0f,  40f), new Color(1.0f, 0.3f, 0.3f)); // 빨강
        CreateLandmarkPillar("Pillar_NW", new Vector3(-40f, 0f,  40f), new Color(0.3f, 0.8f, 1.0f)); // 파랑
        CreateLandmarkPillar("Pillar_SE", new Vector3( 40f, 0f, -40f), new Color(0.3f, 1.0f, 0.4f)); // 초록
        CreateLandmarkPillar("Pillar_SW", new Vector3(-40f, 0f, -40f), new Color(1.0f, 0.9f, 0.2f)); // 노랑

        // ── 3. GazeTarget (Sphere + 전방 막대) ──────────────────────────────
        var sphere = GetOrCreate("GazeTarget", () =>
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Sphere);

            var rod = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            rod.name = "ForwardRod";
            rod.transform.SetParent(go.transform, false);
            rod.transform.localPosition = new Vector3(0f, 0f, 1.3f);
            rod.transform.localRotation = Quaternion.Euler(90f, 0f, 0f);
            rod.transform.localScale    = new Vector3(0.15f, 0.6f, 0.15f);
            Object.DestroyImmediate(rod.GetComponent<Collider>());
            SetColor(rod, new Color(1f, 0.2f, 0.2f));  // 빨강 = 전방

            return go;
        });
        sphere.name                = "GazeTarget";
        sphere.transform.position  = new Vector3(0f, 0.5f, 0f);
        sphere.transform.rotation  = Quaternion.identity;
        SetColor(sphere, Color.white);

        // ── 4. MainThreadDispatcher ───────────────────────────────────────────
        GetOrCreate("MainThreadDispatcher", () =>
        {
            var go = new GameObject("MainThreadDispatcher");
            go.AddComponent<MainThreadDispatcher>();
            return go;
        });

        // ── 5. GazeSystem ────────────────────────────────────────────────────
        var gazeSystem = GetOrCreate("GazeSystem", () => new GameObject("GazeSystem"));

        var receiver   = gazeSystem.GetComponent<GazeReceiver>()
                      ?? Undo.AddComponent<GazeReceiver>(gazeSystem);
        var navigator  = gazeSystem.GetComponent<GazeNavigator>()
                      ?? Undo.AddComponent<GazeNavigator>(gazeSystem);
        var calibrator = gazeSystem.GetComponent<GazeCalibrator>()
                      ?? Undo.AddComponent<GazeCalibrator>(gazeSystem);

        // GazeNavigator.targetObject = Sphere
        {
            var so = new SerializedObject(navigator);
            so.FindProperty("targetObject").objectReferenceValue = sphere.transform;
            so.ApplyModifiedProperties();
        }

        // GazeReceiver.gazeNavigator = navigator
        {
            var so = new SerializedObject(receiver);
            so.FindProperty("gazeNavigator").objectReferenceValue = navigator;
            so.ApplyModifiedProperties();
        }

        // GazeCalibrator.gazeReceiver = receiver
        {
            var so = new SerializedObject(calibrator);
            so.FindProperty("gazeReceiver").objectReferenceValue = receiver;
            so.ApplyModifiedProperties();
        }

        // ── 6. Main Camera + FollowCamera ─────────────────────────────────────
        var camGo = Camera.main != null
            ? Camera.main.gameObject
            : GetOrCreate("Main Camera", () =>
            {
                var go = new GameObject("Main Camera");
                go.tag = "MainCamera";
                go.AddComponent<Camera>();
                go.AddComponent<AudioListener>();
                return go;
            });

        // 배경: 짙은 남색 (동물 씬과 명확히 구분)
        var cam = camGo.GetComponent<Camera>();
        cam.clearFlags       = CameraClearFlags.SolidColor;
        cam.backgroundColor  = new Color(0.04f, 0.06f, 0.16f, 1f);
        camGo.transform.position = new Vector3(0f, 4f, -6f);
        camGo.transform.rotation = Quaternion.Euler(15f, 0f, 0f);

        var followCam = camGo.GetComponent<FollowCamera>()
                     ?? Undo.AddComponent<FollowCamera>(camGo);
        {
            var so = new SerializedObject(followCam);
            so.FindProperty("target").objectReferenceValue = sphere.transform;
            so.ApplyModifiedProperties();
        }

        // ── 씬 저장 ───────────────────────────────────────────────────────────
        EditorSceneManager.MarkSceneDirty(
            UnityEngine.SceneManagement.SceneManager.GetActiveScene());

        EditorUtility.DisplayDialog(
            "Gaze Test Scene 세팅 완료",
            "생성 완료:\n" +
            "  • Ground_Plane  (200m × 200m, 진회색)\n" +
            "  • 랜드마크 기둥 4개  (빨/파/초/노)\n" +
            "  • GazeTarget  (흰 Sphere + 빨간 전방 막대)\n" +
            "  • GazeSystem  (GazeReceiver + GazeNavigator + GazeCalibrator)\n" +
            "  • Main Camera  (짙은 남색 배경 + FollowCamera)\n\n" +
            "실행 순서:\n" +
            "  1. python scripts/gaze_sender.py  실행\n" +
            "  2. Unity Play\n" +
            "  3. Unity 화면의 노란 점을 순서대로 응시 (9회)\n" +
            "  4. 캘리브레이션 완료 → Sphere 조종 시작\n\n" +
            "GazeNavigator 튜닝 (Inspector):\n" +
            "  Move Speed / Turn Speed / Dead Zone",
            "OK");
    }

    // ── 랜드마크 기둥 생성 ────────────────────────────────────────────────────

    private static void CreateLandmarkPillar(string goName, Vector3 pos, Color col)
    {
        var pillar = GetOrCreate(goName, () =>
            GameObject.CreatePrimitive(PrimitiveType.Cylinder));
        pillar.name                = goName;
        pillar.transform.position  = pos + new Vector3(0f, 5f, 0f);
        pillar.transform.localScale = new Vector3(1.5f, 5f, 1.5f);
        SetColor(pillar, col);
    }

    // ── 헬퍼: 이름으로 찾고 없으면 생성 ─────────────────────────────────────

    private static GameObject GetOrCreate(string goName, System.Func<GameObject> creator)
    {
        var existing = GameObject.Find(goName);
        if (existing != null) return existing;
        var go = creator();
        go.name = goName;
        Undo.RegisterCreatedObjectUndo(go, $"Create {goName}");
        return go;
    }

    // ── 헬퍼: Renderer 색상 설정 ─────────────────────────────────────────────
    // URP / HDRP / Built-in 파이프라인을 순서대로 시도해 적합한 쉐이더를 선택.

    private static readonly string[] ShaderCandidates =
    {
        "Universal Render Pipeline/Lit",   // URP
        "HDRP/Lit",                        // HDRP
        "Standard",                        // Built-in
    };

    private static void SetColor(GameObject go, Color col)
    {
        var rend = go.GetComponent<Renderer>();
        if (rend == null) return;

        Shader shader = null;
        foreach (var name in ShaderCandidates)
        {
            shader = Shader.Find(name);
            if (shader != null) break;
        }
        if (shader == null) return;

        var mat = new Material(shader);
        // URP Lit 은 _BaseColor, Standard/HDRP 은 _Color (.color 프로퍼티)
        if (mat.HasProperty("_BaseColor"))
            mat.SetColor("_BaseColor", col);
        if (mat.HasProperty("_Color"))
            mat.SetColor("_Color", col);

        rend.sharedMaterial = mat;
    }
}
