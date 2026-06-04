// LocomotionConfigEditor.cs
// 메뉴: HandAvatar > Locomotion Config
//
// locomotion_config.json 을 Unity Editor 에서 직접 편집한다.
// 각 동물의 poses.json 을 읽어 _anim 목록을 드롭다운으로 표시하므로
// 이름을 직접 타이핑할 필요가 없다.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

public class LocomotionConfigEditor : EditorWindow
{
    // ──────────────────────────────────────────────────────────
    // 내부 데이터 구조
    // ──────────────────────────────────────────────────────────

    [Serializable]
    private class AnimalEntry
    {
        public string   animal;
        public string   base_anim;
        public float    speed_scale;
        public float    dir_scale;
        public float    dir_deadzone;
        public float    speed_max;
        public float    ema_alpha;
        public float    proximity_scale;

        // Editor 전용 (저장 안 함)
        public List<string> availableAnims = new List<string>();
        public int          animIndex;       // 드롭다운 선택 인덱스
    }

    private static readonly string[] _metaKeys = { "_comment", "_hint" };

    private string               _configPath = "";
    private string               _posesDir   = "";
    private List<AnimalEntry>    _entries    = new List<AnimalEntry>();
    private Vector2              _scroll;
    private string               _status     = "";
    private bool                 _dirty      = false;

    // ──────────────────────────────────────────────────────────
    // 메뉴 진입점
    // ──────────────────────────────────────────────────────────

    [MenuItem("HandAvatar/Locomotion Config")]
    public static void ShowWindow()
    {
        var w = GetWindow<LocomotionConfigEditor>("Locomotion Config");
        w.minSize = new Vector2(480, 520);

        w._configPath = Path.GetFullPath(
            Path.Combine(Application.dataPath,
                         "../../python/data/mappings/locomotion_config.json"));
        w._posesDir = Path.GetFullPath(
            Path.Combine(Application.dataPath,
                         "../../python/data/animal_skeletons"));

        w.LoadConfig();
    }

    // ──────────────────────────────────────────────────────────
    // GUI
    // ──────────────────────────────────────────────────────────

