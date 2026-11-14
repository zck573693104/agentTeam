/** 页头组件:统一所有内页的顶部样式。 */
export function PageHeader({
  index,
  title,
  subtitle,
  action,
}: {
  index: string;
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <header className="at-fade-in" style={{
      display: "flex",
      justifyContent: "space-between",
      alignItems: "flex-end",
      marginBottom: 24,
      paddingBottom: 18,
      borderBottom: "1px solid var(--at-border)",
    }}>
      <div>
        <div style={{
          fontFamily: "var(--at-font-mono)",
          fontSize: 12,
          color: "var(--at-amber)",
          letterSpacing: "0.24em",
          textTransform: "uppercase",
          marginBottom: 6,
        }}>
          // {index}
        </div>
        <h1 style={{
          fontFamily: "var(--at-font-sans)",
          fontSize: 28,
          fontWeight: 300,
          color: "var(--at-text)",
          margin: 0,
          letterSpacing: "-0.01em",
        }}>
          {title}
          {subtitle && (
            <span style={{ color: "var(--at-text-faint)", fontWeight: 300 }}> · {subtitle}</span>
          )}
        </h1>
      </div>
      {action}
    </header>
  );
}
