import { Layout, Menu } from "antd";
import {
  BookOutlined,
  DashboardOutlined,
  PlayCircleOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { Link, Route, Routes, useLocation } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Skills from "./pages/Skills";
import Teams from "./pages/Teams";
import Runs from "./pages/Runs";
import RunDetail from "./pages/RunDetail";

const { Sider, Content } = Layout;

export default function App() {
  const location = useLocation();
  const selectedKey = location.pathname.startsWith("/runs/")
    ? "/runs"
    : location.pathname;

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider collapsible>
        <div style={{ height: 32, margin: 16, color: "#fff", fontSize: 18, textAlign: "center", lineHeight: "32px" }}>
          AgentTeam
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={[
            { key: "/", icon: <DashboardOutlined />, label: <Link to="/">Dashboard</Link> },
            { key: "/teams", icon: <TeamOutlined />, label: <Link to="/teams">Teams</Link> },
            { key: "/skills", icon: <BookOutlined />, label: <Link to="/skills">Skills</Link> },
            { key: "/runs", icon: <PlayCircleOutlined />, label: <Link to="/runs">Runs</Link> },
          ]}
        />
      </Sider>
      <Layout>
        <Content style={{ padding: 24, overflow: "auto" }}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/teams" element={<Teams />} />
            <Route path="/skills" element={<Skills />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/:runId" element={<RunDetail />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}
