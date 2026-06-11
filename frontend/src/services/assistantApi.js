const API_URL = "http://localhost:8000";

async function readJson(response) {
  const result = await response.json();
  if (!response.ok) {
    throw new Error(result?.detail || "请求失败");
  }
  return result;
}

export async function listAssistantSessions(mode, targetId) {
  const search = new URLSearchParams();
  if (mode) {
    search.set("mode", mode);
  }
  if (targetId) {
    search.set("target_id", targetId);
  }
  const response = await fetch(`${API_URL}/api/chat/sessions?${search.toString()}`);
  return readJson(response);
}

export async function createAssistantSession(payload) {
  const response = await fetch(`${API_URL}/api/chat/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function getAssistantSession(sessionId) {
  const response = await fetch(`${API_URL}/api/chat/sessions/${sessionId}`);
  return readJson(response);
}

export async function sendAssistantMessage(sessionId, content) {
  const response = await fetch(`${API_URL}/api/chat/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  return readJson(response);
}

export async function sendAssistantMessageStream(sessionId, content, onEvent) {
  const response = await fetch(`${API_URL}/api/chat/sessions/${sessionId}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error?.detail || "请求失败");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const data = line.slice(6);
          if (data.trim()) {
            try {
              const event = JSON.parse(data);
              onEvent(event);
            } catch (error) {
              console.error("Failed to parse SSE data:", error);
            }
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export async function reviseSpeechWithAssistant(speechId, sessionId, instruction) {
  const response = await fetch(`${API_URL}/api/speeches/${speechId}/revise`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruction, session_id: sessionId }),
  });
  return readJson(response);
}

export async function deleteAssistantSession(sessionId) {
  const response = await fetch(`${API_URL}/api/chat/sessions/${sessionId}`, {
    method: "DELETE",
  });
  return readJson(response);
}
