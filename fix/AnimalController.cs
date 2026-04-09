// AnimalController.cs
// [수정 2026-04]
//   1. BuildRotation()을 다축 동시 회전 지원으로 교체
//      기존: Axis.X 하나만 → 수정: rotation_axis_x/y/z 각각 체크박스
//   2. JointEntry에 axisX/Y/Z bool 추가 — Inspector에서 축 조합 선택 가능
//   3. 관절 이름 패턴으로 기본 축 자동 추론 (Awake에서 한 번 실행)
//      _base → Y축 (옆으로 벌어짐), _mid/_tip → X축 (앞뒤 굽힘)

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
        public bool axisX = true;
        public bool axisY = false;
        public bool axisZ = false;

        [Header("축별 부호 반전 (모델 방향에 따라)")]
        public bool invertX = false;
        public bool invertY = false;
        public bool invertZ = false;
    }

    [Header("관절 매핑")]
    [SerializeField] private List<JointEntry> jointEntries = new List<JointEntry>();

    [Header("Lerp 속도 (클수록 빠름, 60fps 기준)")]
    [SerializeField] [Range(1f, 30f)] private float lerpSpeed = 12f;

    [Header("Idle 애니메이션")]
    [SerializeField] private Animator idleAnimator;

    [Header("축 자동 추론 (Awake에서 실행)")]
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

            // 축 자동 추론
            if (autoInferAxes)
                InferAxes(entry);

            _jointMap[entry.jointName] = entry;
        }
        Debug.Log($"[AnimalController] 관절 {_jointMap.Count}개 로드됨");

        // 로드된 관절별 축 설정 출력 (디버그)
        foreach (var kv in _jointMap)
        {
            var e = kv.Value;
            string axes = $"{(e.axisX?"X":"")}{(e.axisY?"Y":"")}{(e.axisZ?"Z":"")}";
            if (axes == "") axes = "none";
            Debug.Log($"  {kv.Key}: 축={axes}" +
                      $"{(e.invertX?" -X":"")}{(e.invertY?" -Y":"")}{(e.invertZ?" -Z":"")}");
        }
    }

    /// <summary>
    /// 관절 이름 패턴으로 회전 축을 자동 추론한다.
    ///
    /// 거미 다리 기준:
    ///   _base  → Y축만 (몸통에서 옆으로 벌어지는 관절)
    ///   _mid   → X축만 (중간 굽힘)
    ///   _tip   → X축만 (끝 굽힘)
    ///
    /// 다른 동물 추가 시 이 메서드를 확장하거나
    /// Inspector에서 autoInferAxes를 끄고 직접 지정한다.
    /// </summary>
    private static void InferAxes(JointEntry entry)
    {
        string name = entry.jointName.ToLower();

        // 초기화
        entry.axisX = false;
        entry.axisY = false;
        entry.axisZ = false;

        if (name.Contains("_base"))
        {
            // 몸통 연결 관절: 옆으로 벌어지는 방향 = Y
            entry.axisY = true;
        }
        else if (name.Contains("_mid") || name.Contains("_tip"))
        {
            // 굽힘 관절: 앞뒤 = X
            entry.axisX = true;
        }
        else if (name.Contains("wing") || name.Contains("fin"))
        {
            // 날개/지느러미: 상하 = Z
            entry.axisZ = true;
        }
        else if (name.Contains("tail") || name.Contains("spine") || name.Contains("trunk"))
        {
            // 꼬리/척추: 좌우 = Y
            entry.axisY = true;
        }
        else
        {
            // 기본: X (굽힘)
            entry.axisX = true;
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
        if (_applyCount <= 10)
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
    /// 체크된 축에만 angle을 적용한 Quaternion 반환.
    /// 여러 축이 체크된 경우 각 축 회전을 순서대로 합성한다.
    /// </summary>
    private static Quaternion BuildRotation(JointEntry entry, float angle)
    {
        float ax = entry.axisX ? (angle * (entry.invertX ? -1f : 1f)) : 0f;
        float ay = entry.axisY ? (angle * (entry.invertY ? -1f : 1f)) : 0f;
        float az = entry.axisZ ? (angle * (entry.invertZ ? -1f : 1f)) : 0f;
        return Quaternion.Euler(ax, ay, az);
    }

    // ── 디버그 헬퍼 ───────────────────────────────────────────

    /// <summary>
    /// 특정 관절의 축 설정을 런타임에 변경한다 (디버그/미세조정용).
    /// 예: controller.SetJointAxes("leg_L1_base", axisY: true);
    /// </summary>
    public void SetJointAxes(string jointName,
                              bool axisX = false, bool axisY = false, bool axisZ = false,
                              bool invertX = false, bool invertY = false, bool invertZ = false)
    {
        if (!_jointMap.TryGetValue(jointName, out var entry))
        {
            Debug.LogWarning($"[AnimalController] 관절 없음: {jointName}");
            return;
        }
        entry.axisX   = axisX;   entry.axisY   = axisY;   entry.axisZ   = axisZ;
        entry.invertX = invertX; entry.invertY = invertY; entry.invertZ = invertZ;
        Debug.Log($"[AnimalController] {jointName} 축 변경: " +
                  $"X={axisX}({(invertX?"-":"+")}), Y={axisY}({(invertY?"-":"+")}), Z={axisZ}({(invertZ?"-":"+")})");
    }

    /// <summary>
    /// 현재 모든 관절의 각도를 즉시 0으로 리셋한다 (T포즈로 복귀).
    /// </summary>
    public void ResetToTPose()
    {
        foreach (var entry in jointEntries)
        {
            if (entry.jointTransform != null)
                entry.jointTransform.localRotation = Quaternion.identity;
        }
        _targetAngles.Clear();
    }
}
