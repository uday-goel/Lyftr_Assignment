export const API_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");

function readableError(status, body) {
  const detail = body && body.detail;
  if (status === 503) return "All AI providers are temporarily unavailable. Please try again in a moment.";
  if (status === 422) {
    if (Array.isArray(detail)) {
      const first = detail[0] || {};
      return `Invalid request: ${first.msg || "please check your message"}`;
    }
    return `Invalid request: ${typeof detail === "string" ? detail : "please check your message"}`;
  }
  return typeof detail === "string" ? detail : `Something went wrong (HTTP ${status}).`;
}

export async function sendChat({ sessionId, message, systemPrompt, simulateFailure }) {
  const payload = { session_id: sessionId, message };
  if (systemPrompt && systemPrompt.trim()) payload.system_prompt = systemPrompt.trim();
  const headers = { "Content-Type": "application/json" };
  if (simulateFailure && simulateFailure.trim()) {
    headers["X-Simulate-Failure"] = simulateFailure.trim();
  }
  let res;
  try {
    res = await fetch(`${API_URL}/chat`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
  } catch {
    throw new Error("Cannot reach the server. Check your connection and try again.");
  }
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error body */ }
  if (!res.ok) throw new Error(readableError(res.status, body));
  return body;
}

export async function getSessionMessages(sessionId) {
  try {
    const res = await fetch(`${API_URL}/sessions/${sessionId}/messages`);
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

export async function getHealth() {
  try {
    const res = await fetch(`${API_URL}/health`);
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}
