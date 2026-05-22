// ModelReviewWindow.cs
// 새 동물 모델 import 시 관절 좌/우 배정 및 체인 루트 손가락 배정을 검수하는 Editor 창.
//
// 동작:
//   1. AnimalControllerEditor 가 bone_map + skeleton.json 파싱 후 이 창을 열어 줌
//   2. 모든 관절: 좌/우 자동 감지 (l_ → Left 등), 드롭다운으로 수정 가능
//   3. 체인 루트 관절: 손가락 자동 감지 (l_leg→index, l_leg_001→middle 등), 수정 가능
//   4. "Confirm & Generate" →
//        side_override.json   저장 (좌우 변경 있을 때)
//        finger_override.json 저장 (손가락 변경 있을 때)
//      → JointEntry 자동 생성 콜백 실행

using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEngine;

public class ModelReviewWindow : EditorWindow
{
    // ── 공개 데이터 구조 ──────────────────────────────────────────
    public class JointSideEntry
    {
        public string jointId;
        public string detectedSide;    // "left" | "right" | "center"
        public string overrideSide;    // null = detectedSide 사용
        public bool   included = true; // false = JointEntry 생성 제외

        // 체인 루트 손가락 배정 (비체인 루트는 null)
        public string detectedFinger;  // "index" | "middle" | "ring" | "pinky" | null
        public string overrideFinger;  // null = detectedFinger 사용

        public string EffectiveSide   => string.IsNullOrEmpty(overrideSide)   ? detectedSide   : overrideSide;
        public string EffectiveFinger => string.IsNullOrEmpty(overrideFinger) ? detectedFinger : overrideFinger;
        public bool   IsChainRoot     => detectedFinger != null;
    }

    // ── 내부 상태 ─────────────────────────────────────────────────
    private string _animal;
    private List<JointSideEntry> _entries = new List<JointSideEntry>();
    private Vector2 _scroll;
    private System.Action<List<JointSideEntry>> _onConfirm;

    private static readonly string[] SideOptions   = { "left", "right", "center" };
    private static readonly string[] FingerOptions = { "index", "middle", "ring", "pinky", "thumb" };
    private static readonly Color    OverrideColor = new Color(1f, 0.93f, 0.6f);

    // ── 공개 진입점 ───────────────────────────────────────────────
    public static void Open(
        string animal,
        List<JointSideEntry> entries,
        System.Action<List<JointSideEntry>> onConfirm)
    {
        var window = GetWindow<ModelReviewWindow>(true, "Joint Review", true);
        window._animal    = animal;
        window._entries   = entries;
        window._onConfirm = onConfirm;
        window.minSize    = new Vector2(640, 500);
        window.Show();
    }

    // ── 손가락 자동 감지 (Python mapping_optimizer._auto_finger 와 동일 로직) ──
    public static string DetectFinger(string jointId)
    {
        // l_/r_ 접두사 제거
        string base_ = jointId;
        if (base_.StartsWith("l_", System.StringComparison.OrdinalIgnoreCase)) base_ = base_.Substring(2);
        else if (base_.StartsWith("r_", System.StringComparison.OrdinalIgnoreCase)) base_ = base_.Substring(2);

        // trailing _NNN 파싱
        var m = Regex.Match(base_, @"_(\d+)$");
        int idx = m.Success ? int.Parse(m.Groups[1].Value) : 0;

        string[] order = { "index", "middle", "ring", "pinky" };
        return idx < order.Length ? order[idx] : null;
    }

