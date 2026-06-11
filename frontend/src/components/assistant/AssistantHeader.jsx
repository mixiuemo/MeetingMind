export default function AssistantHeader({ context, loading, onClose, onNewSession }) {
  return (
    <div className="assistant-header">
      <div>
        <p className="section-label">AI ASSISTANT</p>
        <h3>AI 助手 · {context.title}</h3>
        <span>{context.targetTitle || (loading ? "正在连接上下文..." : "当前页面已自动绑定")}</span>
      </div>
      <div className="assistant-header-actions">
        <button type="button" onClick={onNewSession}>新对话</button>
        <button type="button" className="assistant-close" onClick={onClose}>×</button>
      </div>
    </div>
  );
}
