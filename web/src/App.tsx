import { Link, Route, Routes, useLocation } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Skills from "./pages/Skills";
import Teams from "./pages/Teams";
import Runs from "./pages/Runs";
import RunDetail from "./pages/RunDetail";

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
    sublabel: "全局态势",
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
  {
    key: "/teams",
    index: "02",
    label: "TEAMS",
    sublabel: "团队配置",
    path: "/teams",
    icon: (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <circle cx="5" cy="5" r="2" />
        <circle cx="11" cy="5" r="2" />
        <path d="M1 13c0-2.2 1.8-4 4-4s4 1.8 4 4" />
        <path d="M9 13c0-2.2 1.8-4 4-4" opacity="0.5" />
      </svg>
    ),
  },
  {
    key: "/skills",
    index: "03",
    label: "SKILLS",
    sublabel: "技能库",
    path: "/skills",
    icon: (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <path d="M2 3h12v10H2z" />
        <path d="M2 6h12M5 3v10M9 3v10" />
      </svg>
    ),
  },
  {
    key: "/runs",
    index: "04",
    label: "RUNS",
    sublabel: "执行记录",
    path: "/runs",
    icon: (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
        <polygon points="4,2 13,8 4,14" />
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
      <span style={{ color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 10 }}>
        {item.index}
      </span>
      <span style={{ color: active ? "var(--at-amber)" : "var(--at-text-faint)" }}>{item.icon}</span>
      <span style={{ flex: 1 }}>
        <div style={{
          fontFamily: "var(--at-font-mono)",
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: "0.14em",
          lineHeight: 1.2,
        }}>
          {item.label}
        </div>
        <div style={{
          fontFamily: "var(--at-font-sans)",
          fontSize: 10,
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
              fontSize: 10,
              color: "var(--at-text-faint)",
              letterSpacing: "0.16em",
            }}>
              v1.0
            </span>
          </div>
          <div style={{
            fontFamily: "var(--at-font-mono)",
            fontSize: 9,
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
              fontSize: 9,
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
            fontSize: 9,
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
          fontSize: 9,
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
          <Route path="/" element={<Dashboard />} />
          <Route path="/teams" element={<Teams />} />
          <Route path="/skills" element={<Skills />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:runId" element={<RunDetail />} />
        </Routes>
      </main>
    </div>
  );
}
