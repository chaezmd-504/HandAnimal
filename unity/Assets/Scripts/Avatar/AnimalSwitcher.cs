// AnimalSwitcher.cs
// switch_animal 메시지를 받으면 현재 동물을 숨기고 다음 동물을 표시한다.
//
// Inspector 설정 방법:
//   1. animalEntries 리스트에 항목 추가
//   2. animalName: "spider", "butterfly" 등 Python과 동일한 이름
//   3. rootObject: 해당 동물의 루트 GameObject 드래그 앤 드롭
//   4. controller: 해당 동물의 AnimalController 컴포넌트

using System;
using System.Collections.Generic;
using UnityEngine;
using TMPro;

public class AnimalSwitcher : MonoBehaviour
{
    [Serializable]
    public class AnimalEntry
    {
        public string         animalName;
        public GameObject     rootObject;
        public AnimalController controller;
    }

    [Header("동물 목록")]
    [SerializeField] private List<AnimalEntry> animalEntries = new List<AnimalEntry>();

    [Header("UI (선택 사항)")]
    [SerializeField] private TextMeshProUGUI animalNameText;

    private Dictionary<string, AnimalEntry> _map;
    private string _current;

    // ──────────────────────────────────────────────────────────
    // Unity 생명주기
    // ──────────────────────────────────────────────────────────

    private void Awake()
    {
        _map = new Dictionary<string, AnimalEntry>(StringComparer.OrdinalIgnoreCase);
        foreach (var entry in animalEntries)
        {
            if (!string.IsNullOrEmpty(entry.animalName))
                _map[entry.animalName] = entry;
        }

        // 첫 번째 동물만 활성화
        bool first = true;
        foreach (var entry in animalEntries)
        {
            if (entry.rootObject != null)
                entry.rootObject.SetActive(first);
            first = false;
        }

        if (animalEntries.Count > 0)
            _current = animalEntries[0].animalName;
    }

    // ──────────────────────────────────────────────────────────
    // 외부 호출 API
    // ──────────────────────────────────────────────────────────

    /// <summary>
    /// 지정한 동물로 전환한다. WebSocketClient에서 호출한다.
    /// </summary>
    public void SwitchTo(string animalName)
    {
        if (!_map.TryGetValue(animalName, out var next))
        {
            Debug.LogWarning($"[AnimalSwitcher] 알 수 없는 동물: {animalName}");
            return;
        }

        // 현재 동물 비활성화
        if (_current != null && _map.TryGetValue(_current, out var prev))
        {
            prev.rootObject?.SetActive(false);
            prev.controller?.SetIdle();
        }

        // 다음 동물 활성화
        next.rootObject?.SetActive(true);
        _current = animalName;

        if (animalNameText != null)
            animalNameText.text = animalName;

        Debug.Log($"[AnimalSwitcher] 동물 전환: {animalName}");
    }

    public string CurrentAnimal => _current;

    /// <summary>
    /// 현재 동물의 AnimalController를 반환한다.
    /// </summary>
    public AnimalController GetCurrentController()
    {
        if (_current != null && _map.TryGetValue(_current, out var entry))
            return entry.controller;
        return null;
    }
}
