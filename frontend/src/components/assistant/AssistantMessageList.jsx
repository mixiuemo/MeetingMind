import { useEffect, useRef } from "react";
import AssistantMessageItem from "./AssistantMessageItem";

export default function AssistantMessageList({ messages, streamingMessage, loading, error, emptyText }) {
  const listRef = useRef(null);

  useEffect(() => {
    const element = listRef.current;
    if (element) {
      element.scrollTop = element.scrollHeight;
    }
  }, [messages, streamingMessage, loading, error]);

  return (
    <div className="assistant-messages" ref={listRef}>
      {messages.length === 0 && !loading && !error && !streamingMessage && (
        <div className="assistant-empty">
          <strong>{emptyText}</strong>
          <p>你可以直接发一句自然语言，AI 会结合当前页面内容回答。</p>
        </div>
      )}
      {error && (
        <div className="assistant-error">
          <strong>这次对话没有成功</strong>
          <p>{error}</p>
        </div>
      )}
      {messages.map((message) => <AssistantMessageItem key={message.id} message={message} />)}
      {streamingMessage && <AssistantMessageItem key={streamingMessage.id} message={streamingMessage} streaming />}
      {loading && !streamingMessage && (
        <div className="assistant-thinking">
          <span />
          <span />
          <span />
        </div>
      )}
    </div>
  );
}