    // ── GUI ───────────────────────────────────────────────────────
    private void OnGUI()
    {
        EditorGUILayout.LabelField($"관절 검수  —  {_animal}", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox(
            "[좌/우]  l_ → Left, r_ → Right, 그 외 → Center\n" +
            "[손가락] 체인 루트만 표시. l_leg→index, l_leg_001→middle, _002→ring, _003→pinky\n" +
            "Confirm 시 변경사항이 side/finger_override.json 에 저장됩니다.",
            MessageType.Info);

        // ── 헤더 ─────────────────────────────────────────────────
        EditorGUILayout.BeginHorizontal(EditorStyles.toolbar);
        EditorGUILayout.LabelField("Include",     EditorStyles.toolbarButton, GUILayout.Width(52));
        EditorGUILayout.LabelField("Joint ID",    EditorStyles.toolbarButton, GUILayout.Width(190));
        EditorGUILayout.LabelField("Side Auto",   EditorStyles.toolbarButton, GUILayout.Width(58));
        EditorGUILayout.LabelField("Side",        EditorStyles.toolbarButton, GUILayout.Width(80));
        EditorGUILayout.LabelField("Finger Auto", EditorStyles.toolbarButton, GUILayout.Width(70));
        EditorGUILayout.LabelField("Finger",      EditorStyles.toolbarButton, GUILayout.Width(80));
        EditorGUILayout.EndHorizontal();

        _scroll = EditorGUILayout.BeginScrollView(_scroll, GUILayout.ExpandHeight(true));

        bool anyOverride = false;
        for (int i = 0; i < _entries.Count; i++)
        {
            var  e           = _entries[i];
            bool sideChanged = !string.IsNullOrEmpty(e.overrideSide)   && e.overrideSide   != e.detectedSide;
            bool finChanged  = !string.IsNullOrEmpty(e.overrideFinger) && e.overrideFinger != e.detectedFinger;
            bool changed     = sideChanged || finChanged;
            if (changed) anyOverride = true;

            var prevBg = GUI.backgroundColor;
            if (!e.included) GUI.backgroundColor = new Color(0.7f, 0.7f, 0.7f);
            else if (changed) GUI.backgroundColor = OverrideColor;
            EditorGUILayout.BeginHorizontal();
            GUI.backgroundColor = prevBg;

            e.included = EditorGUILayout.Toggle(e.included, GUILayout.Width(52));

            var labelStyle = e.included ? EditorStyles.label : new GUIStyle(EditorStyles.label) { normal = { textColor = Color.gray } };
            EditorGUILayout.LabelField(e.jointId, labelStyle, GUILayout.Width(190));

            // ── 좌/우 ──────────────────────────────────────────
            EditorGUILayout.LabelField(e.detectedSide, GUILayout.Width(58));
            int sideIdx = Mathf.Max(0, System.Array.IndexOf(SideOptions, e.EffectiveSide));
            int newSide = EditorGUILayout.Popup(sideIdx, SideOptions, GUILayout.Width(80));
            if (newSide != sideIdx)
                e.overrideSide = (SideOptions[newSide] == e.detectedSide) ? null : SideOptions[newSide];

            // ── 손가락 (체인 루트만) ────────────────────────────
            if (e.IsChainRoot)
            {
                EditorGUILayout.LabelField(e.detectedFinger ?? "-", GUILayout.Width(70));
                int finIdx = Mathf.Max(0, System.Array.IndexOf(FingerOptions, e.EffectiveFinger));
                int newFin = EditorGUILayout.Popup(finIdx, FingerOptions, GUILayout.Width(80));
                if (newFin != finIdx)
                    e.overrideFinger = (FingerOptions[newFin] == e.detectedFinger) ? null : FingerOptions[newFin];
            }
            else
            {
                EditorGUILayout.LabelField("-", GUILayout.Width(70));
                EditorGUILayout.LabelField("-", GUILayout.Width(80));
            }

            EditorGUILayout.EndHorizontal();
        }

        EditorGUILayout.EndScrollView();

        if (anyOverride)
            EditorGUILayout.HelpBox("변경된 항목 있음 — Confirm 시 override JSON 저장.", MessageType.Warning);

        EditorGUILayout.Space(4);
        EditorGUILayout.BeginHorizontal();
        int excluded = _entries.Count(e => !e.included);
        if (excluded > 0)
            EditorGUILayout.HelpBox($"{excluded}개 관절 제외됨 — JointEntry 미생성.", MessageType.Info);

        if (GUILayout.Button("Confirm & Generate", GUILayout.Height(32)))
        {
            var included = _entries.Where(e => e.included).ToList();
            SaveOverrides(_animal, included);
            _onConfirm?.Invoke(included);
            Close();
        }
        if (GUILayout.Button("Cancel", GUILayout.Height(32), GUILayout.Width(80)))
            Close();
        EditorGUILayout.EndHorizontal();
    }

    // ── side_override.json + finger_override.json 저장 ───────────
    private static void SaveOverrides(string animal, List<JointSideEntry> entries)
    {
        string basePath = Path.GetFullPath(
            Path.Combine(Application.dataPath, $"../../python/data/animal_skeletons/"));

        // side_override
        SaveJson(Path.Combine(basePath, $"{animal}_side_override.json"),
            BuildDict(entries, e => e.overrideSide,
                      e => !string.IsNullOrEmpty(e.overrideSide) && e.overrideSide != e.detectedSide),
            "side_override");

        // finger_override (체인 루트만)
        SaveJson(Path.Combine(basePath, $"{animal}_finger_override.json"),
            BuildDict(entries, e => e.overrideFinger,
                      e => e.IsChainRoot && !string.IsNullOrEmpty(e.overrideFinger) && e.overrideFinger != e.detectedFinger),
            "finger_override");
    }

    private static Dictionary<string, string> BuildDict(
        List<JointSideEntry> entries,
        System.Func<JointSideEntry, string> valueSelector,
        System.Func<JointSideEntry, bool> filter)
    {
        var d = new Dictionary<string, string>();
        foreach (var e in entries)
            if (filter(e)) d[e.jointId] = valueSelector(e);
        return d;
    }

    private static void SaveJson(string path, Dictionary<string, string> data, string label)
    {
        if (data.Count > 0)
        {
            var sb = new StringBuilder();
            sb.AppendLine("{");
            int i = 0;
            foreach (var kv in data)
            {
                string comma = (i < data.Count - 1) ? "," : "";
                sb.AppendLine($"  \"{kv.Key}\": \"{kv.Value}\"{comma}");
                i++;
            }
            sb.AppendLine("}");
            File.WriteAllText(path, sb.ToString(), Encoding.UTF8);
            Debug.Log($"[ModelReviewWindow] {label} 저장: {path} ({data.Count}개)");
        }
        else if (File.Exists(path))
        {
            File.Delete(path);
        }
    }
}
