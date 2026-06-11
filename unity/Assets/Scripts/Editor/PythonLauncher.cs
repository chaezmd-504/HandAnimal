// PythonLauncher.cs
// HandAvatar > Python Launcher 메뉴에서 열기.
// Unity 에디터 안에서 Python 프로세스를 시작/중지하고 로그를 확인한다.
// cmd 창을 따로 켤 필요 없음.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;

public class PythonLauncherWindow : EditorWindow
{
    // ── 경로 설정 ──────────────────────────────────────────
    private string _mainPyPath  = "";
    private string _condaEnv    = "capstone_env";
    private bool   _useCondaRun = true;   // false → 직접 python 경로 지정
    private string _pythonExe   = "python";

    // ── 실행 인수 ──────────────────────────────────────────
    private static readonly string[] Animals   = { "spider", "butterfly", "fish", "octopus", "snake", "horse" };
    private static readonly string[] Mappings  = { "blend", "keyframe", "direct" };
    private int   _animalIdx   = 0;
    private int   _mappingIdx  = 0;
    private bool  _locomotion  = true;
    private bool  _gaze        = false;
    private bool  _distLog     = false;
    private int   _distLogN    = 30;
    private float _temperature = 8f;
    private string _extraArgs  = "";

    // Play 모드 연동
    private bool _autoPlay = false;

    // ── 프로세스 ───────────────────────────────────────────
    private Process          _proc;
    private readonly List<string> _log     = new();
    private readonly object       _logLock = new();
    private Vector2          _scrollPos;
    private bool             _autoScroll = true;
    private bool             _dirty;          // Repaint 플래그

    [MenuItem("HandAvatar/Python Launcher")]
    public static void ShowWindow() =>
        GetWindow<PythonLauncherWindow>("Python Launcher");

    // ── 초기화 ─────────────────────────────────────────────
    private void OnEnable()
    {
        // main.py 자동 감지: Assets 기준 ../../python/main.py
        string candidate = Path.GetFullPath(
            Path.Combine(Application.dataPath, "../../python/main.py"));
        if (File.Exists(candidate))
            _mainPyPath = candidate.Replace('\\', '/');

        EditorApplication.update += OnEditorUpdate;
    }

    private void OnDisable()
    {
        EditorApplication.update -= OnEditorUpdate;
    }

    private void OnEditorUpdate()
    {
        if (_dirty) { _dirty = false; Repaint(); }
    }

    // ── GUI ────────────────────────────────────────────────
    private void OnGUI()
    {
        // ─ 경로 설정 ─
        EditorGUILayout.LabelField("경로 설정", EditorStyles.boldLabel);

        EditorGUILayout.BeginHorizontal();
        _mainPyPath = EditorGUILayout.TextField("main.py", _mainPyPath);
        if (GUILayout.Button("…", GUILayout.Width(28)))
        {
            string p = EditorUtility.OpenFilePanel("main.py 선택", "", "py");
            if (!string.IsNullOrEmpty(p)) _mainPyPath = p.Replace('\\', '/');
        }
        EditorGUILayout.EndHorizontal();

        _useCondaRun = EditorGUILayout.Toggle("conda run 사용", _useCondaRun);
        if (_useCondaRun)
            _condaEnv  = EditorGUILayout.TextField("conda 환경", _condaEnv);
        else
            _pythonExe = EditorGUILayout.TextField("python 경로", _pythonExe);

        EditorGUILayout.Space(8);

        // ─ 실행 인수 ─
        EditorGUILayout.LabelField("실행 인수", EditorStyles.boldLabel);

        _animalIdx  = EditorGUILayout.Popup("--animal",  _animalIdx,  Animals);
        _mappingIdx = EditorGUILayout.Popup("--mapping", _mappingIdx, Mappings);
        _locomotion = EditorGUILayout.Toggle("--locomotion", _locomotion);
        _gaze       = EditorGUILayout.Toggle("--gaze (시선 제어)", _gaze);

        EditorGUILayout.BeginHorizontal();
        _distLog = EditorGUILayout.Toggle("--dist-log", _distLog);
        EditorGUI.BeginDisabledGroup(!_distLog);
        _distLogN = EditorGUILayout.IntField(_distLogN, GUILayout.Width(50));
        EditorGUI.EndDisabledGroup();
        EditorGUILayout.EndHorizontal();

        _extraArgs = EditorGUILayout.TextField("추가 인수", _extraArgs);

        // 명령어 미리보기
        EditorGUILayout.HelpBox(BuildPreview(), MessageType.None);

        EditorGUILayout.Space(4);
        _autoPlay = EditorGUILayout.Toggle("Python 시작 시 PlayMode 진입", _autoPlay);
        EditorGUILayout.Space(8);

        // ─ 버튼 ─
        bool running = IsRunning();

        EditorGUILayout.BeginHorizontal();

        EditorGUI.BeginDisabledGroup(running);
        Color prev = GUI.backgroundColor;
        GUI.backgroundColor = new Color(0.3f, 0.8f, 0.3f);
        if (GUILayout.Button("▶  시작", GUILayout.Height(32)))
            StartPython();
        GUI.backgroundColor = prev;
        EditorGUI.EndDisabledGroup();

        EditorGUI.BeginDisabledGroup(!running);
        GUI.backgroundColor = new Color(0.9f, 0.3f, 0.3f);
        if (GUILayout.Button("■  중지", GUILayout.Height(32)))
            StopPython();
        GUI.backgroundColor = prev;
        EditorGUI.EndDisabledGroup();

        GUI.backgroundColor = new Color(0.8f, 0.7f, 0.2f);
        if (GUILayout.Button("↺  재시작", GUILayout.Height(32)))
            RestartPython();
        GUI.backgroundColor = prev;

        EditorGUILayout.EndHorizontal();

        // 상태 표시
        var statusStyle = new GUIStyle(EditorStyles.miniLabel);
        statusStyle.normal.textColor = running ? new Color(0.3f, 1f, 0.3f) : Color.gray;
        EditorGUILayout.LabelField(running ? "● 실행 중" : "○ 정지", statusStyle);

        EditorGUILayout.Space(6);

        // ─ 로그 ─
        EditorGUILayout.BeginHorizontal();
        EditorGUILayout.LabelField("로그", EditorStyles.boldLabel);
        if (GUILayout.Button("지우기", GUILayout.Width(55)))
            lock (_logLock) _log.Clear();
        _autoScroll = GUILayout.Toggle(_autoScroll, "자동 스크롤", GUILayout.Width(88));
        EditorGUILayout.EndHorizontal();

        float logH = Mathf.Max(150f, position.height - 350f);
        _scrollPos = EditorGUILayout.BeginScrollView(_scrollPos, GUILayout.Height(logH));
        lock (_logLock)
        {
            foreach (var line in _log)
            {
                bool isErr  = line.StartsWith("[ERR]");
                bool isSys  = line.StartsWith("[SYS]");
                var s = new GUIStyle(EditorStyles.label) { wordWrap = true, fontSize = 11 };
                s.normal.textColor = isErr ? new Color(1f, 0.4f, 0.4f)
                                   : isSys ? new Color(0.6f, 0.8f, 1f)
                                   : Color.white;
                GUILayout.Label(line, s);
            }
        }
        EditorGUILayout.EndScrollView();
    }

