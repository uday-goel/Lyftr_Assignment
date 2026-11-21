import { useCallback, useEffect, useRef, useState } from "react";
import { getHealth, sendChat } from "./api.js";

const MAX_LEN = 4000;
const SUGGESTIONS = [
  "What is the capital of France?",
  "Explain caching in two sentences.",
  "Give me three tips for a great demo video.",
];

const newSessionId = () => `s-${crypto.randomUUID().slice(0, 8)}`;

function Meta({ m }) {
  const cached = m.resolution_source === "LLM_CACHE";
  return (
    <div className="meta">
      <span className={`pill ${cached ? "pill-cache" : "pill-live"}`} data-testid="meta-source">
        {m.resolution_source}
      </span>
      <span className="chip">
        <i>model</i><b data-testid="meta-model">{m.model}</b>
      </span>
      <span className="chip">
        <i>latency</i><b data-testid="meta-latency">{m.latency_ms} ms</b>
      </span>
      <span className="chip">
        <i>in</i><b data-testid="meta-input-tokens">{m.input_tokens}</b>
      </span>
      <span className="chip">
        <i>out</i><b data-testid="meta-output-tokens">{m.output_tokens}</b>
      </span>
      <span className="chip chip-quiet">
        <i>key</i><b>{m.key_id ?? "—"}</b>
        {m.fallback_attempts > 0 && (
          <em className="failover-tag"> · {m.fallback_attempts} failover{m.fallback_attempts > 1 ? "s" : ""}</em>
        )}
      </span>
    </div>
  );
}

export default function App() {
  const [sessionId, setSessionId] = useState(newSessionId);
  const [items, setItems] = useState([]); // {id, kind: user|assistant|error, text, meta?}
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [failoverHook, setFailoverHook] = useState("");
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState(null); // null = unknown/down
  const endRef = useRef(null);
  const taRef = useRef(null);
  const counter = useRef(0);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      const h = await getHealth();
      if (alive) setHealth(h);
    };
    poll();
    const t = setInterval(poll, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items, loading]);

  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
  }, [input]);

  const push = (item) => setItems((prev) => [...prev, { id: ++counter.current, ...item }]);

  const send = useCallback(
    async (override) => {
      const text = (override ?? input).trim();
      if (!text || loading) return;
      if (text.length > MAX_LEN) {
        push({ kind: "error", text: `Message is too long (${text.length}/${MAX_LEN} characters).` });
        return;
      }
      setInput("");
      push({ kind: "user", text });
      setLoading(true);
      try {
        const data = await sendChat({
          sessionId,
          message: text,
          systemPrompt,
          simulateFailure: failoverHook,
        });
        push({ kind: "assistant", text: data.reply, meta: data.metadata });
      } catch (err) {
        push({ kind: "error", text: err.message });
      } finally {
        setLoading(false);
        taRef.current?.focus();
      }
    },
    [input, loading, sessionId, systemPrompt, failoverHook]
  );

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  const reset = () => {
    setItems([]);
    setInput("");
    setSessionId(newSessionId());
  };

  const status = health ? "online" : "offline";

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="logo" aria-hidden="true">⚡</span>
          <div>
            <h1>Relay AI</h1>
            <p className="sub">session <code>{sessionId}</code></p>
          </div>
        </div>
        <div className="top-actions">
          <span
            className={`status status-${status}`}
            title={health ? `${health.keys_configured} keys · cache ${health.cache_enabled ? "on" : "off"}` : "Backend unreachable"}
          >
            <span className="dot" />
            {health ? `${health.keys_configured} keys · cache on` : "backend offline"}
          </span>
          <button
            className={`ghost ${showSettings ? "active" : ""}`}
            onClick={() => setShowSettings((s) => !s)}
            aria-expanded={showSettings}
          >
            ⚙ Settings & Hooks
          </button>
          <button className="ghost" onClick={reset}>
            + New chat
          </button>
        </div>
      </header>

      {/* Settings & Failover Hook Panel (rendered in DOM for testability) */}
      <section className={`settings-panel ${showSettings ? "open" : ""}`}>
        <div className="settings-grid">
          <div className="setting-block">
            <label htmlFor="sp">System prompt for this session</label>
            <textarea
              id="sp"
              data-testid="system-prompt-input"
              rows={2}
              maxLength={MAX_LEN}
              placeholder="e.g. You are a helpful, concise AI assistant."
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
            />
          </div>
          <div className="setting-block">
            <label htmlFor="sim-hook">🧪 Test Failover Hook (X-Simulate-Failure)</label>
            <select
              id="sim-hook"
              className="sim-select"
              value={failoverHook}
              onChange={(e) => setFailoverHook(e.target.value)}
            >
              <option value="">Normal (Live key pool & cache)</option>
              <option value="key_1=429">Simulate key_1=429 (Rate limit primary → key_2)</option>
              <option value="key_1=500">Simulate key_1=500 (Server error primary → key_2)</option>
              <option value="key_1=429,key_2=500">Simulate key_1=429, key_2=500 (Multi-key failover)</option>
              <option value="*=429">Simulate *=429 (All keys fail → 503 error)</option>
            </select>
            {failoverHook && (
              <span className="sim-hint">
                Active Header: <code>X-Simulate-Failure: {failoverHook}</code>
              </span>
            )}
          </div>
        </div>
      </section>

      <main className="thread" aria-live="polite">
        {items.length === 0 && (
          <div className="empty">
            <h2>Fast, Resilient AI Chat</h2>
            <p>Every response displays its resolution source, active model, latency, and request tokens. Test failover or caching instantly.</p>
            <div className="suggest">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)}>
                  <span>💬</span> {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {items.map((it) => {
          if (it.kind === "user") {
            return (
              <div key={it.id} className="row row-user">
                <div className="bubble user" data-testid="user-message">
                  {it.text}
                </div>
              </div>
            );
          }
          if (it.kind === "error") {
            return (
              <div key={it.id} className="row">
                <div className="bubble error" role="alert" data-testid="error-message">
                  <strong>⚠️ Error:</strong> {it.text}
                </div>
              </div>
            );
          }
          return (
            <div key={it.id} className="row">
              <div className="bubble assistant" data-testid="assistant-message">
                <div className="reply">{it.text}</div>
                <Meta m={it.meta} />
              </div>
            </div>
          );
        })}
        {loading && (
          <div className="row">
            <div className="bubble assistant typing" aria-label="Assistant is thinking">
              <span />
              <span />
              <span />
            </div>
          </div>
        )}
        <div ref={endRef} />
      </main>

      <footer className="composer">
        <div className="box">
          <textarea
            ref={taRef}
            data-testid="chat-input"
            rows={1}
            value={input}
            placeholder="Type your message… (Enter to send, Shift+Enter for new line)"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            aria-label="Message"
          />
          <button
            data-testid="send-button"
            className="send"
            onClick={() => send()}
            disabled={loading || !input.trim()}
          >
            {loading ? "…" : "Send"}
          </button>
        </div>
        <div className="composer-hints">
          <span className={`hint ${input.length > MAX_LEN ? "over" : ""}`}>
            Enter to send · Shift+Enter for new line
            {input.length > MAX_LEN * 0.8 ? ` · ${input.length}/${MAX_LEN}` : ""}
          </span>
          {failoverHook && (
            <span className="hint hook-active">
              Simulation active: <code>{failoverHook}</code>
            </span>
          )}
        </div>
      </footer>
    </div>
  );
}
