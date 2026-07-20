import { useEffect, useState } from "react";

/** 事件类型 → 颜色映射(用 CSS 变量值)。 */
const EVENT_COLORS: Record<string, string> = {
  run_start: "#4ad8e8",
  run_end: "#ffd24a",
  error: "#ff5a5f",
  run_interrupted: "#ffb547",
  leader_plan: "#4ad8e8",
  leader_review: "#4ad8e8",
  worker_start: "#4ad8e8",
  worker_end: "#5dff9e",
  tool_call: "#ffb547",
  approval_requested: "#ffb547",
  approval_decided: "#ffd24a",
};

/** 已知事件类型列表,用于注册 EventSource 监听器。 */
const EVENT_TYPES = [
  "run_start",
  "run_end",
  "error",
  "run_interrupted",
  "leader_plan",
  "leader_review",
  "worker_start",
  "worker_end",
  "tool_call",
  "approval_requested",
  "approval_decided",
];

interface SSEEvent {
  event_type: string;
  timestamp?: string;
  id?: number;
  [key: string]: unknown;
}

interface SSEViewerProps {
  runId: string;
  /** 变化时重新连接 SSE。审批后续跑时父组件递增此值。 */
  refreshKey: number;
  /** 用户点击"重连"按钮时的回调。 */
  onReconnect: () => void;
}

export default function SSEViewer({ runId, refreshKey, onReconnect }: SSEViewerProps) {
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [ended, setEnded] = useState(false);
  const [endedWithError, setEndedWithError] = useState(false);
  const [interrupted, setInterrupted] = useState(false);

  useEffect(() => {
    setEvents([]);
    setEnded(false);
    setEndedWithError(false);
    setInterrupted(false);
    setConnected(false);

    const es = new EventSource(`/api/runs/${runId}/stream`);

    const handler = (e: MessageEvent) => {
      try {
        const data: SSEEvent = JSON.parse(e.data);
        setEvents((prev) => [...prev, data]);
        if (data.event_type === "run_interrupted") {
          setInterrupted(true);
        } else if (data.event_type === "run_end" || data.event_type === "error") {
          es.close();
          setConnected(false);
          setEnded(true);
          setEndedWithError(data.event_type === "error");
        }
      } catch {
        // 忽略 JSON 解析错误
      }
    };

    EVENT_TYPES.forEach((type) => es.addEventListener(type, handler));
    es.onmessage = handler;

    es.onopen = () => setConnected(true);
    es.onerror = () => {
      setConnected(false);
      es.close();
    };

    return () => {
      es.close();
    };
  }, [runId, refreshKey]);

  // 状态指示:运行中绿点 / 中断 amber / 失败 red / 结束 gold
  const statusConfig = connected
    ? { color: "#5dff9e", text: "CONNECTED", dotClass: "at-dot-running" }
    : endedWithError
    ? { color: "#ff5a5f", text: "FAILED", dotClass: "at-dot-failed" }
    : ended
    ? { color: "#ffd24a", text: "ENDED", dotClass: "at-dot-completed" }
    : interrupted
    ? { color: "#ffb547", text: "AWAITING APPROVAL", dotClass: "at-dot-interrupted" }
    : { color: "#ff5a5f", text: "DISCONNECTED", dotClass: "at-dot-failed" };

  const showReconnect = !connected && !ended && !interrupted;

  return (
    <div className="at-panel" style={{ padding: 0 }}>
      <header style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "12px 16px",
        borderBottom: "1px solid var(--at-border)",
      }}>
        <div className="at-section-title" style={{ margin: 0 }}>Live Trace · 实时轨迹</div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontFamily: "var(--at-font-mono)",
            fontSize: 10,
            color: statusConfig.color,
            letterSpacing: "0.1em",
          }}>
            <span className={`at-dot ${statusConfig.dotClass}`} />
            {statusConfig.text}
          </span>
          {showReconnect && (
            <button className="at-link" onClick={onReconnect}>↻ RECONNECT</button>
          )}
        </div>
      </header>

      {events.length === 0 ? (
        <div style={{
          padding: 48,
          textAlign: "center",
          color: "var(--at-text-faint)",
          fontFamily: "var(--at-font-mono)",
          fontSize: 12,
        }}>
          ◌ WAITING FOR EVENTS · 等待事件...
        </div>
      ) : (
        <div style={{ maxHeight: 560, overflowY: "auto", padding: "8px 0" }}>
          {events.map((e, i) => {
            const color = EVENT_COLORS[e.event_type] || "#6b7785";
            return (
              <div
                key={e.id ?? i}
                style={{
                  padding: "10px 16px",
                  borderLeft: `2px solid ${color}`,
                  marginBottom: 2,
                  marginLeft: 8,
                  background: "transparent",
                  transition: "background 0.12s ease",
                }}
                onMouseEnter={(ev) => { ev.currentTarget.style.background = "var(--at-bg-hover)"; }}
                onMouseLeave={(ev) => { ev.currentTarget.style.background = "transparent"; }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
                  <span style={{
                    fontFamily: "var(--at-font-mono)",
                    fontSize: 10,
                    color: color,
                    fontWeight: 600,
                    letterSpacing: "0.1em",
                    textTransform: "uppercase",
                  }}>
                    {e.event_type}
                  </span>
                  {e.timestamp && (
                    <span style={{
                      fontFamily: "var(--at-font-mono)",
                      fontSize: 9,
                      color: "var(--at-text-faint)",
                      letterSpacing: "0.08em",
                    }}>
                      {new Date(e.timestamp).toLocaleString("zh-CN", { hour12: false })}
                    </span>
                  )}
                </div>
                {e.event_type === "run_interrupted" && (
                  <div style={{
                    color: "var(--at-amber)",
                    fontFamily: "var(--at-font-mono)",
                    fontSize: 11,
                    marginTop: 4,
                  }}>
                    ⚠ 等待审批决策...
                  </div>
                )}
                <details style={{ marginTop: 6 }}>
                  <summary style={{
                    cursor: "pointer",
                    color: "var(--at-text-faint)",
                    fontFamily: "var(--at-font-mono)",
                    fontSize: 10,
                    letterSpacing: "0.1em",
                    textTransform: "uppercase",
                  }}>
                    ▸ payload
                  </summary>
                  <pre style={{
                    marginTop: 6,
                    fontSize: 11,
                    padding: 10,
                    background: "var(--at-bg-deep)",
                    border: "1px solid var(--at-border)",
                    color: "var(--at-text-mono)",
                    overflowX: "auto",
                  }}>
                    {JSON.stringify(e, null, 2)}
                  </pre>
                </details>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
