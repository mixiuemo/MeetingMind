import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  createAssistantSession,
  getAssistantSession,
  listAssistantSessions,
  reviseSpeechWithAssistant,
  sendAssistantMessageStream,
} from "../../services/assistantApi";
import AssistantComposer from "./AssistantComposer";
import AssistantHeader from "./AssistantHeader";
import AssistantMessageList from "./AssistantMessageList";
import AssistantQuickActions from "./AssistantQuickActions";
import useAssistantViewport from "./useAssistantViewport";
import "./assistant.css";

export default function AssistantPanel({
  open,
  context,
  onClose,
  onSpeechUpdated,
}) {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [streamingMessage, setStreamingMessage] = useState(null);
  const viewport = useAssistantViewport();
  const streamingRef = useRef(false);

  useEffect(() => {
    if (!open) {
      return;
    }
    if ((context.mode === "meeting" || context.mode === "speech") && !context.targetId) {
      setSession(null);
      setError("");
      return;
    }
    let cancelled = false;
    async function bootstrap() {
      try {
        setLoading(true);
        setError("");
        const sessions = await listAssistantSessions(context.mode, context.targetId);
        const latest = sessions[0];
        const nextSession = latest
          ? await getAssistantSession(latest.id)
          : await createAssistantSession({
            mode: context.mode,
            target_id: context.targetId,
          });
        if (!cancelled) {
          setSession(nextSession);
        }
      } catch (nextError) {
        if (!cancelled) {
          setError(nextError.message || "无法连接 AI 助手");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    bootstrap();
    return () => {
      cancelled = true;
    };
  }, [open, context.mode, context.targetId]);

  async function startNewSession() {
    if ((context.mode === "meeting" || context.mode === "speech") && !context.targetId) {
      return;
    }
    try {
      setLoading(true);
      setError("");
      const nextSession = await createAssistantSession({
        mode: context.mode,
        target_id: context.targetId,
      });
      setSession(nextSession);
    } catch (nextError) {
      setError(nextError.message || "无法创建新对话");
    } finally {
      setLoading(false);
    }
  }

  async function pushMessage(content) {
    if (!session?.id || streamingRef.current) {
      return;
    }
    try {
      streamingRef.current = true;
      setLoading(true);
      setError("");

      // 立即添加用户消息到UI
      const tempUserMessage = {
        id: `temp-user-${Date.now()}`,
        role: "user",
        content: content,
      };

      setSession(prev => ({
        ...prev,
        messages: [...(prev.messages || []), tempUserMessage],
      }));

      // 初始化流式消息
      setStreamingMessage({
        id: `temp-assistant-${Date.now()}`,
        role: "assistant",
        content: "",
      });

      if (context.mode === "speech") {
        const result = await reviseSpeechWithAssistant(context.targetId, session.id, content);
        await onSpeechUpdated?.(result.speech, result.revision);
        setSession(result.session);
        setStreamingMessage(null);
        return;
      }

      let finalResult = null;

      await sendAssistantMessageStream(session.id, content, (event) => {
        if (event.type === "start") {
          // 更新消息ID
          setStreamingMessage(prev => ({
            ...prev,
            id: event.assistant_message_id,
          }));
        } else if (event.type === "content") {
          // 追加内容
          setStreamingMessage(prev => ({
            ...prev,
            content: (prev?.content || "") + event.content,
          }));
        } else if (event.type === "done") {
          // 保存最终结果
          finalResult = event;
        } else if (event.type === "error") {
          setError(event.error || "发送失败");
        }
      });

      // 更新会话状态
      if (finalResult) {
        setSession(prev => ({
          ...prev,
          messages: finalResult.messages,
          summary: finalResult.summary,
        }));
      }

      setStreamingMessage(null);
    } catch (nextError) {
      setError(nextError.message || "发送失败");
      setStreamingMessage(null);
    } finally {
      setLoading(false);
      streamingRef.current = false;
    }
  }

  const needTarget = (context.mode === "meeting" || context.mode === "speech") && !context.targetId;
  const gutter = viewport.width <= 900 ? 12 : 24;
  const panelWidth = Math.min(408, viewport.width - gutter * 2);
  const panelHeight = Math.min(640, viewport.height - gutter * 2);
  const panelStyle = {
    left: `${viewport.left + viewport.width - panelWidth - gutter}px`,
    top: `${viewport.top + viewport.height - panelHeight - gutter}px`,
    width: `${panelWidth}px`,
    height: `${panelHeight}px`,
  };

  return createPortal(
    <section className={`assistant-panel ${open ? "open" : ""}`} style={panelStyle}>
      <AssistantHeader context={context} loading={loading} onClose={onClose} onNewSession={startNewSession} />
      {needTarget ? (
        <div className="assistant-empty bind-required">
          <strong>{context.mode === "meeting" ? "先选择一场会议" : "先选择一篇演讲稿"}</strong>
          <p>助手会自动绑定当前对象，然后围绕它继续上下文对话。</p>
        </div>
      ) : (
        <>
          <AssistantMessageList
            messages={session?.messages || []}
            streamingMessage={streamingMessage}
            loading={loading}
            error={error}
            emptyText={context.mode === "free" ? "现在可以开始自由聊天了" : `现在可以围绕${context.targetTitle || context.title}继续提问`}
          />
          <AssistantQuickActions actions={context.quickActions} disabled={loading || needTarget} onSelect={pushMessage} />
          <AssistantComposer
            disabled={loading || needTarget}
            placeholder={context.mode === "free" ? "聊点什么？" : "直接说你的需求，例如：把这一段改正式一些"}
            onSend={pushMessage}
          />
        </>
      )}
    </section>,
    document.body,
  );
}
