export function buildAssistantContext(view, selectedMeeting, selectedSpeech) {
  if (view === "history") {
    return {
      mode: "meeting",
      title: "会议助手",
      targetId: selectedMeeting?.id || null,
      targetTitle: selectedMeeting?.title || "",
      quickActions: ["总结当前会议", "提炼待办事项", "生成汇报口径"],
    };
  }
  if (view === "speeches") {
    return {
      mode: "speech",
      title: "演讲稿助手",
      targetId: selectedSpeech?.id || null,
      targetTitle: selectedSpeech?.title || "",
      quickActions: ["压缩到2分钟", "改成更正式", "优化结尾"],
    };
  }
  return {
    mode: "free",
    title: "自由聊天",
    targetId: null,
    targetTitle: "",
    quickActions: ["帮我整理一个想法", "陪我聊聊", "给我一个表达建议"],
  };
}
