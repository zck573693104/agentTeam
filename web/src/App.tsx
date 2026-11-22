import { Link, Route, Routes, useLocation } from "react-router-dom";
import V2Dashboard from "./pages/v2/Dashboard";
import V2Attention from "./pages/v2/Attention";
import V2Tickets from "./pages/v2/Tickets";
import V2TicketDetail from "./pages/v2/TicketDetail";
import V2Threads from "./pages/v2/Threads";
import V2Observe from "./pages/v2/Observe";
import V2Workers from "./pages/v2/Workers";
import V2Sources from "./pages/v2/Sources";
import V2Skills from "./pages/v2/Skills";
import V2Memory from "./pages/v2/Memory";

/**
 * 自定义侧栏导航 — 替代 antd Layout/Sider/Menu。
 *
 * 设计意图:让左侧像"任务台"而非通用 admin 侧栏。
 * - 顶部"AGT"大字 logo + 版本号 + 状态指示灯
 * - 导航项是 mono 字体 + 编号前缀,active 态有 amber 左侧高亮条
 * - 底部有 system status 模拟控制台 footer
 */

interface NavItem {
  key: string;
  index: string;       // 编号前缀
  label: string;       // 主标签
  sublabel: string;    // 副标签
  path: string;
  icon: React.ReactNode;
}

const NAV_ITEMS: NavItem[] = [
  {
    key: "/",
    index: "01",
    label: "OVERVIEW",
    sublabel: "总览指挥台",
    path: "/",
    icon: (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <rect x="1.5" y="1.5" width="5" height="5" />
        <rect x="9.5" y="1.5" width="5" height="5" />
        <rect x="1.5" y="9.5" width="5" height="5" />
        <rect x="9.5" y="9.5" width="5" height="5" />
      </svg>
    ),
  },
  // ---- V2 控制台(dsh 式工单中心;P5 切换后接管导航) ----
  {
    key: "/v2/attention",
    index: "02",
    label: "INBOX · V2",
    sublabel: "注意件收件箱",
    path: "/v2/attention",
    icon: (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <path d="M8 1.5l6.5 11h-13z" />
        <path d="M8 6v3.5" opacity="0.7" />
        <circle cx="8" cy="11" r="0.4" fill="currentColor" />
      </svg>
    ),
  },
  {
    key: "/v2/tickets",
    index: "03",
    label: "TICKETS · V2",
    sublabel: "工单看板",
    path: "/v2/tickets",
    icon: (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <rect x="1.5" y="2.5" width="13" height="11" />
        <path d="M1.5 6h13M6 6v7.5" />
      </svg>
    ),
  },
  {
    key: "/v2/threads",
    index: "04",
    label: "THREADS · V2",
    sublabel: "入站线程",
    path: "/v2/threads",
    icon: (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <path d="M2 3h12M2 8h8M2 13h10" />
      </svg>
    ),
  },
  {
    key: "/v2/observe",
    index: "05",
    label: "OBSERVE · V2",
    sublabel: "观察模式",
    path: "/v2/observe",
    icon: (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <path d="M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z" />
        <circle cx="8" cy="8" r="1.8" />
      </svg>
    ),
  },
  {
    key: "/v2/workers",
    index: "06",
    label: "WORKERS · V2",
    sublabel: "工人池",
    path: "/v2/workers",
    icon: (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <circle cx="8" cy="4.5" r="2.5" />
        <path d="M2.5 14c0-3 2.5-5 5.5-5s5.5 2 5.5 5" />
      </svg>
    ),
  },
  {
    key: "/v2/sources",
    index: "07",
    label: "SOURCES · V2",
    sublabel: "入站源",
    path: "/v2/sources",
    icon: (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <path d="M8 14V7M8 7c-3 0-4-3-4-3M8 7c3 0 4-3 4-3" />
        <circle cx="8" cy="14" r="1.2" />
      </svg>
    ),
  },
  {
    key: "/v2/skills",
    index: "08",
    label: "SKILLS · V2",
    sublabel: "技能库",
    path: "/v2/skills",
    icon: (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <path d="M2 3h12v10H2z" />
        <path d="M2 6h12M5 3v10M9 3v10" />
      </svg>
    ),
  },
  {
    key: "/v2/memory",
    index: "09",
    label: "MEMORY · V2",
    sublabel: "记忆库",
    path: "/v2/memory",
    icon: (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <circle cx="8" cy="8" r="5.5" />
        <circle cx="8" cy="8" r="1.6" />
        <path d="M8 2.5v2M8 11.5v2M2.5 8h2M11.5 8h2" />
      </svg>
    ),
  },
];

