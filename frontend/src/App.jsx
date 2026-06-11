import { memo, useCallback, useEffect, useRef, useState } from "react";
import { calculateLevel, prepareAudioChunk } from "./audio";

const SOCKET_URL = "ws://localhost:8000/ws/meetings/live";
const API_URL = "http://localhost:8000";

function formatTime(milliseconds) {
  const totalSeconds = Math.floor((Number(milliseconds) || 0) / 1000);
  const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function joinTranscriptText(previous, next) {
  if (!previous) {
    return next;
  }
  if (!next) {
    return previous;
  }
  const needsSpace = /[A-Za-z0-9]$/.test(previous) && /^[A-Za-z0-9]/.test(next);
  return `${previous}${needsSpace ? " " : ""}${next}`;
}

function findActiveSegment(segments, currentMs) {
  for (let index = (segments?.length || 0) - 1; index >= 0; index -= 1) {
    const segment = segments[index];
    if (currentMs >= segment.start_ms && currentMs <= segment.end_ms) {
      return segment;
    }
  }
  return null;
}

function speakerAccent(speakerId) {
  const palette = ["#8ca5ff", "#68e7b2", "#ef9f78", "#c596ff", "#65c8e8", "#e4c66e"];
  const match = String(speakerId || "").match(/(\d+)$/);
  const index = match ? Math.max(0, Number(match[1]) - 1) : 0;
  return palette[index % palette.length];
}

const LiveSegment = memo(function LiveSegment({
  segment,
  previewText,
  previewSpeaker,
  onChange,
}) {
  return (
    <article className="segment" style={{ "--speaker-accent": speakerAccent(segment.speakerId) }}>
      <div className="timeline-mark">
        <span>{formatTime(segment.startMs)}</span>
        <i />
      </div>
      <div className="segment-content">
        <div className="segment-meta">
          <strong className="speaker-name">{segment.speaker}</strong>
          <span>{formatTime(segment.startMs)} - {formatTime(segment.endMs)}</span>
        </div>
        <textarea
          value={segment.text}
          rows={Math.max(3, Math.ceil(segment.text.length / 35))}
          onChange={(event) => onChange(segment.id, event.target.value)}
        />
        {previewText && (
          <>
            {previewSpeaker && <span className="preview-speaker">可能是 {previewSpeaker}</span>}
            <p className="inline-preview">{previewText}</p>
          </>
        )}
        {segment.text !== segment.originalText && <span className="edited-mark">已编辑</span>}
      </div>
    </article>
  );
});

const HistorySegment = memo(function HistorySegment({
  active,
  index,
  segment,
  onPlay,
  onSeek,
  onChange,
}) {
  return (
    <article
      id={`history-segment-${segment.id}`}
      className={`history-segment ${active ? "playing" : ""}`}
      style={{ "--speaker-accent": speakerAccent(segment.speaker_id) }}
      onClick={() => onPlay(segment)}
    >
      <button
        className="segment-time"
        onClick={(event) => {
          event.stopPropagation();
          onPlay(segment);
        }}
      >
        <strong>{String(index + 1).padStart(2, "0")}</strong>
        <span>{formatTime(segment.start_ms)}</span>
      </button>
      <div>
        <strong className="speaker-name" title={segment.speaker_status === "uncertain" ? "说话人识别置信度较低" : ""}>
          {segment.speaker}
          {segment.speaker_status === "uncertain" && <span className="speaker-uncertain">待确认</span>}
        </strong>
        <textarea
          value={segment.text}
          rows={Math.max(2, Math.ceil(segment.text.length / 38))}
          onClick={(event) => {
            event.stopPropagation();
            onSeek(segment);
          }}
          onChange={(event) => onChange(segment.id, event.target.value)}
        />
      </div>
    </article>
  );
});

function AnalysisState({ meeting, onGenerate }) {
  const status = meeting.analysis_status || "not_started";
  if (status === "processing") {
    return (
      <div className="analysis-state">
        <span className="analysis-spinner" />
        <h3>AI 正在整理这场会议</h3>
        <p>正在提炼摘要、核心要点与明确待办，完成后页面会自动更新。</p>
      </div>
    );
  }
  if (status === "failed") {
    return (
      <div className="analysis-state">
        <span className="analysis-state-mark">!</span>
        <h3>AI 分析未完成</h3>
        <p>{meeting.analysis_error || "请检查 LLM 服务配置后重试。"}</p>
        <button onClick={onGenerate}>重新生成</button>
      </div>
    );
  }
  return (
    <div className="analysis-state">
      <span className="analysis-state-mark">AI</span>
      <h3>生成这场会议的 AI 纪要</h3>
      <p>系统将基于会议原文生成摘要、核心要点、会议结论与待办事项。</p>
      <button onClick={onGenerate}>开始生成</button>
    </div>
  );
}

function InsightList({ title, items = [], onSource, emptyText }) {
  return (
    <section className="insight-section">
      <div className="insight-heading">
        <p className="section-label">{title}</p>
        <span>{String(items.length).padStart(2, "0")}</span>
      </div>
      {items.length === 0 && <p className="insight-empty">{emptyText}</p>}
      {items.map((item, index) => (
        <button
          className="insight-item"
          key={`${title}-${index}`}
          onClick={() => onSource(item.source_segment_ids)}
        >
          <strong>{String(index + 1).padStart(2, "0")}</strong>
          <span>{item.text}</span>
          {item.source_segment_ids?.length > 0 && <small>查看原文 →</small>}
        </button>
      ))}
    </section>
  );
}

function AnalysisInsights({ meeting, onSource, onGenerate }) {
  if (meeting.analysis_status !== "completed" || !meeting.analysis) {
    return <AnalysisState meeting={meeting} onGenerate={onGenerate} />;
  }
  const analysis = meeting.analysis;
  return (
    <div className="analysis-content">
      <section className="summary-card">
        <p className="section-label">MEETING SUMMARY</p>
        <h3>本次会议摘要</h3>
        <p>{analysis.summary || "本次会议没有足够内容可供总结。"}</p>
      </section>
      <InsightList title="核心要点" items={analysis.key_points} onSource={onSource} emptyText="未提取到明确核心要点。" />
      <InsightList title="会议结论" items={analysis.decisions} onSource={onSource} emptyText="会议中没有形成明确结论。" />
      <InsightList title="未决问题" items={analysis.open_questions} onSource={onSource} emptyText="未识别到尚未解决的问题。" />
    </div>
  );
}

function AnalysisTasks({ meeting, onSource, onGenerate }) {
  if (meeting.analysis_status !== "completed" || !meeting.analysis) {
    return <AnalysisState meeting={meeting} onGenerate={onGenerate} />;
  }
  const tasks = meeting.analysis.action_items || [];
  return (
    <div className="analysis-content task-content">
      <div className="task-intro">
        <div>
          <p className="section-label">ACTION ITEMS</p>
          <h3>待办事项</h3>
        </div>
        <strong>{String(tasks.length).padStart(2, "0")}</strong>
      </div>
      {tasks.length === 0 && (
        <div className="analysis-state compact">
          <span className="analysis-state-mark">✓</span>
          <h3>本次会议未识别到明确待办</h3>
          <p>AI 不会根据讨论内容自行编造任务。</p>
        </div>
      )}
      {tasks.map((task, index) => (
        <button className="task-card" key={`task-${index}`} onClick={() => onSource(task.source_segment_ids)}>
          <span className="task-check" />
          <div>
            <strong>{task.task}</strong>
            <p>负责人：{task.owner || "未指定"} · 截止时间：{task.deadline || "未指定"}</p>
          </div>
          {task.source_segment_ids?.length > 0 && <small>查看原文 →</small>}
        </button>
      ))}
    </div>
  );
}

function App() {
  const [devices, setDevices] = useState([]);
  const [deviceId, setDeviceId] = useState("");
  const [status, setStatus] = useState("idle");
  const [level, setLevel] = useState(0);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [preview, setPreview] = useState(null);
  const [segments, setSegments] = useState([]);
  const [notice, setNotice] = useState("请选择麦克风，然后开始会议");
  const [meetingTitle, setMeetingTitle] = useState("");
  const [deviceMenuOpen, setDeviceMenuOpen] = useState(false);
  const [view, setView] = useState("home");
  const [createOpen, setCreateOpen] = useState(false);
  const [meetings, setMeetings] = useState([]);
  const [selectedMeeting, setSelectedMeeting] = useState(null);
  const [historyTab, setHistoryTab] = useState("transcript");
  const [pendingSourceId, setPendingSourceId] = useState(null);
  const [currentMeetingId, setCurrentMeetingId] = useState(null);
  const [activeSegmentId, setActiveSegmentId] = useState(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [audioCurrentTime, setAudioCurrentTime] = useState(0);
  const [audioDuration, setAudioDuration] = useState(0);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [speakerProfiles, setSpeakerProfiles] = useState([]);
  const [speakerName, setSpeakerName] = useState("");
  const [speakerRecording, setSpeakerRecording] = useState(false);
  const [speakerRecordingMs, setSpeakerRecordingMs] = useState(0);
  const [speakerLevel, setSpeakerLevel] = useState(0);
  const [speakerTargetId, setSpeakerTargetId] = useState(null);
  const [speakerNotice, setSpeakerNotice] = useState("录制一段自然、清晰的语音来创建声纹身份。");
  const [speeches, setSpeeches] = useState([]);
  const [selectedSpeech, setSelectedSpeech] = useState(null);
  const [speechPrompt, setSpeechPrompt] = useState("");
  const [speechBusy, setSpeechBusy] = useState(false);
  const [speechNotice, setSpeechNotice] = useState("描述你需要的演讲稿，AI 会自动理解并撰写完整正文。");

  const socketRef = useRef(null);
  const streamRef = useRef(null);
  const contextRef = useRef(null);
  const sourceRef = useRef(null);
  const workletRef = useRef(null);
  const startedAtRef = useRef(0);
  const pausedAtRef = useRef(0);
  const totalPausedRef = useRef(0);
  const statusRef = useRef(status);
  const audioRef = useRef(null);
  const deviceMenuRef = useRef(null);
  const historySegmentsRef = useRef(null);
  const lastLevelUpdateRef = useRef(0);
  const lastLevelRef = useRef(0);
  const speakerCaptureRef = useRef(null);
  const speakerTimerRef = useRef(null);
  const speechContentRef = useRef(null);

  async function wait(milliseconds) {
    await new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  }

  async function fetchJsonWithRetry(url, options = {}, retryCount = 1) {
    let lastError = null;
    for (let attempt = 0; attempt <= retryCount; attempt += 1) {
      try {
        const response = await fetch(url, options);
        const result = await response.json();
        if (response.ok) {
          return result;
        }
        const detail = result?.detail || "请求失败";
        if (attempt < retryCount && [500, 502, 503].includes(response.status)) {
          await wait(700);
          continue;
        }
        throw new Error(detail);
      } catch (error) {
        lastError = error;
        if (attempt < retryCount) {
          await wait(700);
          continue;
        }
      }
    }
    throw lastError || new Error("请求失败");
  }

  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  useEffect(() => {
    refreshDevices();
    loadMeetings();
    loadSpeakerProfiles();
    loadSpeeches();
    navigator.mediaDevices?.addEventListener("devicechange", refreshDevices);
    return () => navigator.mediaDevices?.removeEventListener("devicechange", refreshDevices);
  }, []);

  useEffect(() => {
    function closeDeviceMenu(event) {
      if (!deviceMenuRef.current?.contains(event.target)) {
        setDeviceMenuOpen(false);
      }
    }
    document.addEventListener("pointerdown", closeDeviceMenu);
    return () => document.removeEventListener("pointerdown", closeDeviceMenu);
  }, []);

  useEffect(() => {
    if (status !== "recording") {
      return undefined;
    }
    const timer = window.setInterval(() => {
      setElapsedMs(Date.now() - startedAtRef.current - totalPausedRef.current);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [status]);

  useEffect(() => () => {
    closeResources();
    closeSpeakerCapture();
  }, []);

  useEffect(() => {
    if (selectedMeeting?.analysis_status !== "processing") {
      return undefined;
    }
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${API_URL}/api/meetings/${selectedMeeting.id}`);
        const data = await response.json();
        setSelectedMeeting(data);
      } catch {
        // Keep the current analysis state and retry on the next interval.
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [selectedMeeting?.id, selectedMeeting?.analysis_status]);

  useEffect(() => {
    const textarea = speechContentRef.current;
    if (!textarea || !selectedSpeech) {
      return;
    }
    textarea.style.height = "auto";
    textarea.style.height = `${Math.max(850, textarea.scrollHeight)}px`;
  }, [selectedSpeech?.id, selectedSpeech?.content]);

  useEffect(() => {
    if (historyTab !== "transcript" || !pendingSourceId) {
      return undefined;
    }
    const frame = window.requestAnimationFrame(() => {
      const segment = selectedMeeting?.segments?.find((item) => item.id === pendingSourceId);
      const element = document.getElementById(`history-segment-${pendingSourceId}`);
      const container = historySegmentsRef.current;
      const audio = audioRef.current;
      if (segment && audio) {
        audio.pause();
        audio.currentTime = segment.start_ms / 1000;
        setAudioCurrentTime(segment.start_ms / 1000);
      }
      if (element && container) {
        const containerRect = container.getBoundingClientRect();
        const elementRect = element.getBoundingClientRect();
        const targetTop = container.scrollTop
          + elementRect.top
          - containerRect.top
          - container.clientHeight / 2
          + elementRect.height / 2;
        container.scrollTo({
          top: Math.max(0, targetTop),
          behavior: "smooth",
        });
      }
      setPendingSourceId(null);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [historyTab, pendingSourceId, selectedMeeting?.segments]);

  async function refreshDevices() {
    if (!navigator.mediaDevices?.enumerateDevices) {
      setNotice("当前浏览器不支持麦克风采集");
      return;
    }
    const list = await navigator.mediaDevices.enumerateDevices();
    const inputs = list.filter((device) => device.kind === "audioinput");
    setDevices(inputs);
    setDeviceId((current) => current || inputs[0]?.deviceId || "");
  }

  async function loadSpeakerProfiles() {
    try {
      const response = await fetch(`${API_URL}/api/speakers`);
      if (!response.ok) {
        throw new Error("无法读取声纹库");
      }
      setSpeakerProfiles(await response.json());
    } catch (error) {
      setSpeakerNotice(error.message || "无法读取声纹库");
    }
  }

  async function loadSpeeches() {
    try {
      const response = await fetch(`${API_URL}/api/speeches`);
      const data = await response.json();
      setSpeeches(Array.isArray(data) ? data : []);
    } catch {
      setSpeechNotice("无法读取历史演讲稿");
    }
  }

  async function generateSpeech() {
    if (!speechPrompt.trim() || speechBusy) {
      return;
    }
    try {
      setSpeechBusy(true);
      setSpeechNotice("AI 正在撰写演讲稿...");
      const result = await fetchJsonWithRetry(`${API_URL}/api/speeches`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: speechPrompt.trim() }),
      });
      setSelectedSpeech(result);
      setSpeechPrompt("");
      setSpeechNotice("演讲稿已生成，可以继续编辑或导出 Word。");
      await loadSpeeches();
    } catch (error) {
      setSpeechNotice(error.message || "生成演讲稿失败");
    } finally {
      setSpeechBusy(false);
    }
  }

  async function saveSpeech() {
    if (!selectedSpeech || speechBusy) {
      return false;
    }
    try {
      setSpeechBusy(true);
      const response = await fetch(`${API_URL}/api/speeches/${selectedSpeech.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: selectedSpeech.title,
          content: selectedSpeech.content,
        }),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || "保存演讲稿失败");
      }
      setSelectedSpeech(result);
      setSpeechNotice("修改已保存。");
      await loadSpeeches();
      return true;
    } catch (error) {
      setSpeechNotice(error.message || "保存演讲稿失败");
      return false;
    } finally {
      setSpeechBusy(false);
    }
  }

  async function exportSelectedSpeech() {
    if (!selectedSpeech || speechBusy) {
      return;
    }
    const speechId = selectedSpeech.id;
    const saved = await saveSpeech();
    if (saved) {
      window.location.href = `${API_URL}/api/speeches/${speechId}/export/docx`;
    }
  }

  async function regenerateSelectedSpeech() {
    if (!selectedSpeech || speechBusy) {
      return;
    }
    try {
      setSpeechBusy(true);
      setSpeechNotice("AI 正在根据原始描述重新撰写...");
      const result = await fetchJsonWithRetry(`${API_URL}/api/speeches/${selectedSpeech.id}/regenerate`, {
        method: "POST",
      });
      setSelectedSpeech(result);
      setSpeechNotice("演讲稿已重新生成。");
      await loadSpeeches();
    } catch (error) {
      setSpeechNotice(error.message || "重新生成失败");
    } finally {
      setSpeechBusy(false);
    }
  }

  async function deleteSelectedSpeech() {
    if (!selectedSpeech || !window.confirm(`确认删除“${selectedSpeech.title}”吗？`)) {
      return;
    }
    const response = await fetch(`${API_URL}/api/speeches/${selectedSpeech.id}`, {
      method: "DELETE",
    });
    if (response.ok) {
      setSelectedSpeech(null);
      setSpeechNotice("演讲稿已删除。");
      await loadSpeeches();
    }
  }

  function handleMessage(event) {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      setNotice("收到无法解析的转写消息");
      return;
    }
    if (message.type === "meeting.started") {
      setCurrentMeetingId(message.meeting_id || null);
      setNotice("会议转写进行中");
    } else if (message.type === "transcript.preview") {
      setPreview((current) => ({
        ...(current?.id === message.segment_id ? current : {}),
        id: message.segment_id || "preview",
        text: String(message.text || ""),
        startMs: Number(message.start_ms) || 0,
        endMs: Number(message.end_ms) || 0,
      }));
    } else if (message.type === "speaker.preview") {
      setPreview((current) => ({
        ...(current?.id === message.segment_id ? current : {}),
        id: message.segment_id || "preview",
        text: current?.id === message.segment_id ? current.text : "",
        speaker: message.speaker || "",
        speakerStatus: message.speaker_status || "",
      }));
    } else if (message.type === "transcript.final") {
      const finalText = String(message.text || "");
      if (!finalText) {
        return;
      }
      setPreview((current) => current?.id === message.segment_id ? null : current);
      setSegments((current) => {
        const speaker = message.speaker || "发言人";
        const speakerId = message.speaker_id || "";
        const last = current[current.length - 1];
        if (last && last.speakerId === speakerId) {
          const text = joinTranscriptText(last.text, finalText);
          const originalText = joinTranscriptText(last.originalText, finalText);
          return [
            ...current.slice(0, -1),
            {
              ...last,
              text,
              originalText,
              endMs: Number(message.end_ms) || last.endMs,
              speakerConfidence: Number(message.speaker_confidence) || last.speakerConfidence,
              speakerStatus: message.speaker_status || last.speakerStatus,
            },
          ];
        }
        return [
          ...current,
          {
            id: message.segment_id,
            text: finalText,
            originalText: finalText,
            speaker,
            speakerId,
            speakerConfidence: Number(message.speaker_confidence) || 0,
            speakerStatus: message.speaker_status || "disabled",
            startMs: Number(message.start_ms) || 0,
            endMs: Number(message.end_ms) || 0,
          },
        ];
      });
    } else if (message.type === "error") {
      setNotice(message.message);
    } else if (message.type === "meeting.ended") {
      closeResources();
      setStatus("ended");
      setPreview(null);
      setNotice("会议已结束");
      loadMeetings();
    }
  }

  async function loadMeetings() {
    try {
      const response = await fetch(`${API_URL}/api/meetings`);
      const data = await response.json();
      setMeetings(Array.isArray(data) ? data : []);
    } catch {
      setNotice("无法读取历史会议");
    }
  }

  async function openHistory(preferredMeetingId = null) {
    setView("history");
    try {
      const response = await fetch(`${API_URL}/api/meetings`);
      const data = await response.json();
      const list = Array.isArray(data) ? data : [];
      setMeetings(list);
      const requestedId = typeof preferredMeetingId === "string" ? preferredMeetingId : null;
      const selectedId = list.some((meeting) => meeting.id === selectedMeeting?.id)
        ? selectedMeeting.id
        : null;
      const targetId = requestedId || selectedId || list[0]?.id;
      if (targetId) {
        await openMeeting(targetId);
      }
    } catch {
      setNotice("无法读取历史会议");
    }
  }

  async function openCreateMeeting() {
    await refreshDevices();
    setMeetingTitle("");
    setCreateOpen(true);
  }

  async function openMeeting(meetingId) {
    const response = await fetch(`${API_URL}/api/meetings/${meetingId}`);
    const data = await response.json();
    setSelectedMeeting(data);
    setActiveSegmentId(null);
    setHistoryTab("transcript");
  }

  async function generateAnalysis() {
    if (!selectedMeeting) {
      return;
    }
    setSelectedMeeting((current) => ({ ...current, analysis_status: "processing", analysis_error: null }));
    try {
      const response = await fetch(`${API_URL}/api/meetings/${selectedMeeting.id}/analysis`, {
        method: "POST",
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "无法启动 AI 分析");
      }
    } catch (error) {
      setSelectedMeeting((current) => ({
        ...current,
        analysis_status: "failed",
        analysis_error: error.message,
      }));
    }
  }

  function openAnalysisSource(sourceIds = []) {
    const segment = selectedMeeting?.segments?.find((item) => sourceIds.includes(item.id));
    if (!segment) {
      return;
    }
    setActiveSegmentId(segment.id);
    setPendingSourceId(segment.id);
    setHistoryTab("transcript");
  }

  const playSegment = useCallback((segment) => {
    const audio = audioRef.current;
    if (!audio) {
      return;
    }
    setActiveSegmentId(segment.id);
    setAudioCurrentTime(segment.start_ms / 1000);
    audio.currentTime = segment.start_ms / 1000;
    audio.play();
  }, []);

  const seekSegment = useCallback((segment) => {
    const audio = audioRef.current;
    if (audio) {
      setActiveSegmentId(segment.id);
      setAudioCurrentTime(segment.start_ms / 1000);
      audio.currentTime = segment.start_ms / 1000;
    }
  }, []);

  function handleAudioTimeUpdate() {
    const currentTime = audioRef.current?.currentTime || 0;
    setAudioCurrentTime(currentTime);
    const currentMs = currentTime * 1000;
    const active = findActiveSegment(selectedMeeting?.segments, currentMs);
    setActiveSegmentId(active?.id || null);
  }

  function toggleAudioPlayback() {
    const audio = audioRef.current;
    if (!audio) {
      return;
    }
    if (audio.paused) {
      audio.play();
    } else {
      audio.pause();
    }
  }

  function skipAudio(seconds) {
    const audio = audioRef.current;
    if (audio) {
      audio.currentTime = Math.max(0, Math.min(audio.duration || 0, audio.currentTime + seconds));
    }
  }

  function seekAudio(event) {
    const audio = audioRef.current;
    if (audio) {
      audio.currentTime = Number(event.target.value);
    }
  }

  function changePlaybackRate() {
    const rates = [1, 1.25, 1.5, 2, 0.75];
    const nextRate = rates[(rates.indexOf(playbackRate) + 1) % rates.length];
    setPlaybackRate(nextRate);
    if (audioRef.current) {
      audioRef.current.playbackRate = nextRate;
    }
  }

  const updateHistorySegment = useCallback(async (segmentId, text) => {
    setSelectedMeeting((current) => ({
      ...current,
      segments: current.segments.map((segment) => (
        segment.id === segmentId ? { ...segment, text } : segment
      )),
    }));
    await fetch(`${API_URL}/api/meetings/${selectedMeeting.id}/segments/${segmentId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
  }, [selectedMeeting?.id]);

  async function connectSocket() {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(SOCKET_URL);
      socket.binaryType = "arraybuffer";
      socket.onopen = () => {
        socketRef.current = socket;
        socket.send(JSON.stringify({
          type: "meeting.start",
          title: meetingTitle.trim() || "未命名会议",
          sample_rate: 16000,
          channels: 1,
          format: "pcm_s16le",
        }));
        resolve();
      };
      socket.onmessage = handleMessage;
      socket.onerror = () => reject(new Error("无法连接转写服务"));
      socket.onclose = () => {
        socketRef.current = null;
        if (statusRef.current === "recording") {
          setNotice("转写服务连接已断开");
          setStatus("ended");
        }
      };
    });
  }

  async function startCapture() {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: deviceId ? { exact: deviceId } : undefined,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    await refreshDevices();

    const context = new AudioContext();
    await context.audioWorklet.addModule("/audio-capture-worklet.js");
    const source = context.createMediaStreamSource(stream);
    const worklet = new AudioWorkletNode(context, "audio-capture-processor");
    const silentGain = context.createGain();
    silentGain.gain.value = 0;

    worklet.port.onmessage = ({ data }) => {
      const nextLevel = calculateLevel(data);
      const now = performance.now();
      if (
        now - lastLevelUpdateRef.current >= 100
        && Math.abs(nextLevel - lastLevelRef.current) >= 0.02
      ) {
        lastLevelUpdateRef.current = now;
        lastLevelRef.current = nextLevel;
        setLevel(nextLevel);
      }
      if (statusRef.current !== "recording" || socketRef.current?.readyState !== WebSocket.OPEN) {
        return;
      }
      const pcm = prepareAudioChunk(data, context.sampleRate);
      socketRef.current.send(pcm.buffer);
    };

    source.connect(worklet);
    worklet.connect(silentGain);
    silentGain.connect(context.destination);
    streamRef.current = stream;
    contextRef.current = context;
    sourceRef.current = source;
    workletRef.current = worklet;
  }

  async function startMeeting() {
    try {
      setStatus("connecting");
      setNotice("正在连接转写服务...");
      setSegments([]);
      setPreview(null);
      setElapsedMs(0);
      totalPausedRef.current = 0;
      await connectSocket();
      await startCapture();
      startedAtRef.current = Date.now();
      setStatus("recording");
      setNotice("会议转写进行中");
      setCreateOpen(false);
      setView("live");
    } catch (error) {
      closeResources();
      setStatus("idle");
      setNotice(error.message || "无法启动会议");
    }
  }

  async function togglePause() {
    if (status === "recording") {
      pausedAtRef.current = Date.now();
      setStatus("paused");
      setNotice("会议已暂停，音频不会发送");
      socketRef.current?.send(JSON.stringify({ type: "meeting.pause" }));
    } else {
      totalPausedRef.current += Date.now() - pausedAtRef.current;
      await contextRef.current?.resume();
      setStatus("recording");
      setNotice("会议转写进行中");
      socketRef.current?.send(JSON.stringify({ type: "meeting.resume" }));
    }
  }

  function closeResources() {
    workletRef.current?.disconnect();
    sourceRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    contextRef.current?.close();
    socketRef.current?.close();
    workletRef.current = null;
    sourceRef.current = null;
    streamRef.current = null;
    contextRef.current = null;
    socketRef.current = null;
    setLevel(0);
  }

  function closeSpeakerCapture() {
    const capture = speakerCaptureRef.current;
    capture?.worklet?.disconnect();
    capture?.source?.disconnect();
    capture?.stream?.getTracks().forEach((track) => track.stop());
    capture?.context?.close();
    speakerCaptureRef.current = null;
    if (speakerTimerRef.current) {
      window.clearInterval(speakerTimerRef.current);
      speakerTimerRef.current = null;
    }
    setSpeakerRecording(false);
    setSpeakerLevel(0);
  }

  async function startSpeakerRecording(profileId = null) {
    if (isActive) {
      setSpeakerNotice("会议进行中无法注册声纹，请结束会议后再录制。");
      return;
    }
    if (!profileId && !speakerName.trim()) {
      setSpeakerNotice("请先输入需要注册的姓名。");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          deviceId: deviceId ? { exact: deviceId } : undefined,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      const context = new AudioContext();
      await context.audioWorklet.addModule("/audio-capture-worklet.js");
      const source = context.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(context, "audio-capture-processor");
      const silentGain = context.createGain();
      silentGain.gain.value = 0;
      const chunks = [];
      const startedAt = Date.now();
      worklet.port.onmessage = ({ data }) => {
        setSpeakerLevel(calculateLevel(data));
        chunks.push(prepareAudioChunk(data, context.sampleRate));
      };
      source.connect(worklet);
      worklet.connect(silentGain);
      silentGain.connect(context.destination);
      speakerCaptureRef.current = { stream, context, source, worklet, chunks, startedAt };
      setSpeakerTargetId(profileId);
      setSpeakerRecordingMs(0);
      setSpeakerRecording(true);
      setSpeakerNotice(profileId ? "正在追加声音样本，请自然说话。" : "正在录制首条声纹样本，请自然说话。");
      speakerTimerRef.current = window.setInterval(() => {
        setSpeakerRecordingMs(Date.now() - startedAt);
      }, 100);
    } catch (error) {
      closeSpeakerCapture();
      setSpeakerNotice(error.message || "无法启动麦克风");
    }
  }

  async function finishSpeakerRecording() {
    const capture = speakerCaptureRef.current;
    if (!capture) {
      return;
    }
    const duration = Date.now() - capture.startedAt;
    const chunks = capture.chunks;
    const profileId = speakerTargetId;
    closeSpeakerCapture();
    if (duration < 3000) {
      setSpeakerNotice("录音至少需要 3 秒，请重新录制。");
      return;
    }
    const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const pcm = new Int16Array(totalLength);
    let offset = 0;
    chunks.forEach((chunk) => {
      pcm.set(chunk, offset);
      offset += chunk.length;
    });
    try {
      setSpeakerNotice("正在提取声纹并保存...");
      const url = profileId
        ? `${API_URL}/api/speakers/${profileId}/samples`
        : `${API_URL}/api/speakers?name=${encodeURIComponent(speakerName.trim())}`;
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: pcm.buffer,
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || "声纹注册失败");
      }
      setSpeakerName("");
      setSpeakerTargetId(null);
      setSpeakerNotice(profileId ? "声音样本已追加。" : "声纹身份已创建。");
      await loadSpeakerProfiles();
    } catch (error) {
      setSpeakerNotice(error.message || "声纹注册失败");
    }
  }

  async function deleteSpeakerProfile(profile) {
    if (!window.confirm(`确认删除“${profile.name}”的声纹资料吗？历史会议不会被删除。`)) {
      return;
    }
    const response = await fetch(`${API_URL}/api/speakers/${profile.id}`, { method: "DELETE" });
    if (response.ok) {
      setSpeakerNotice(`已删除“${profile.name}”的声纹资料。`);
      await loadSpeakerProfiles();
    } else {
      setSpeakerNotice("删除声纹身份失败。");
    }
  }

  function endMeeting() {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type: "meeting.end" }));
      setStatus("ending");
      setNotice("正在保存最后一段转写...");
      return;
    }
    closeResources();
    setStatus("ended");
    setNotice("会议已结束");
  }

  const updateSegment = useCallback((id, text) => {
    setSegments((current) => current.map((segment) => (
      segment.id === id ? { ...segment, text } : segment
    )));
  }, []);

  const canStart = status === "idle" || status === "ended";
  const isActive = status === "recording" || status === "paused" || status === "ending";

  function renderDevicePicker() {
    return (
      <div className="device-picker" ref={deviceMenuRef}>
        <label className="field-label" htmlFor="microphone">音频来源</label>
        <button
          id="microphone"
          className={`device-trigger ${deviceMenuOpen ? "open" : ""}`}
          type="button"
          disabled={isActive}
          aria-haspopup="listbox"
          aria-expanded={deviceMenuOpen}
          onClick={() => setDeviceMenuOpen((open) => !open)}
        >
          <span>
            {devices.find((device) => device.deviceId === deviceId)?.label
              || (devices.length ? "选择麦克风" : "等待麦克风授权")}
          </span>
          <i />
        </button>
        {deviceMenuOpen && !isActive && (
          <div className="device-menu" role="listbox">
            <div className="device-menu-glow" />
            {devices.map((device, index) => (
              <button
                type="button"
                role="option"
                aria-selected={device.deviceId === deviceId}
                className={device.deviceId === deviceId ? "selected" : ""}
                key={device.deviceId}
                onClick={() => {
                  setDeviceId(device.deviceId);
                  setDeviceMenuOpen(false);
                }}
              >
                <span className="device-option-icon" />
                <span className="device-option-copy">
                  <strong>{device.label || `麦克风 ${index + 1}`}</strong>
                  <small>{device.deviceId === "default" ? "系统默认输入设备" : "可用音频输入"}</small>
                </span>
                {device.deviceId === deviceId && <b>✓</b>}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className={`app-shell view-${view}`}>
      <div className="stage-light stage-light-one" />
      <div className="stage-light stage-light-two" />
      <header className="topbar">
        <button className="brand" type="button" onClick={() => setView("home")}>
          <span className="brand-mark">会</span>
          <div>
            <span className="eyebrow">HUIYI INTELLIGENCE</span>
            <h1>会议智能记录</h1>
          </div>
        </button>
        <nav
          className="glass-nav"
          style={{
            "--nav-count": isActive ? 5 : 4,
            "--nav-index": view === "home" ? 0 : view === "live" ? 1 : view === "history" ? (isActive ? 2 : 1) : view === "speeches" ? (isActive ? 3 : 2) : (isActive ? 4 : 3),
          }}
        >
          <span className="nav-indicator" aria-hidden="true" />
          <button className={view === "home" ? "active" : ""} onClick={() => setView("home")}>首页</button>
          {isActive && <button className={view === "live" ? "active" : ""} onClick={() => setView("live")}>会议现场</button>}
          <button className={view === "history" ? "active" : ""} onClick={openHistory}>历史记录</button>
          <button className={view === "speeches" ? "active" : ""} onClick={() => { setView("speeches"); loadSpeeches(); }}>演讲稿</button>
          <button className={view === "speakers" ? "active" : ""} onClick={() => { setView("speakers"); loadSpeakerProfiles(); }}>声纹库</button>
        </nav>
        {isActive ? <div className="meeting-summary">
          <div className={`live-state ${status}`}>
            <span className="status-dot" />
            {status === "recording" ? "正在转写" : notice}
          </div>
          <span className="summary-divider" />
          <strong>{formatTime(elapsedMs)}</strong>
        </div> : <button className="top-create" onClick={openCreateMeeting}>会议记录</button>}
      </header>

      <main className="workspace">
        {view === "home" && (
          <section className="landing">
            <div className="hero-copy">
              <p className="hero-kicker">VOICE · MEMORY · INTELLIGENCE</p>
              <h2>让每一次发言<br />都成为清晰记录</h2>
              <p className="hero-description">从实时转写到完整回放，把会议里的声音沉淀为可搜索、可编辑、可追溯的内容资产。</p>
              <div className="hero-actions">
                <button className="hero-primary" onClick={openCreateMeeting}><span />会议记录</button>
                <button className="hero-secondary" onClick={openHistory}>浏览会议档案</button>
              </div>
            </div>
            <div className="voice-art" aria-hidden="true">
              <div className="voice-orbit orbit-one" />
              <div className="voice-orbit orbit-two" />
              <div className="voice-core">
                {Array.from({ length: 24 }).map((_, index) => <i key={index} style={{ "--i": index }} />)}
              </div>
              <span>LIVE<br />VOICE</span>
            </div>
            <div className="recent-section">
              <div className="recent-heading">
                <div><p className="section-label">RECENT ARCHIVE</p><h3>最近会议</h3></div>
                <button onClick={openHistory}>查看全部 →</button>
              </div>
              <div className="recent-grid">
                {meetings.slice(0, 3).map((meeting, index) => (
                  <button className="recent-card" key={meeting.id} onClick={() => { setView("history"); openMeeting(meeting.id); }}>
                    <span className="recent-index">0{index + 1}</span>
                    <strong>{meeting.title}</strong>
                    <p>{new Date(meeting.started_at).toLocaleString()}</p>
                    <small>{formatTime(meeting.duration_ms)}</small>
                  </button>
                ))}
                {meetings.length === 0 && <div className="recent-card empty-card"><span>+</span><strong>你的第一场会议</strong><p>创建后将在这里形成内容档案</p></div>}
              </div>
            </div>
          </section>
        )}

        {view === "live" && <section className="focus-workspace">
          <div className="focus-heading">
            <div>
              <p className="section-label">LIVE TRANSCRIPT</p>
              <h2>{meetingTitle || "未命名会议"}</h2>
            </div>
            <div className="focus-controls">
              {(status === "recording" || status === "paused") ? (
                <>
                  <div className="live-meter"><div style={{ width: `${level * 100}%` }} /></div>
                  <button onClick={togglePause}>{status === "paused" ? "继续" : "暂停"}</button>
                  <button className="end-control" onClick={endMeeting}>结束会议</button>
                </>
              ) : (
                <>
                  <span className="ended-label">会议已结束</span>
                  <button onClick={() => setView("home")}>返回首页</button>
                  <button onClick={() => openHistory(currentMeetingId)}>查看档案</button>
                </>
              )}
            </div>
          </div>

          <div className="focus-document">
            {segments.length === 0 && !preview && (
              <div className="listening-state">
                <span className="listening-dot" />
                <h3>正在聆听</h3>
                <p>开始说话，文字会在这里持续生长。</p>
              </div>
            )}

            {segments.map((segment, index) => (
              <LiveSegment
                key={segment.id}
                segment={segment}
                previewText={index === segments.length - 1 ? preview?.text : ""}
                previewSpeaker={index === segments.length - 1 ? preview?.speaker : ""}
                onChange={updateSegment}
              />
            ))}

            {preview && segments.length === 0 && (
              <article className="segment preview">
                <div className="timeline-mark">
                  <span>现在</span>
                  <i />
                </div>
                <div className="segment-content">
                  <div className="segment-meta">
                    <strong>{preview.speaker ? `可能是 ${preview.speaker}` : "实时转写"}</strong>
                    <span>正在聆听</span>
                  </div>
                  <p className="inline-preview">{preview.text}</p>
                </div>
              </article>
            )}
          </div>
        </section>}

        {view === "history" && (
          <section className="history-layout">
            <aside className="history-list">
              <div className="history-heading">
                <p className="section-label">会议档案</p>
                <h2>历史记录</h2>
              </div>
              {meetings.length === 0 && <p className="history-empty">还没有保存的会议</p>}
              {meetings.map((meeting) => (
                <button
                  className={`history-item ${selectedMeeting?.id === meeting.id ? "active" : ""}`}
                  key={meeting.id}
                  onClick={() => openMeeting(meeting.id)}
                >
                  <strong>{meeting.title}</strong>
                  <span>{new Date(meeting.started_at).toLocaleString()}</span>
                  <small>{formatTime(meeting.duration_ms)}</small>
                </button>
              ))}
            </aside>
            <section className="history-document">
              {!selectedMeeting && (
                <div className="archive-empty">
                  <div className="archive-orbit"><span>ARC</span></div>
                  <p className="section-label">MEETING ARCHIVE</p>
                  <h3>从一场会议开始回看</h3>
                  <p>选择左侧会议，查看转写正文、播放完整录音，<br />并通过文字跳转到对应时刻。</p>
                </div>
              )}
              {selectedMeeting && (
                <>
                  <div className="playback-bar">
                    <div>
                      <p className="section-label">会议回放</p>
                      <h2>{selectedMeeting.title}</h2>
                    </div>
                    <div className="archive-actions">
                      <button
                        className="analysis-button"
                        disabled={selectedMeeting.analysis_status === "processing"}
                        onClick={generateAnalysis}
                      >
                        {selectedMeeting.analysis_status === "processing" ? "AI 整理中…" : "生成 AI 纪要"}
                      </button>
                      <a
                        className="export-button"
                        href={`${API_URL}/api/meetings/${selectedMeeting.id}/export/docx`}
                      >
                        导出 Word
                      </a>
                    </div>
                  </div>
                  <div className="archive-tabs">
                    <button className={historyTab === "transcript" ? "active" : ""} onClick={() => setHistoryTab("transcript")}>会议原文</button>
                    <button className={historyTab === "insights" ? "active" : ""} onClick={() => setHistoryTab("insights")}>AI 纪要</button>
                    <button className={historyTab === "tasks" ? "active" : ""} onClick={() => setHistoryTab("tasks")}>
                      待办事项
                      {selectedMeeting.analysis?.action_items?.length > 0 && <span>{selectedMeeting.analysis.action_items.length}</span>}
                    </button>
                  </div>
                  {historyTab === "transcript" && (
                    <>
                      <div className="history-segments" ref={historySegmentsRef}>
                        {selectedMeeting.segments.map((segment, index) => (
                          <HistorySegment
                            key={segment.id}
                            active={activeSegmentId === segment.id}
                            index={index}
                            segment={segment}
                            onPlay={playSegment}
                            onSeek={seekSegment}
                            onChange={updateHistorySegment}
                          />
                        ))}
                      </div>
                      <audio
                        ref={audioRef}
                        className="native-audio"
                        src={`${API_URL}${selectedMeeting.audio_url}`}
                        onTimeUpdate={handleAudioTimeUpdate}
                        onLoadedMetadata={(event) => setAudioDuration(event.currentTarget.duration || 0)}
                        onPlay={() => setIsPlaying(true)}
                        onPause={() => setIsPlaying(false)}
                        onEnded={() => setIsPlaying(false)}
                      />
                      <div className="glass-player">
                        <button className="skip-button" onClick={() => skipAudio(-10)} aria-label="后退10秒">−10</button>
                        <button className={`play-button ${isPlaying ? "playing" : ""}`} onClick={toggleAudioPlayback} aria-label={isPlaying ? "暂停" : "播放"}>
                          <i />
                        </button>
                        <button className="skip-button" onClick={() => skipAudio(10)} aria-label="前进10秒">+10</button>
                        <span className="player-time">{formatTime(audioCurrentTime * 1000)}</span>
                        <input
                          className="player-progress"
                          type="range"
                          min="0"
                          max={audioDuration || 0}
                          step="0.01"
                          value={Math.min(audioCurrentTime, audioDuration || 0)}
                          onChange={seekAudio}
                          style={{ "--progress": `${audioDuration ? (audioCurrentTime / audioDuration) * 100 : 0}%` }}
                        />
                        <span className="player-time">{formatTime(audioDuration * 1000)}</span>
                        <button className="rate-button" onClick={changePlaybackRate}>{playbackRate}x</button>
                      </div>
                    </>
                  )}
                  {historyTab === "insights" && (
                    <AnalysisInsights meeting={selectedMeeting} onSource={openAnalysisSource} onGenerate={generateAnalysis} />
                  )}
                  {historyTab === "tasks" && (
                    <AnalysisTasks meeting={selectedMeeting} onSource={openAnalysisSource} onGenerate={generateAnalysis} />
                  )}
                </>
              )}
            </section>
          </section>
        )}

        {view === "speakers" && (
          <section className="speaker-library">
            <div className="speaker-library-heading">
              <div>
                <p className="section-label">VOICE IDENTITY</p>
                <h2>声纹库</h2>
                <p>为常用参会者保存多个声音样本，让新的会议自动显示真实姓名。</p>
              </div>
              <div className={`speaker-record-status ${speakerRecording ? "recording" : ""}`}>
                <span />
                {speakerRecording ? `录制中 ${formatTime(speakerRecordingMs)}` : `${speakerProfiles.length} 个身份`}
              </div>
            </div>

            <div className="speaker-register-panel">
              <div className="speaker-register-copy">
                <p className="hero-kicker">NEW VOICE PROFILE</p>
                <h3>{speakerTargetId ? "追加声音样本" : "注册新的声音身份"}</h3>
                <p>{speakerNotice}</p>
              </div>
              <div className="speaker-register-controls">
                {!speakerTargetId && (
                  <input
                    value={speakerName}
                    maxLength={40}
                    placeholder="输入姓名，例如：张三"
                    disabled={speakerRecording}
                    onChange={(event) => setSpeakerName(event.target.value)}
                  />
                )}
                <div className="speaker-level"><i style={{ width: `${speakerLevel * 100}%` }} /></div>
                {speakerRecording ? (
                  <button className="speaker-stop" onClick={finishSpeakerRecording}>完成录制</button>
                ) : (
                  <button className="speaker-record" onClick={() => startSpeakerRecording(speakerTargetId)}>
                    <span />{speakerTargetId ? "录制追加样本" : "开始录制"}
                  </button>
                )}
                {speakerTargetId && !speakerRecording && <button className="speaker-cancel" onClick={() => setSpeakerTargetId(null)}>取消</button>}
              </div>
            </div>

            <div className="speaker-profile-grid">
              {speakerProfiles.length === 0 && (
                <div className="speaker-profile-empty">
                  <strong>还没有注册声音</strong>
                  <p>建议每位参会者录制 2 至 3 条不同时间、不同距离的自然语音。</p>
                </div>
              )}
              {speakerProfiles.map((profile, index) => (
                <article className="speaker-profile-card" key={profile.id} style={{ "--speaker-accent": speakerAccent(`speaker_${index + 1}`) }}>
                  <div className="speaker-avatar">{profile.name.slice(0, 1)}</div>
                  <div className="speaker-profile-copy">
                    <strong>{profile.name}</strong>
                    <span>{profile.sample_count} 条声音样本</span>
                    <small>更新于 {new Date(profile.updated_at).toLocaleString()}</small>
                  </div>
                  <div className="speaker-samples">
                    {profile.samples.map((sample, sampleIndex) => (
                      <audio key={sample.id} controls preload="none" src={`${API_URL}${sample.audio_url}`} title={`样本 ${sampleIndex + 1}`} />
                    ))}
                  </div>
                  <div className="speaker-profile-actions">
                    <button onClick={() => { setSpeakerTargetId(profile.id); setSpeakerNotice(`为“${profile.name}”追加声音样本。`); }}>追加样本</button>
                    <button className="danger" onClick={() => deleteSpeakerProfile(profile)}>删除</button>
                  </div>
                </article>
              ))}
            </div>
          </section>
        )}

        {view === "speeches" && (
          <section className="speech-workspace">
            <aside className="speech-history">
              <div className="speech-history-heading">
                <div><p className="section-label">SPEECH ARCHIVE</p><h2>演讲稿</h2></div>
                <button onClick={() => { setSelectedSpeech(null); setSpeechPrompt(""); setSpeechNotice("描述你需要的演讲稿，AI 会自动理解并撰写完整正文。"); }}>新建</button>
              </div>
              {speeches.length === 0 && <p className="history-empty">还没有保存的演讲稿</p>}
              {speeches.map((speech) => (
                <button
                  className={`speech-history-item ${selectedSpeech?.id === speech.id ? "active" : ""}`}
                  key={speech.id}
                  onClick={() => setSelectedSpeech(speech)}
                >
                  <strong>{speech.title}</strong>
                  <span>{speech.word_count} 字 · 约 {speech.estimated_minutes} 分钟</span>
                  <small>{new Date(speech.updated_at).toLocaleString()}</small>
                </button>
              ))}
            </aside>

            <div className="speech-studio">
              {!selectedSpeech ? (
                <div className="speech-generator">
                  <p className="hero-kicker">AI SPEECH WRITER</p>
                  <h2>把想法整理成一篇<br />可以直接朗读的演讲稿</h2>
                  <p>只需描述你的需求。涉及近期事件、具体数据或内部信息时，请在描述中一并提供相关材料。</p>
                  <textarea
                    value={speechPrompt}
                    placeholder="例如：帮我写一篇关于人工智能发展的五分钟演讲稿，面向公司同事，语气专业但不要太生硬，重点介绍大模型、AI 智能体和多模态技术。"
                    onChange={(event) => setSpeechPrompt(event.target.value)}
                  />
                  <div className="speech-generator-footer">
                    <span>{speechNotice}</span>
                    <button disabled={!speechPrompt.trim() || speechBusy} onClick={generateSpeech}>
                      {speechBusy ? "正在生成..." : "生成演讲稿"}
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="speech-toolbar">
                    <div>
                      <p className="section-label">AI SPEECH DRAFT</p>
                      <span>{selectedSpeech.word_count} 字 · 预计朗读 {selectedSpeech.estimated_minutes} 分钟</span>
                    </div>
                    <div>
                      <button disabled={speechBusy} onClick={regenerateSelectedSpeech}>重新生成</button>
                      <button disabled={speechBusy} onClick={saveSpeech}>保存修改</button>
                      <button disabled={speechBusy} onClick={exportSelectedSpeech}>导出 Word</button>
                      <button className="danger" onClick={deleteSelectedSpeech}>删除</button>
                    </div>
                  </div>
                  <div className="speech-paper-wrap">
                    <article className="speech-paper">
                      <input
                        className="speech-title-input"
                        value={selectedSpeech.title}
                        onChange={(event) => setSelectedSpeech((current) => ({ ...current, title: event.target.value }))}
                      />
                      <textarea
                        ref={speechContentRef}
                        className="speech-content-input"
                        value={selectedSpeech.content}
                        onChange={(event) => setSelectedSpeech((current) => ({ ...current, content: event.target.value }))}
                      />
                    </article>
                  </div>
                  <div className="speech-status">{speechNotice}</div>
                </>
              )}
            </div>
          </section>
        )}
      </main>

      {createOpen && (
        <div className="modal-backdrop" onMouseDown={() => setCreateOpen(false)}>
          <section className="create-modal" onMouseDown={(event) => event.stopPropagation()}>
            <button className="modal-close" onClick={() => setCreateOpen(false)}>×</button>
            <p className="hero-kicker">NEW SESSION</p>
            <h2>开始一场新的会议</h2>
            <p className="modal-description">设置会议名称与音频来源，开始后系统将持续保存录音和转写内容。</p>
            <label className="modal-field">
              <span>会议名称</span>
              <input value={meetingTitle} autoFocus maxLength={80} placeholder="例如：产品周会" onChange={(event) => setMeetingTitle(event.target.value)} />
            </label>
            <div className="modal-field">{renderDevicePicker()}</div>
            <button className="modal-start" disabled={!canStart || status === "connecting"} onClick={startMeeting}>
              <span className="record-icon" />{status === "connecting" ? "正在连接..." : "开始会议"}
            </button>
          </section>
        </div>
      )}
    </div>
  );
}

export default App;
