// AnimalController.cs
// Python에서 수신한 관절 각도를 동물 3D 모델의 Transform에 실제로 적용한다.

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
        public Axis      rotationAxis = Axis.X;
    }

    public enum Axis { X, Y, Z }

    [Header("관절 매핑")]
    [SerializeField] private List<JointEntry> jointEntries = new List<JointEntry>();

    [Header("Lerp 속도 (클수록 빠름, 60fps 기준)")]
    [SerializeField] [Range(1f, 30f)] private float lerpSpeed = 12f;

    [Header("Idle 애니메이션")]
    [SerializeField] private Animator idleAnimator;

    private Dictionary<string, JointEntry> _jointMap;
    private Dictionary<string, float> _targetAngles = new Dictionary<string, float>();
    private bool _isIdle = true;

    // 디버그: 몇 번 ApplyJoints 호출됐는지
    private int _applyCount = 0;

    private void Awake()
    {
        _jointMap = new Dictionary<string, JointEntry>(StringComparer.Ordinal);
        foreach (var entry in jointEntries)
        {
            if (!string.IsNullOrEmpty(entry.jointName) && entry.jointTransform != null)
                _jointMap[entry.jointName] = entry;
        }
        Debug.Log($"[AnimalController] 관절 {_jointMap.Count}개 로드됨");
    }

    private void Update()
    {
        if (_isIdle) return;

        foreach (var kv in _targetAngles)
        {
            if (!_jointMap.TryGetValue(kv.Key, out var entry)) continue;
            if (entry.jointTransform == null) continue;

            Quaternion current = entry.jointTransform.localRotation;
            Quaternion target  = BuildRotation(entry.rotationAxis, kv.Value);
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

        // 처음 10번만 로그 출력
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

    private static Quaternion BuildRotation(Axis axis, float angle)
    {
        return axis switch
        {
            Axis.X => Quaternion.Euler(angle, 0f, 0f),
            Axis.Y => Quaternion.Euler(0f, angle, 0f),
            Axis.Z => Quaternion.Euler(0f, 0f, angle),
            _      => Quaternion.identity,
        };
    }

    public void SetAxis(string jointName, Axis axis)
    {
        if (_jointMap.TryGetValue(jointName, out var entry))
            entry.rotationAxis = axis;
    }
}