    private void OnGUI()
    {
        GUILayout.Label("Locomotion Config Editor", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox(
            "동물별 base_anim 과 이동 파라미터를 설정합니다.\n" +
            "base_anim 드롭다운은 {animal}_poses.json 의 _anim 목록에서 자동 채워집니다.",
            MessageType.Info);

        EditorGUILayout.Space(4);

        // 상단 버튼
        using (new EditorGUILayout.HorizontalScope())
        {
            if (GUILayout.Button("Reload", GUILayout.Width(80)))
                LoadConfig();

            GUI.enabled = _dirty;
            if (GUILayout.Button("Save", GUILayout.Width(80)))
                SaveConfig();
            GUI.enabled = true;

            GUILayout.FlexibleSpace();
            EditorGUILayout.LabelField($"파일: {Path.GetFileName(_configPath)}",
                                       EditorStyles.miniLabel);
        }

        if (!string.IsNullOrEmpty(_status))
        {
            EditorGUILayout.HelpBox(_status, MessageType.None);
        }

        EditorGUILayout.Space(6);

        // 항목 목록
        _scroll = EditorGUILayout.BeginScrollView(_scroll);

        foreach (var entry in _entries)
        {
            DrawEntry(entry);
            EditorGUILayout.Space(4);
        }

        EditorGUILayout.EndScrollView();
    }

    private void DrawEntry(AnimalEntry entry)
    {
        using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
        {
            // ── 동물 이름 헤더 ──
            EditorGUILayout.LabelField(entry.animal, EditorStyles.boldLabel);

            // ── base_anim 드롭다운 ──
            using (new EditorGUILayout.HorizontalScope())
            {
                EditorGUILayout.PrefixLabel("base_anim");

                if (entry.availableAnims.Count == 0)
                {
                    // poses.json 없음 — 직접 입력
                    string newVal = EditorGUILayout.TextField(entry.base_anim);
                    if (newVal != entry.base_anim)
                    {
                        entry.base_anim = newVal;
                        _dirty = true;
                    }
                    EditorGUILayout.LabelField("(poses.json 없음)", EditorStyles.miniLabel,
                                               GUILayout.Width(110));
                }
                else
                {
                    // 드롭다운
                    var options = new List<string> { "(없음)" };
                    options.AddRange(entry.availableAnims);

                    int idx = entry.animIndex;
                    int newIdx = EditorGUILayout.Popup(idx, options.ToArray());
                    if (newIdx != idx)
                    {
                        entry.animIndex = newIdx;
                        entry.base_anim = newIdx == 0 ? "" : entry.availableAnims[newIdx - 1];
                        _dirty = true;
                    }

                    // 선택된 anim 이 없으면 경고
                    if (string.IsNullOrEmpty(entry.base_anim))
                    {
                        EditorGUILayout.LabelField("⚠ 미설정", EditorStyles.miniLabel,
                                                   GUILayout.Width(60));
                    }
                }
            }

            // ── 수치 파라미터 ──
            entry.speed_scale     = FloatField("speed_scale",     entry.speed_scale,     ref _dirty);
            entry.dir_scale       = FloatField("dir_scale",       entry.dir_scale,       ref _dirty);
            entry.dir_deadzone    = FloatField("dir_deadzone(°)", entry.dir_deadzone,    ref _dirty);
            entry.speed_max       = FloatField("speed_max",       entry.speed_max,       ref _dirty);
            entry.ema_alpha       = FloatField("ema_alpha",       entry.ema_alpha,       ref _dirty,
                                               min: 0.01f, max: 1.0f);
            entry.proximity_scale = FloatField("proximity_scale", entry.proximity_scale, ref _dirty,
                                               min: 0.001f, max: 0.5f);
        }
    }

    private static float FloatField(string label, float value, ref bool dirty,
                                    float min = 0f, float max = 0f)
    {
        float newVal = EditorGUILayout.FloatField(label, value);
        if (max > min) newVal = Mathf.Clamp(newVal, min, max);
        if (Math.Abs(newVal - value) > 1e-6f) dirty = true;
        return newVal;
    }

    private static float GetFloat(JObject d, string key, float defaultVal)
    {
        var token = d[key];
        return token != null ? token.Value<float>() : defaultVal;
    }

    // ──────────────────────────────────────────────────────────
    // 로드
    // ──────────────────────────────────────────────────────────

    private void LoadConfig()
    {
        _entries.Clear();
        _status = "";

        if (!File.Exists(_configPath))
        {
            _status = $"파일 없음: {_configPath}";
            return;
        }

        string json = File.ReadAllText(_configPath);
        JObject root;
        try { root = JObject.Parse(json); }
        catch (Exception ex) { _status = $"JSON 파싱 실패: {ex.Message}"; return; }

        foreach (var kv in root)
        {
            if (_metaKeys.Contains(kv.Key)) continue;
            if (!(kv.Value is JObject d)) continue;

            var entry = new AnimalEntry
            {
                animal          = kv.Key,
                base_anim       = d.Value<string>("base_anim")      ?? "",
                speed_scale     = GetFloat(d, "speed_scale",     0.04f),
                dir_scale       = GetFloat(d, "dir_scale",       0.8f),
                dir_deadzone    = GetFloat(d, "dir_deadzone",    5.0f),
                speed_max       = GetFloat(d, "speed_max",       2.0f),
                ema_alpha       = GetFloat(d, "ema_alpha",       0.3f),
                proximity_scale = GetFloat(d, "proximity_scale", 0.03f),
            };

            // poses.json 에서 anim 목록 읽기
            entry.availableAnims = ReadAnimNames(kv.Key);

            // 현재 base_anim 의 드롭다운 인덱스 찾기
            if (!string.IsNullOrEmpty(entry.base_anim) && entry.availableAnims.Count > 0)
            {
                int idx = entry.availableAnims.IndexOf(entry.base_anim);
                entry.animIndex = idx >= 0 ? idx + 1 : 0;  // +1: 첫 항목이 "(없음)"
            }

            _entries.Add(entry);
        }

        _dirty  = false;
        _status = $"로드 완료 ({_entries.Count}개 동물)";
        Repaint();
    }

    private List<string> ReadAnimNames(string animal)
    {
        string posesPath = Path.Combine(_posesDir, $"{animal}_poses.json");
        if (!File.Exists(posesPath)) return new List<string>();

        string json = File.ReadAllText(posesPath);
        JArray poses;
        try { poses = JArray.Parse(json); }
        catch { return new List<string>(); }

        var seen  = new HashSet<string>();
        var names = new List<string>();
        foreach (var p in poses)
        {
            string name = p.Value<string>("_anim") ?? "";
            if (!string.IsNullOrEmpty(name) && seen.Add(name))
                names.Add(name);
        }
        return names;
    }

    // ──────────────────────────────────────────────────────────
    // 저장
    // ──────────────────────────────────────────────────────────

    private void SaveConfig()
    {
        var sb = new System.Text.StringBuilder();
        sb.AppendLine("{");
        sb.AppendLine("  \"_comment\": \"동물별 로코모션 설정. base_anim 은 해당 동물의 poses.json 에 있는 _anim 이름과 일치해야 함.\",");
        sb.AppendLine("  \"_hint\":    \"python main.py --locomotion 실행 시 시작 로그에 사용 가능한 anim 목록이 출력됨.\",");
        sb.AppendLine();

        for (int i = 0; i < _entries.Count; i++)
        {
            var e = _entries[i];
            sb.AppendLine($"  \"{e.animal}\": {{");
            sb.AppendLine($"    \"base_anim\":       \"{e.base_anim}\",");
            sb.AppendLine($"    \"speed_scale\":     {F(e.speed_scale)},");
            sb.AppendLine($"    \"dir_scale\":       {F(e.dir_scale)},");
            sb.AppendLine($"    \"dir_deadzone\":    {F(e.dir_deadzone)},");
            sb.AppendLine($"    \"speed_max\":       {F(e.speed_max)},");
            sb.AppendLine($"    \"ema_alpha\":       {F(e.ema_alpha)},");
            sb.Append(    $"    \"proximity_scale\": {F(e.proximity_scale)}");
            sb.AppendLine();
            sb.Append("  }");
            sb.AppendLine(i < _entries.Count - 1 ? "," : "");
        }

        sb.AppendLine("}");

        File.WriteAllText(_configPath, sb.ToString(), System.Text.Encoding.UTF8);
        _dirty  = false;
        _status = "저장 완료";
        Repaint();
    }

    private static string F(float v) => v.ToString("0.####", System.Globalization.CultureInfo.InvariantCulture);

}
