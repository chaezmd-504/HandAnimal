// AnimalController.cs
// [수정 2026-04]
//   핵심 버그 수정:
//   1. Rest-pose 보존: Awake에서 초기 localRotation 저장,
//      이후 회전은 restPose * Quaternion.Euler(delta) 방식으로 적용
//      → 기존: Quaternion.Euler(angle)로 절대 회전 세팅 → 다리 축 처짐
//   2. ROM 클램핑: 관절 타입(_base/_mid/_tip)별 각도 한계 적용
//   3. 다축 회전 지원 + 관절명 패턴 축 자동 추론 유지

using System;
using System.Collections.Generic;
using UnityEngine;

public class AnimalController : MonoBehaviour
{
    [Serializable]
    public class JointEntry
    {
        public string    jointName;
        public Transform jointTransform;

        [Header("회전 축 (복수 선택 가능)")]
        public bool axisX = false;
        public bool axisY = false;
        public bool axisZ = false;

        [Header("축별 부호 반전 (모델 방향에 따라)")]
        public bool invertX = false;
        public bool invertY = false;
        public bool invertZ = false;

        [Header("각도 범위 (REST 기준 델타)")]
        public float minAngle = -30f;
        public float maxAngle =  30f;

        // 런타임 전용 — Inspector에 노출 안 함
        [NonSerialized] public Quaternion restRotation;
    }

    [Header("관절 매핑")]
    [SerializeField] private List<JointEntry> jointEntries = new List<JointEntry>();

    [Header("Lerp 속도 (클수록 빠름, 60fps 기준)")]
    [SerializeField] [Range(1f, 30f)] private float lerpSpeed = 12f;

    [Header("Idle 애니메이션")]
    [SerializeField] private Animator idleAnimator;

    [Header("축 / ROM 자동 추론 (Awake에서 실행)")]
    [SerializeField] private bool autoInferAxes = true;

    private Dictionary<string, JointEntry> _jointMap;
    private Dictionary<string, float> _targetAngles = new Dictionary<string, float>();
    private bool _isIdle = true;
    private int _applyCount = 0;

    private void Awake()
    {
        _jointMap = new Dictionary<string, JointEntry>(StringComparer.Ordinal);
        foreach (var entry in jointEntries)
        {
            if (string.IsNullOrEmpty(entry.jointName) || entry.jointTransform == null)
                continue;

            // ── 핵심: Rest-pose 저장 ──────────────────────────────
            // 이후 모든 회전은 이 값을 기준으로 delta 적용
            entry.restRotation = entry.jointTransform.localRotation;

            // 축 / ROM 자동 추론
            if (autoInferAxes)
                InferAxesAndRom(entry);

            _jointMap[entry.jointName] = entry;
        }

        Debug.Log($"[AnimalController] 관절 {_jointMap.Count}개 로드됨 (autoInferAxes={autoInferAxes})");
        foreach (var kv in _jointMap)
        {
            var e = kv.Value;
            string axes = $"{(e.axisX?"X":"")}{(e.axisY?"Y":"")}{(e.axisZ?"Z":"")}";
            if (string.IsNullOrEmpty(axes)) axes = "none";
            Debug.Log($"  {kv.Key}: 축={axes}  ROM=[{e.minAngle:F0}°, {e.maxAngle:F0}°]  " +
                      $"rest=({e.restRotation.x:F3},{e.restRotation.y:F3}," +
                      $"{e.restRotation.z:F3},{e.restRotation.w:F3})");
        }
    }

    /// <summary>
    /// 관절 이름 패턴으로 축과 ROM을 자동 추론.
    ///
    /// 거미 다리 기준:
    ///   _base  → Y축, ROM ±30°  (몸통에서 옆으로 벌어짐)
    ///   _mid   → X축, ROM 0~90° (중간 굽힘)
    ///   _tip   → X축, ROM 0~60° (끝 굽힘)
    /// </summary>
    private static void InferAxesAndRom(JointEntry entry)
    {
        string name = entry.jointName.ToLower();

        entry.axisX = false;
        entry.axisY = false;
        entry.axisZ = false;

        if (name.Contains("_base"))
        {
            entry.axisY    = true;
            entry.minAngle = -30f;
            entry.maxAngle =  30f;
        }
        else if (name.Contains("_mid"))
        {
            entry.axisX    = true;
            entry.minAngle =  0f;
            entry.maxAngle = 90f;
        }
        else if (name.Contains("_tip"))
        {
            entry.axisX    = true;
            entry.minAngle =  0f;
            entry.maxAngle = 60f;
        }
        else if (name.Contains("wing") || name.Contains("fin"))
        {
            entry.axisZ    = true;
            entry.minAngle = -60f;
            entry.maxAngle =  60f;
        }
        else if (name.Contains("tail") || name.Contains("spine"))
        {
            entry.axisY    = true;
            entry.minAngle = -45f;
            entry.maxAngle =  45f;
        }
        else
        {
            entry.axisX    = true;
            entry.minAngle = -45f;
            entry.maxAngle =  45f;
        }
    }

