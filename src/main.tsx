import React from "react";
import ReactDOM from "react-dom/client";
import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { HashRouter, Routes, Route } from "react-router-dom";
import { StoreProvider } from "./store";
import App from "./App";
import { SharedApp } from "./pages/SharedApp";
import { AuthGate } from "./AuthGate";
import "antd/dist/reset.css";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#3867ed",
          colorInfo: "#3867ed",
          colorSuccess: "#289575",
          colorWarning: "#c88928",
          colorError: "#d55053",
          colorText: "#273246",
          colorTextSecondary: "#7d8798",
          colorBorder: "#e3e7ef",
          colorBgLayout: "#f7f9fc",
          borderRadius: 8,
          controlHeight: 38,
          fontFamily:
            '"PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          fontSize: 14,
        },
        components: {
          Button: { primaryShadow: "0 2px 4px rgba(56,103,237,.12)" },
          Table: {
            headerBg: "#f8f9fc",
            headerColor: "#7d8798",
            cellPaddingBlock: 17,
          },
          Tabs: { horizontalItemGutter: 30 },
          Modal: { titleFontSize: 18 },
        },
      }}
    >
      <AntApp>
        <HashRouter>
          <Routes><Route path="/share" element={<SharedApp/>}/><Route path="*" element={<AuthGate><StoreProvider>
            <App />
          </StoreProvider></AuthGate>}/></Routes>
        </HashRouter>
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
);
