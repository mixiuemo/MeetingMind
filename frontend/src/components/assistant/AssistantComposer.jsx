import { useState } from "react";

export default function AssistantComposer({ disabled, placeholder, onSend }) {
  const [draft, setDraft] = useState("");

  async function submit() {
    const content = draft.trim();
    if (!content || disabled) {
      return;
    }
    setDraft("");
    await onSend(content);
  }

  return (
    <div className="assistant-composer">
      <textarea
        value={draft}
        disabled={disabled}
        placeholder={placeholder}
        rows={3}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <button type="button" disabled={disabled || !draft.trim()} onClick={submit}>发送</button>
    </div>
  );
}