    // ── 명령어 미리보기 ────────────────────────────────────
    private string BuildPreview()
    {
        string args = BuildArgs();
        if (_useCondaRun)
            return $"conda run -n {_condaEnv} python main.py {args}";
        return $"{_pythonExe} main.py {args}";
    }

    private string BuildArgs()
    {
        var sb = new StringBuilder();
        sb.Append($"--animal {Animals[_animalIdx]}");
        sb.Append($" --mapping {Mappings[_mappingIdx]}");
        if (_locomotion)   sb.Append(" --locomotion");
        if (_gaze)         sb.Append(" --gaze");
        if (_distLog)      sb.Append($" --dist-log {_distLogN}");
        if (_temperature != 8f) sb.Append($" --temperature {_temperature:F1}");
        if (!string.IsNullOrWhiteSpace(_extraArgs)) sb.Append($" {_extraArgs.Trim()}");
        return sb.ToString();
    }

    // ── 프로세스 제어 ──────────────────────────────────────
    private void StartPython()
    {
        if (!File.Exists(_mainPyPath))
        {
            AddLog("[ERR] main.py 경로가 올바르지 않습니다.");
            return;
        }

        string workDir = Path.GetDirectoryName(_mainPyPath);
        string fileName, arguments;

        if (_useCondaRun)
        {
            fileName  = "cmd.exe";
            arguments = $"/c chcp 65001 >nul && conda run -n {_condaEnv} --no-capture-output python -X utf8 -u main.py {BuildArgs()}";
        }
        else
        {
            fileName  = _pythonExe;
            arguments = $"-X utf8 main.py {BuildArgs()}";
        }

        _proc = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName               = fileName,
                Arguments              = arguments,
                WorkingDirectory       = workDir,
                UseShellExecute        = false,
                RedirectStandardOutput = true,
                RedirectStandardError  = true,
                StandardOutputEncoding = System.Text.Encoding.UTF8,
                StandardErrorEncoding  = System.Text.Encoding.UTF8,
                CreateNoWindow         = true,
            },
            EnableRaisingEvents = true,
        };

        _proc.OutputDataReceived += (_, e) => { if (e.Data != null) AddLog(e.Data); };
        _proc.ErrorDataReceived  += (_, e) => { if (e.Data != null) AddLog("[ERR] " + e.Data); };
        _proc.Exited             += (_, _) => AddLog("[SYS] 프로세스 종료됨");

        _proc.Start();
        _proc.BeginOutputReadLine();
        _proc.BeginErrorReadLine();

        AddLog($"[SYS] 시작: {fileName} {arguments}");

        if (_autoPlay && !EditorApplication.isPlaying)
            EditorApplication.delayCall += () => EditorApplication.EnterPlaymode();
    }

    private void StopPython()
    {
        if (_proc != null && !_proc.HasExited)
        {
            // Windows: taskkill로 자식 프로세스(python.exe)까지 정리
            try
            {
                var kill = new Process
                {
                    StartInfo = new ProcessStartInfo
                    {
                        FileName        = "taskkill",
                        Arguments       = $"/PID {_proc.Id} /T /F",
                        UseShellExecute = false,
                        CreateNoWindow  = true,
                    }
                };
                kill.Start();
                kill.WaitForExit(2000);
            }
            catch { _proc.Kill(); }

            AddLog("[SYS] 중지됨");
        }
    }

    private void RestartPython()
    {
        StopPython();
        // 잠깐 기다린 후 재시작 (포트 해제 대기)
        EditorApplication.delayCall += () =>
        {
            System.Threading.Thread.Sleep(600);
            StartPython();
        };
    }

    private bool IsRunning() => _proc != null && !_proc.HasExited;

    private void AddLog(string line)
    {
        lock (_logLock)
        {
            _log.Add(line);
            if (_log.Count > 800) _log.RemoveRange(0, 100);
        }
        if (_autoScroll) _scrollPos = new Vector2(0, float.MaxValue);
        _dirty = true;
    }

    private void OnDestroy() => StopPython();
}
