import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ConfigProvider, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ConfigProvider
        locale={zhCN}
        theme={{
          algorithm: theme.darkAlgorithm,
          token: {
            colorPrimary: "#ffb547",
            colorBgBase: "#0f141b",
            colorBgContainer: "#161c26",
            colorBgElevated: "#1d2530",
            colorBorder: "#2a3441",
            colorText: "#e6ecf2",
            colorTextSecondary: "#8a96a8",
            colorTextTertiary: "#5a6678",
            colorSuccess: "#5dff9e",
            colorError: "#ff5a5f",
            colorWarning: "#ffb547",
            colorInfo: "#4ad8e8",
            fontFamily: '"IBM Plex Sans", -apple-system, BlinkMacSystemFont, sans-serif',
            fontSize: 14,
            borderRadius: 2,
            wireframe: false,
          },
        }}
      >
        <App />
      </ConfigProvider>
    </BrowserRouter>
  </React.StrictMode>
);
