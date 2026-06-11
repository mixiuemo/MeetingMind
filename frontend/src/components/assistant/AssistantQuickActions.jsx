export default function AssistantQuickActions({ actions, disabled, onSelect }) {
  if (!actions?.length) {
    return null;
  }
  return (
    <div className="assistant-quick-actions">
      {actions.map((action) => (
        <button key={action} type="button" disabled={disabled} onClick={() => onSelect(action)}>
          {action}
        </button>
      ))}
    </div>
  );
}
