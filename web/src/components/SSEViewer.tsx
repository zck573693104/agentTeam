import { useEffect, useRef, useState } from "react";
import { Badge, Button, Card, Timeline } from "antd";

/** 事件类型 → Timeline 颜色映射。 */
const EVENT_COLORS: Record<string, string> = {
  run_start: "blue",
  run_end: "green",
  error: "red",
  step_started: "cyan",
  worker_started: "cyan",
  step_completed: "green",
  worker_completed: "green",
  run_interrupted: "orange",
};

/** 已知事件类型列表,用于注册 EventSource 监听器。 */
const EVENT_TYPES = [
  "run_start",
  "run_end",
  "error",
  "run_interrupted",
  "step_started",
  "step_completed",
  "worker_started",
  "worker_completed",
];

interface SSEEvent {
  event_type: string;
  timestamp?: string;
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
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    setEvents([]);
    setEnded(false);
    setConnected(false);

    const es = new EventSource(`/api/runs/${runId}/stream`);
    esRef.current = es;

    const handler = (e: MessageEvent) => {
      try {
        const data: SSEEvent = JSON.parse(e.data);
        setEvents((prev) => [...prev, data]);
        if (data.event_type === "run_end" || data.event_type === "error") {
          es.close();
          setConnected(false);
          setEnded(true);
        }
      } catch {
        // 忽略 JSON 解析错误
      }
    };

    // 注册所有已知事件类型的监听器
    EVENT_TYPES.forEach((type) => es.addEventListener(type, handler));
    // 兜底:未命名事件走 onmessage
    es.onmessage = handler;

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    return () => {
      es.close();
    };
  }, [runId, refreshKey]);

  return (
    <Card
      title="实时轨迹"
      extra={
        <span>
          <Badge
            status={connected ? "processing" : ended ? "success" : "error"}
            text={connected ? "已连接" : ended ? "已结束" : "已断开"}
          />
          {!connected && !ended && (
            <Button size="small" style={{ marginLeft: 8 }} onClick={onReconnect}>
              重连
            </Button>
          )}
        </span>
      }
    >
      {events.length === 0 ? (
        <div style={{ textAlign: "center", color: "#999", padding: 24 }}>等待事件...</div>
      ) : (
        <Timeline
          items={events.map((e, i) => ({
            color: EVENT_COLORS[e.event_type] || "gray",
            children: (
              <div key={i}>
                <Badge
                  color={EVENT_COLORS[e.event_type] || "gray"}
                  text={e.event_type}
                />
                {e.timestamp && (
                  <span style={{ marginLeft: 8, color: "#999", fontSize: 12 }}>
                    {new Date(e.timestamp).toLocaleString()}
                  </span>
                )}
                {e.event_type === "run_interrupted" && (
                  <div style={{ color: "#fa8c16", marginTop: 4 }}>等待审批...</div>
                )}
                <details style={{ marginTop: 4 }}>
                  <summary style={{ cursor: "pointer", color: "#999", fontSize: 12 }}>
                    payload
                  </summary>
                  <pre style={{ fontSize: 12, overflowX: "auto" }}>
                    {JSON.stringify(e, null, 2)}
                  </pre>
                </details>
              </div>
            ),
          }))}
        />
      )}
    </Card>
  );
}