    private void Update()
    {
        if (_isIdle) return;

        foreach (var kv in _targetAngles)
        {
            if (!_jointMap.TryGetValue(kv.Key, out var entry)) continue;
            if (entry.jointTransform == null) continue;

            Quaternion current = entry.jointTransform.localRotation;
            Quaternion target  = BuildRotation(entry, kv.Value);
            entry.jointTransform.localRotation =
                Quaternion.Lerp(current, target, lerpSpeed * Time.deltaTime);
        }
    }

    public void ApplyJoints(Dictionary<string, float> joints)
    {
        _isIdle = false;

        if (idleAnimator != null && idleAnimator.enabled)
            idleAnimator.enabled = false;

        foreach (var kv in joints)
            _targetAngles[kv.Key] = kv.Value;

        _applyCount++;
        if (_applyCount <= 5)
        {
            var sb = new System.Text.StringBuilder();
            foreach (var kv in joints)
                sb.Append($"{kv.Key}={kv.Value:F1} ");
            Debug.Log($"[AnimalController] ApplyJoints #{_applyCount}: {sb}");
        }
    }

    public void SetIdle()
    {
        if (_isIdle) return;
        _isIdle = true;
        if (idleAnimator != null)
            idleAnimator.enabled = true;
    }

    /// <summary>
    /// Rest-pose 기준 delta 회전을 적용한 Quaternion 반환.
    ///
    /// 계산:
    ///   1. angle을 ROM [minAngle, maxAngle]으로 클램핑
    ///   2. delta = Quaternion.Euler(clampedAngle * axis * sign)
    ///   3. result = restRotation * delta
    ///
    /// restRotation을 곱하는 이유:
    ///   본이 프리팹에서 가지는 초기 쿼터니언(비-identity)을 보존하면서
    ///   그 위에 손가락 각도만큼의 추가 회전을 얹는 구조.
    ///   → 기존 절대 Euler 방식의 "축 처짐" 버그 수정
    /// </summary>
    private static Quaternion BuildRotation(JointEntry entry, float angle)
    {
        float clamped = Mathf.Clamp(angle, entry.minAngle, entry.maxAngle);

        float ax = entry.axisX ? (clamped * (entry.invertX ? -1f : 1f)) : 0f;
        float ay = entry.axisY ? (clamped * (entry.invertY ? -1f : 1f)) : 0f;
        float az = entry.axisZ ? (clamped * (entry.invertZ ? -1f : 1f)) : 0f;

        Quaternion delta = Quaternion.Euler(ax, ay, az);
        return entry.restRotation * delta;
    }

    // ── 디버그 헬퍼 ──────────────────────────────────────────────

    /// <summary>
    /// 특정 관절의 축/ROM/반전을 런타임에 변경한다.
    /// </summary>
    public void SetJointAxes(string jointName,
                              bool axisX = false, bool axisY = false, bool axisZ = false,
                              bool invertX = false, bool invertY = false, bool invertZ = false,
                              float minAngle = -45f, float maxAngle = 45f)
    {
        if (!_jointMap.TryGetValue(jointName, out var entry))
        {
            Debug.LogWarning($"[AnimalController] 관절 없음: {jointName}");
            return;
        }
        entry.axisX = axisX; entry.axisY = axisY; entry.axisZ = axisZ;
        entry.invertX = invertX; entry.invertY = invertY; entry.invertZ = invertZ;
        entry.minAngle = minAngle; entry.maxAngle = maxAngle;
        Debug.Log($"[AnimalController] {jointName} 변경: 축={( axisX?"X":"")}{(axisY?"Y":"")}{(axisZ?"Z":"")} " +
                  $"ROM=[{minAngle},{maxAngle}]");
    }

    /// <summary>
    /// 모든 관절을 Rest-pose로 즉시 복귀.
    /// </summary>
    public void ResetToRestPose()
    {
        foreach (var entry in jointEntries)
        {
            if (entry.jointTransform != null)
                entry.jointTransform.localRotation = entry.restRotation;
        }
        _targetAngles.Clear();
        Debug.Log("[AnimalController] Rest-pose로 복귀");
    }
}
