// AnimalSceneSetup.cs
// HandAvatar > Setup * in Scene 메뉴 실행 시:
//   1. 동물 프리팹을 씬에 배치
//   2. AnimalController + AnimalLocomotion 컴포넌트 추가
//   3. walkAnimName 설정
//   4. Animator controller 설정
//   5. AnimalSwitcher.animalEntries에 항목 추가
//   6. 씬 저장 안내

using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class AnimalSceneSetup
{
    // ── 기존 씬에 Gaze 시스템만 추가 ────────────────────────────
    [MenuItem("HandAvatar/Add Gaze System to Scene")]
    public static void AddGazeToScene()
    {
        // 씬에서 AnimalLocomotion 자동 탐색
        var locos = Object.FindObjectsByType<AnimalLocomotion>(FindObjectsSortMode.None);
        if (locos.Length == 0)
        {
            EditorUtility.DisplayDialog("오류",
                "씬에 AnimalLocomotion 컴포넌트를 찾지 못했습니다.\n" +
                "먼저 Setup Spider/Horse in Scene 을 실행하세요.", "OK");
            return;
        }

        // 여러 마리면 첫 번째에 연결 (보통 AnimalSwitcher 가 관리)
        var loco = locos[0];
        SetupGazeSystem(loco);

        EditorSceneManager.MarkSceneDirty(
            UnityEngine.SceneManagement.SceneManager.GetActiveScene());

        EditorUtility.DisplayDialog("완료",
            "GazeSystem 추가 완료!\n\n" +
            $"AnimalLocomotion: {loco.gameObject.name}\n\n" +
            "실행 방법:\n" +
            "  python -u main.py --animal horse --mapping blend --locomotion --gaze\n" +
            "  Unity Play → 노란 점 9개 응시 → 시선으로 방향 제어\n\n" +
            "AnimalLocomotion Inspector → Yaw Source:\n" +
            "  Auto  = gaze 연결 시 자동 전환\n" +
            "  Gaze  = 항상 시선\n" +
            "  HeadDir = 기존 head-dir", "OK");
    }

    // ── Spider ───────────────────────────────────────────────────
    [MenuItem("HandAvatar/Setup Spider in Scene")]
    public static void SetupSpider()
    {
        SetupAnimal(
            animalName:    "spider",
            prefabPath:    "Assets/Spiders/Prefabs/spider_black.prefab",
            controllerPath:"Assets/Spiders/Animations/Spider.controller",
            walkAnimName:  "Walk",
            spawnPos:      new Vector3(0f, 0f, 0f)
        );
    }

    // ── Horse ────────────────────────────────────────────────────
    [MenuItem("HandAvatar/Setup Horse in Scene")]
    public static void SetupHorse()
    {
        SetupAnimal(
            animalName:    "horse",
            prefabPath:    "Assets/ithappy/Animals_FREE/Prefabs/Horse_001.prefab",
            controllerPath:"Assets/ithappy/Animals_FREE/Animations/Animation_Controllers/Horse_HandAnimal.controller",
            walkAnimName:  "Horse_001_walk",
            spawnPos:      new Vector3(20f, 0f, 0f)
        );
    }

    // ── 공통 로직 ─────────────────────────────────────────────────
    private static void SetupAnimal(
        string animalName,
        string prefabPath,
        string controllerPath,
        string walkAnimName,
        Vector3 spawnPos)
    {
        string goName = System.IO.Path.GetFileNameWithoutExtension(prefabPath);

        // 1. 씬에 이미 있는지 확인
        var existing = GameObject.Find(goName);
        if (existing != null)
        {
            bool overwrite = EditorUtility.DisplayDialog(
                $"{goName} 이미 존재",
                $"씬에 {goName} 오브젝트가 이미 있습니다.\n덮어쓰시겠습니까?",
                "덮어쓰기", "취소");
            if (!overwrite) return;
            Undo.DestroyObjectImmediate(existing);
        }

        // 2. 프리팹 로드
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
        if (prefab == null)
        {
            EditorUtility.DisplayDialog("오류", $"프리팹을 찾을 수 없습니다:\n{prefabPath}", "OK");
            return;
        }

        // 3. 씬에 배치
        var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
        go.name = goName;
        go.transform.position = spawnPos;
        go.transform.rotation = Quaternion.identity;
        Undo.RegisterCreatedObjectUndo(go, $"Setup {animalName} in Scene");

        // 4. Animator controller 교체
        var animator = go.GetComponentInChildren<Animator>();
        if (animator != null)
        {
            var controller = AssetDatabase.LoadAssetAtPath<RuntimeAnimatorController>(controllerPath);
            if (controller != null)
            {
                Undo.RecordObject(animator, "Set Animator Controller");
                animator.runtimeAnimatorController = controller;
                animator.enabled = false;
                Debug.Log($"[AnimalSceneSetup] Animator controller = {controller.name}");
            }
            else
            {
                Debug.LogWarning($"[AnimalSceneSetup] controller 없음: {controllerPath}");
            }
        }
        else
        {
            Debug.LogWarning($"[AnimalSceneSetup] {goName}에서 Animator를 찾지 못했습니다.");
        }

        // 5. AnimalController 추가
        var ctrl = go.GetComponent<AnimalController>() ?? Undo.AddComponent<AnimalController>(go);
        var ctrlSO = new SerializedObject(ctrl);
        ctrlSO.FindProperty("walkAnimName").stringValue              = walkAnimName;
        ctrlSO.FindProperty("idleAnimator").objectReferenceValue     = animator;
        ctrlSO.ApplyModifiedProperties();
        Debug.Log($"[AnimalSceneSetup] AnimalController: walkAnimName={walkAnimName}");

        // 6. AnimalLocomotion 추가
        var loco = go.GetComponent<AnimalLocomotion>() ?? Undo.AddComponent<AnimalLocomotion>(go);
        Debug.Log("[AnimalSceneSetup] AnimalLocomotion 추가 완료");

        // 6-1. GazeSystem 추가 (GazeReceiver + GazeNavigator + GazeCalibrator)
        SetupGazeSystem(loco);

        // 7. AnimalSwitcher 등록
        var switcher = Object.FindAnyObjectByType<AnimalSwitcher>();
        if (switcher != null)
        {
            var switcherSO  = new SerializedObject(switcher);
            var entriesProp = switcherSO.FindProperty("animalEntries");

            int targetIdx = -1;
            for (int i = 0; i < entriesProp.arraySize; i++)
            {
                string n = entriesProp.GetArrayElementAtIndex(i)
                                      .FindPropertyRelative("animalName").stringValue;
                if (string.Equals(n, animalName, System.StringComparison.OrdinalIgnoreCase))
                { targetIdx = i; break; }
            }
            if (targetIdx < 0)
            {
                entriesProp.InsertArrayElementAtIndex(entriesProp.arraySize);
                targetIdx = entriesProp.arraySize - 1;
            }

            var entry = entriesProp.GetArrayElementAtIndex(targetIdx);
            entry.FindPropertyRelative("animalName").stringValue             = animalName;
            entry.FindPropertyRelative("rootObject").objectReferenceValue    = go;
            entry.FindPropertyRelative("controller").objectReferenceValue    = ctrl;
            entry.FindPropertyRelative("locomotion").objectReferenceValue    = loco;
            switcherSO.ApplyModifiedProperties();
            EditorUtility.SetDirty(switcher);
            Debug.Log($"[AnimalSceneSetup] AnimalSwitcher에 '{animalName}' 등록 완료");
        }
        else
        {
            Debug.LogWarning("[AnimalSceneSetup] AnimalSwitcher를 씬에서 찾지 못했습니다. 수동으로 등록하세요.");
        }

        // 8. 씬 저장 안내
        EditorSceneManager.MarkSceneDirty(go.scene);
        EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo();

        EditorUtility.DisplayDialog(
            "완료",
            $"{goName} 씬 세팅 완료!\n\n" +
            "다음 단계:\n" +
            $"  1. {goName} 선택 → AnimalController Inspector\n" +
            $"  2. 동물 이름 = '{animalName}' 입력\n" +
            "  3. 'JointEntry 전체 자동 생성' 버튼 클릭\n\n" +
            "Gaze 사용 시:\n" +
            "  4. python scripts/gaze_sender.py 실행\n" +
            "  5. AnimalLocomotion Inspector → Yaw Source = Gaze 또는 Auto",
            "OK");
    }

    // ── GazeSystem 구성 ───────────────────────────────────────────────────────

    private static void SetupGazeSystem(AnimalLocomotion loco)
    {
        // 이미 있으면 loco 연결만 갱신
        var gazeSystem = GameObject.Find("GazeSystem")
                      ?? new GameObject("GazeSystem");
        Undo.RegisterCreatedObjectUndo(gazeSystem, "Create GazeSystem");

        var receiver   = gazeSystem.GetComponent<GazeReceiver>()
                      ?? Undo.AddComponent<GazeReceiver>(gazeSystem);
        var navigator  = gazeSystem.GetComponent<GazeNavigator>()
                      ?? Undo.AddComponent<GazeNavigator>(gazeSystem);
        var calibrator = gazeSystem.GetComponent<GazeCalibrator>()
                      ?? Undo.AddComponent<GazeCalibrator>(gazeSystem);

        // GazeNavigator.animalLocomotion = loco
        {
            var so = new SerializedObject(navigator);
            so.FindProperty("animalLocomotion").objectReferenceValue = loco;
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

        // MainThreadDispatcher 확인
        if (Object.FindAnyObjectByType<MainThreadDispatcher>() == null)
        {
            var dispatcher = new GameObject("MainThreadDispatcher");
            dispatcher.AddComponent<MainThreadDispatcher>();
            Undo.RegisterCreatedObjectUndo(dispatcher, "Create MainThreadDispatcher");
        }

        Debug.Log("[AnimalSceneSetup] GazeSystem 구성 완료 (GazeReceiver + GazeNavigator + GazeCalibrator)");
    }
}
