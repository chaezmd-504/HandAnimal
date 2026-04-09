// MainThreadDispatcher.cs
// WebSocket 스레드에서 수신한 데이터를 Unity 메인 스레드에서 처리하기 위한 디스패처.
// 씬에 빈 GameObject를 만들고 이 컴포넌트를 붙이면 된다.
//
// 사용 예:
//   MainThreadDispatcher.Enqueue(() => transform.rotation = ...);

using System;
using System.Collections.Generic;
using UnityEngine;

public class MainThreadDispatcher : MonoBehaviour
{
    private static MainThreadDispatcher _instance;

    private readonly Queue<Action> _queue = new Queue<Action>();
    private readonly object _lock = new object();

    public static MainThreadDispatcher Instance
    {
        get
        {
            if (_instance == null)
                Debug.LogError("[MainThreadDispatcher] 씬에 인스턴스가 없습니다. GameObject를 추가하세요.");
            return _instance;
        }
    }

    private void Awake()
    {
        if (_instance != null && _instance != this)
        {
            Destroy(gameObject);
            return;
        }
        _instance = this;
        DontDestroyOnLoad(gameObject);
    }

    /// <summary>
    /// 다른 스레드에서 Unity 메인 스레드로 액션을 예약한다.
    /// </summary>
    public static void Enqueue(Action action)
    {
        if (_instance == null) return;
        lock (_instance._lock)
        {
            _instance._queue.Enqueue(action);
        }
    }

    private void Update()
    {
        lock (_lock)
        {
            while (_queue.Count > 0)
                _queue.Dequeue()?.Invoke();
        }
    }
}
