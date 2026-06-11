import { createPortal } from "react-dom";
import useAssistantViewport from "./useAssistantViewport";

export default function AssistantLauncher({ open, onToggle, title }) {
  const viewport = useAssistantViewport();
  const size = viewport.width <= 900 ? 60 : 66;
  const rightGap = viewport.width <= 900 ? 18 : 28;
  const bottomGap = viewport.width <= 900 ? 18 : 28;
  const style = {
    left: `${viewport.left + viewport.width - size - rightGap}px`,
    top: `${viewport.top + viewport.height - size - bottomGap}px`,
    width: `${size}px`,
    height: `${size}px`,
  };

  return createPortal(
    <button
      className={`assistant-launcher ${open ? "open" : ""}`}
      style={style}
      type="button"
      aria-label={open ? "关闭 AI 助手" : "打开 AI 助手"}
      title={title}
      onClick={onToggle}
    >
      <span className="assistant-launcher-ring" />
      <span className="assistant-launcher-core">AI</span>
    </button>,
    document.body,
  );
}