function NavButton({ item, active }: { item: NavItem; active: boolean }) {
  return (
    <Link
      to={item.path}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "11px 14px 11px 18px",
        textDecoration: "none",
        color: active ? "var(--at-text)" : "var(--at-text-dim)",
        background: active ? "var(--at-bg-elev)" : "transparent",
        borderLeft: `2px solid ${active ? "var(--at-amber)" : "transparent"}`,
        transition: "all 0.15s ease",
        position: "relative",
      }}
      onMouseEnter={(e) => {
        if (!active) {
          e.currentTarget.style.background = "var(--at-bg-hover)";
          e.currentTarget.style.color = "var(--at-text)";
        }
      }}
      onMouseLeave={(e) => {
        if (!active) {
          e.currentTarget.style.background = "transparent";
          e.currentTarget.style.color = "var(--at-text-dim)";
        }
      }}
    >
      <span style={{ color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
        {item.index}
      </span>
      <span style={{ color: active ? "var(--at-amber)" : "var(--at-text-faint)" }}>{item.icon}</span>
      <span style={{ flex: 1 }}>
        <div style={{
          fontFamily: "var(--at-font-mono)",
          fontSize: 13,
          fontWeight: 600,
          letterSpacing: "0.14em",
          lineHeight: 1.2,
        }}>
          {item.label}
        </div>
        <div style={{
          fontFamily: "var(--at-font-sans)",
          fontSize: 12,
          color: "var(--at-text-faint)",
          marginTop: 2,
        }}>
          {item.sublabel}
        </div>
      </span>
      {active && (
        <span style={{
          width: 4,
          height: 4,
          background: "var(--at-amber)",
          boxShadow: "var(--at-glow-amber)",
        }} />
      )}
    </Link>
  );
}

export default function App() {
  const location = useLocation();
  const selectedKey = location.pathname.startsWith("/runs/")
    ? "/runs"
    : location.pathname.startsWith("/v2/tickets/")
      ? "/v2/tickets"
      : location.pathname;

  return (
    <div style={{
      display: "flex",
      minHeight: "100vh",
      position: "relative",
      zIndex: 2,
    }}>
      {/* ---- 左侧任务台 ---- */}
      <aside style={{
        width: 232,
        flexShrink: 0,
        background: "var(--at-bg-base)",
        borderRight: "1px solid var(--at-border)",
        display: "flex",
        flexDirection: "column",
        position: "sticky",
        top: 0,
        height: "100vh",
      }}>
        {/* Logo + 系统标识 */}
        <div style={{
          padding: "22px 18px 18px",
          borderBottom: "1px solid var(--at-border)",
          position: "relative",
        }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <span style={{
              fontFamily: "var(--at-font-mono)",
              fontSize: 24,
              fontWeight: 700,
              color: "var(--at-amber)",
              letterSpacing: "-0.02em",
              lineHeight: 1,
            }}>
              AGT
            </span>
            <span style={{
              fontFamily: "var(--at-font-mono)",
              fontSize: 12,
              color: "var(--at-text-faint)",
              letterSpacing: "0.16em",
            }}>
              v1.0
            </span>
          </div>
          <div style={{
            fontFamily: "var(--at-font-mono)",
            fontSize: 12,
            color: "var(--at-text-faint)",
            letterSpacing: "0.2em",
            marginTop: 6,
            textTransform: "uppercase",
          }}>
            Mission Control
          </div>
          {/* 状态指示灯 */}
          <div style={{
            position: "absolute",
            top: 24,
            right: 18,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}>
            <span className="at-dot at-dot-running" />
            <span style={{
              fontFamily: "var(--at-font-mono)",
              fontSize: 12,
              color: "var(--at-green)",
              letterSpacing: "0.12em",
            }}>
              ONLINE
            </span>
          </div>
        </div>

        {/* 导航 */}
        <nav style={{ flex: 1, padding: "14px 0" }}>
          <div style={{
            padding: "0 18px 10px",
            fontFamily: "var(--at-font-mono)",
            fontSize: 12,
            color: "var(--at-text-faint)",
            letterSpacing: "0.22em",
            textTransform: "uppercase",
          }}>
            // Modules
          </div>
          {NAV_ITEMS.map((item) => (
            <NavButton
              key={item.key}
              item={item}
              active={selectedKey === item.key}
            />
          ))}
        </nav>

        {/* Footer:system status */}
        <div style={{
          padding: "14px 18px",
          borderTop: "1px solid var(--at-border)",
          fontFamily: "var(--at-font-mono)",
          fontSize: 12,
          color: "var(--at-text-faint)",
          letterSpacing: "0.1em",
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
            <span>SYS</span>
            <span style={{ color: "var(--at-green)" }}>● OPERATIONAL</span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
            <span>RUNTIME</span>
            <span>PY · 3.14</span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span>BUILD</span>
            <span>{new Date().getFullYear()}.{String(new Date().getMonth() + 1).padStart(2, "0")}</span>
          </div>
        </div>
      </aside>

      {/* ---- 右侧主区域 ---- */}
      <main style={{
        flex: 1,
        minWidth: 0,
        padding: "28px 36px 56px",
        overflow: "auto",
      }}>
        <Routes>
          {/* ---- 控制台(dsh 式工单中心) ---- */}
          <Route path="/" element={<V2Dashboard />} />
          <Route path="/v2/attention" element={<V2Attention />} />
          <Route path="/v2/tickets" element={<V2Tickets />} />
          <Route path="/v2/tickets/:ticketId" element={<V2TicketDetail />} />
          <Route path="/v2/threads" element={<V2Threads />} />
          <Route path="/v2/observe" element={<V2Observe />} />
          <Route path="/v2/workers" element={<V2Workers />} />
          <Route path="/v2/sources" element={<V2Sources />} />
          <Route path="/v2/skills" element={<V2Skills />} />
          <Route path="/v2/memory" element={<V2Memory />} />
        </Routes>
      </main>
    </div>
  );
}
