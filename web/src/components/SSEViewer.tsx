import { useEffect, useState } from "react";
import { Badge, Button, Card, Timeline } from "antd";

/** 事件类型 → Timeline 颜色映射。 */
const EVENT_COLORS: Record<string, string> = {
  run_start: "blue",
  run_end: "green",
  error: "red",
  run_interrupted: "orange",
  leader_plan: "cyan",
  leader_review: "geekblue",
  worker_start: "cyan",
  worker_end: "green",
  tool_call: "purple",
  approval_requested: "orange",
  approval_decided: "gold",
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

    // 注册所有已知事件类型的监听器
    EVENT_TYPES.forEach((type) => es.addEventListener(type, handler));
    // 兜底:未命名事件走 onmessage
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

  const badgeConfig = connected
    ? { status: "processing" as const, text: "已连接" }
    : endedWithError
    ? { status: "error" as const, text: "已失败" }
    : ended
    ? { status: "success" as const, text: "已结束" }
    : interrupted
    ? { status: "warning" as const, text: "等待审批" }
    : { status: "error" as const, text: "已断开" };

  const showReconnect = !connected && !ended && !interrupted;

  return (
    <Card
      title="实时轨迹"
      extra={
        <span>
          <Badge status={badgeConfig.status} text={badgeConfig.text} />
          {showReconnect && (
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
              <div key={e.id ?? i}>
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
