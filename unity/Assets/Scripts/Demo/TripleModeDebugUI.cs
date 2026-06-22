// TripleModeDebugUI.cs
// Play 모드에서 각 모드의 블렌드 수치를 실시간 슬라이더로 조정

using UnityEngine;

public class TripleModeDebugUI : MonoBehaviour
{
    [Header("컨트롤러 연결 (TripleModeSceneSetup 이 자동 연결)")]
    public AnimalController blendCtrl;
    public AnimalController keyframeCtrl;

    [Header("초기값")]
    public float blendWalkHand    = 0.6f;
    public float blendAttackHand  = 0.5f;
    public float keyframeWalkHand    = 0f;
    public float keyframeAttackHand  = 0f;

    private bool _visible = true;

    private void Update()
    {
        // 매 프레임 슬라이더 값을 컨트롤러에 적용
        if (blendCtrl != null)
        {
            blendCtrl.WalkHandBlend    = blendWalkHand;
            blendCtrl.TriggerHandBlend = blendAttackHand;
        }
        if (keyframeCtrl != null)
        {
            keyframeCtrl.WalkHandBlend    = keyframeWalkHand;
            keyframeCtrl.TriggerHandBlend = keyframeAttackHand;
        }

        if (Input.GetKeyDown(KeyCode.H)) _visible = !_visible;
    }

    private void OnGUI()
    {
        if (!_visible) return;

        var box = new GUIStyle(GUI.skin.box)   { fontSize = 13 };
        var lbl = new GUIStyle(GUI.skin.label) { fontSize = 13 };

        GUILayout.BeginArea(new Rect(10, 10, 300, 185), box);
        GUILayout.Label("Triple Blend 조정  [H: 숨기기]", lbl);
        GUILayout.Space(4);

        GUILayout.Label("■ Blend", lbl);
        blendWalkHand   = SliderRow("Walk Hand",   blendWalkHand,   lbl);
        blendAttackHand = SliderRow("Attack Hand", blendAttackHand, lbl);

        GUILayout.Space(6);

        GUILayout.Label("■ Keyframe", lbl);
        keyframeWalkHand   = SliderRow("Walk Hand",   keyframeWalkHand,   lbl);
        keyframeAttackHand = SliderRow("Attack Hand", keyframeAttackHand, lbl);

        GUILayout.EndArea();
    }

    private static float SliderRow(string label, float val, GUIStyle lbl)
    {
        GUILayout.BeginHorizontal();
        GUILayout.Label($"{label}: {val:F2}", lbl, GUILayout.Width(130));
        val = GUILayout.HorizontalSlider(val, 0f, 1f, GUILayout.Width(140));
        GUILayout.EndHorizontal();
        return val;
    }
}
