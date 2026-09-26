/**
 * StatusBadge — 用自定义呼吸点 + mono 标签替代 antd Badge。
 *
 * 呼吸点效果见 index.css 的 .at-dot 类。
 * 终态(completed/cancelled/failed)用静态点,活动态(running/interrupted)用动画点。
 */

interface StatusConfig {
  text: string;
  dotClass: string;
  textColor: string;
  /** 是否是活动态(需要呼吸动画) */
  active: boolean;
}

const STATUS_MAP: Record<string, StatusConfig> = {
  pending:    { text: "等待中",  dotClass: "at-dot-pending",    textColor: "var(--at-text-dim)",    active: false },
  running:    { text: "运行中",  dotClass: "at-dot-running",    textColor: "var(--at-green)",       active: true  },
  completed:  { text: "已完成",  dotClass: "at-dot-completed",  textColor: "var(--at-gold)",        active: false },
  failed:     { text: "失败",    dotClass: "at-dot-failed",     textColor: "var(--at-red)",         active: true  },
  interrupted:{ text: "待审批",  dotClass: "at-dot-interrupted",textColor: "var(--at-amber)",       active: true  },
  cancelling: { text: "取消中",  dotClass: "at-dot-cancelling", textColor: "var(--at-amber)",       active: true  },
  cancelled:  { text: "已取消",  dotClass: "at-dot-cancelled",  textColor: "var(--at-text-faint)",  active: false },
};

export default function StatusBadge({ status }: { status: string }) {
  const config = STATUS_MAP[status] || {
    text: status,
    dotClass: "at-dot-pending",
    textColor: "var(--at-text-dim)",
    active: false,
  };
  return (
    <span style={{
      display: "inline-flex",
      alignItems: "center",
      gap: 8,
      fontFamily: "var(--at-font-mono)",
      fontSize: 11,
      fontWeight: 500,
      letterSpacing: "0.08em",
      color: config.textColor,
    }}>
      <span className={`at-dot ${config.dotClass} ${config.active ? "" : "at-dot-static"}`} />
      <span>{config.text}</span>
    </span>
  );
}
