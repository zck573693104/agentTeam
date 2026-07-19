import { Badge } from "antd";

const STATUS_MAP: Record<string, { status: "default" | "processing" | "success" | "error" | "warning"; text: string }> = {
  pending: { status: "default", text: "等待中" },
  running: { status: "processing", text: "运行中" },
  completed: { status: "success", text: "已完成" },
  failed: { status: "error", text: "失败" },
  interrupted: { status: "warning", text: "待审批" },
  cancelling: { status: "warning", text: "取消中" },
  cancelled: { status: "default", text: "已取消" },
};

export default function StatusBadge({ status }: { status: string }) {
  const config = STATUS_MAP[status] || { status: "default" as const, text: status };
  return <Badge status={config.status} text={config.text} />;
}
