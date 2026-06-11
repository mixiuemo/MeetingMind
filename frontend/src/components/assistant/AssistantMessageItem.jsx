function formatChatTime(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

export default function AssistantMessageItem({ message, streaming }) {
  const waiting = streaming && !message.content;

  return (
    <article className={`assistant-message ${message.role} ${streaming ? "streaming" : ""}`}>
      <div className="assistant-message-bubble">
        {waiting ? (
          <div className="assistant-stream-waiting" aria-live="polite">
            <span>AI 助手正在生成</span>
            <i />
            <i />
            <i />
          </div>
        ) : (
          <p>{message.content}{streaming && <span className="cursor" aria-hidden="true" />}</p>
        )}
        <small>{message.role === "assistant" ? "AI 助手" : "你"} · {formatChatTime(message.created_at)}</small>
      </div>
    </article>
  );
}
