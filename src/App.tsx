import { lazy, Suspense, useEffect, useState } from "react";
import { PageLoadBoundary } from "./PageLoadBoundary";
import { RuntimePanel } from "./RuntimePanel";
import {
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  Alert,
  App as AntApp,
  Avatar,
  Badge,
  Button,
  Drawer,
  Input,
  Modal,
  Popover,
  Select,
  Segmented,
} from "antd";
import {
  ApartmentOutlined,
  ArrowRightOutlined,
  BellOutlined,
  CommentOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  HomeOutlined,
  InfoCircleOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  QuestionCircleOutlined,
  SearchOutlined,
  SettingOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { useStore } from "./store-context";
import { api } from "./live-api";
import { useAuth } from "./AuthGate";
import { AccountSession } from "./AccountSession";
import { isRetrievable, ROLE_LABELS } from "./demo";
import type { Role } from "./types";
import { FileIcon } from "./components";
const Dashboard = lazy(() =>
  import("./pages/Dashboard").then((m) => ({ default: m.Dashboard })),
);
const KnowledgeList = lazy(() =>
  import("./pages/Knowledge").then((m) => ({ default: m.KnowledgeList })),
);
const KnowledgeDetail = lazy(() =>
  import("./pages/Knowledge").then((m) => ({ default: m.KnowledgeDetail })),
);
const Models = lazy(() =>
  import("./pages/Models").then((m) => ({ default: m.Models })),
);
const Assistants = lazy(() =>
  import("./pages/Assistants").then((m) => ({ default: m.Assistants })),
);
const Chat = lazy(() =>
  import("./pages/Chat").then((m) => ({ default: m.Chat })),
);
const Logs = lazy(() =>
  import("./pages/Logs").then((m) => ({ default: m.Logs })),
);
const PlatformSettings = lazy(() =>
  import("./pages/PlatformSettings").then((m) => ({ default: m.PlatformSettings })),
);
const PlatformTools = lazy(() =>
  import("./pages/PlatformTools").then((m) => ({ default: m.PlatformTools })),
);
const PlatformInsights = lazy(() =>
  import("./pages/PlatformInsights").then((m) => ({ default: m.PlatformInsights })),
);
const AppStudio = lazy(() => import("./pages/AppStudio").then(m=>({default:m.AppStudio})));
const PlatformApps = lazy(() =>
  import("./pages/PlatformApps").then((m) => ({ default: m.PlatformApps })),
);
const PlatformChat = lazy(() =>
  import("./pages/PlatformChat").then((m) => ({ default: m.PlatformChat })),
);
const EvaluationCases = lazy(() =>
  import("./pages/EvaluationCases").then((m) => ({ default: m.EvaluationCases })),
);
const Observability = lazy(() =>
  import("./pages/Observability").then((m) => ({ default: m.Observability })),
);
const ObservabilityDesign = lazy(() =>
  import("./pages/ObservabilityDesign").then((m) => ({ default: m.ObservabilityDesign })),
);

const navItems = [
  { path: "/", label: "工作台", icon: HomeOutlined },
  { path: "/knowledge", label: "知识库", icon: DatabaseOutlined },
  { path: "/assistants", label: "问答助手", icon: ApartmentOutlined },
  { path: "/platform/apps", label: "应用", icon: ApartmentOutlined },
  { path: "/platform/tools", label: "工具与 Skill", icon: SettingOutlined },
  { path: "/platform/insights", label: "运营看板", icon: ExperimentOutlined },
  { path: "/chat", label: "在线对话", icon: CommentOutlined },
  { path: "/logs", label: "日志监测", icon: FileSearchOutlined },
  { path: "/observability", label: "可观测中心", icon: DeploymentUnitOutlined },
  { path: "/evaluation", label: "评测集", icon: ExperimentOutlined },
  { path: "/models", label: "模型服务", icon: SettingOutlined },
  { path: "/platform/settings", label: "平台设置", icon: SettingOutlined },
];

type PlatformAlert={id:string;kind:string;createdAt:string;month?:string;count?:number;threshold?:number;estimatedKnown?:string;limit?:string;unknownCount?:number};

export default function App() {
  const auth=useAuth();
  const {
    state,
    update,
    reset,
    storageError,
    mode,
    setMode,
    loading,
    connected,
    syncing,
    connectionError,
    reconnect,
  } = useStore();
  const { modal } = AntApp.useApp();
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [isMobile, setIsMobile] = useState(
    () => window.matchMedia("(max-width: 680px)").matches,
  );
  const [help, setHelp] = useState(false);
  const [runtimeOpen, setRuntimeOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [alerts,setAlerts]=useState<PlatformAlert[]>([]);
  const [alertsSeen,setAlertsSeen]=useState(()=>localStorage.getItem("zhixu-alerts-seen")||"");
  const isAdmin = state.role === "admin";
  const canManage = state.role !== "user";
  const current =
    (location.pathname.startsWith("/platform/chat/") ? navItems.find((item) => item.path === "/platform/apps") : undefined) || navItems.find(
      (item) => item.path !== "/" && location.pathname.startsWith(item.path),
    ) || navItems[0];
  const currentKB = state.knowledgeBases.find(
    (k) => location.pathname === `/knowledge/${k.id}`,
  );
  const visibleNav = navItems.filter((n) =>
    n.path === "/models" || n.path === "/platform/settings" ? isAdmin : n.path === "/logs" || n.path === "/evaluation" || n.path === "/observability" || n.path === "/platform/tools" || n.path === "/platform/insights" ? canManage : true,
  );
  const failed = state.documents.filter(
    (d) => d.status === "failed" || d.status === "review",
  );
  useEffect(()=>{
    let active=true;
    if(mode!=="live"||!isAdmin){setAlerts([]);return}
    const loadAlerts=async()=>{try{const data=await api<{items:PlatformAlert[]}>("/platform/alerts",state.role);if(active)setAlerts(data.items)}catch{if(active)setAlerts([])}};
    void loadAlerts();const timer=window.setInterval(()=>void loadAlerts(),60000);
    return()=>{active=false;window.clearInterval(timer)};
  },[mode,isAdmin,state.role]);
  const unreadAlerts=alerts.some(item=>item.createdAt>alertsSeen);
  useEffect(() => {
    document.title = `${currentKB?.name || current.label} · 知序知识中台`;
  }, [current.label, currentKB?.name]);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 680px)");
    const listener = () => {
      setIsMobile(query.matches);
      if (!query.matches) setMobileNav(false);
    };
    query.addEventListener("change", listener);
    return () => query.removeEventListener("change", listener);
  }, []);
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);
  const changeRole = (role: Role) => {
    update((prev) => ({ ...prev, role }));
    if (
      (role !== "admin" && (location.pathname.startsWith("/models") || location.pathname.startsWith("/platform/settings"))) ||
      (role === "user" && (location.pathname.startsWith("/logs") || location.pathname.startsWith("/evaluation") || location.pathname.startsWith("/observability") || location.pathname.startsWith("/platform/tools") || location.pathname.startsWith("/platform/insights")))
    )
      navigate(role === "user" ? "/chat" : "/");
  };
  const openSearchResult = (url: string) => {
    navigate(url);
    setSearchOpen(false);
    setSearch("");
  };
  const matches = search.trim().toLowerCase();
  return (
    <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      <aside className="sidebar">
        <Link to="/" className="brand">
          <span className="brand-mark">
            <DeploymentUnitOutlined aria-hidden="true" />
          </span>
          <span className="brand-name">
            知序<span>企业知识库中台</span>
          </span>
        </Link>
        <div className="workspace-switch">
          <span className="workspace-logo">
            <ApartmentOutlined aria-hidden="true" />
          </span>
          <div>
            <strong>企业工作空间</strong>
            <small>通用版本 1.0</small>
          </div>
          <span className="workspace-tag">内部</span>
        </div>
        <div className="nav-section-label">工作空间</div>
        <nav aria-label="主导航">
          {visibleNav.map(({ path, label, icon: Icon }) => (
            <NavLink
              key={path}
              to={path}
              end={path === "/"}
              className={({ isActive }) =>
                `nav-item ${isActive ? "active" : ""}`
              }
            >
              <Icon aria-hidden="true" />
              <span>{label}</span>
              {path === "/knowledge" && (
                <small>{state.knowledgeBases.length}</small>
              )}
              {path === "/chat" && <span className="nav-new">AI</span>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="environment-card">
            <span className="environment-dot" />
            <strong>{mode === "live" ? "真实后端模式" : "本地演示环境"}</strong>
            <p>
              {mode === "live"
                ? "文件与配置由本机后端保存"
                : "交互状态保存在当前浏览器"}
            </p>
            <button onClick={() => setHelp(true)}>
              了解当前原型 <ArrowRightOutlined aria-hidden="true" />
            </button>
          </div>
          <button className="help-link" onClick={() => setHelp(true)}>
            <QuestionCircleOutlined aria-hidden="true" />
            <span>使用指南与原型说明</span>
          </button>
          {isAdmin && mode === "live" && <button className="help-link" onClick={() => setRuntimeOpen(true)}><SettingOutlined aria-hidden="true" /><span>本机运行与备份</span></button>}
          <div className="sidebar-footer">
            <span>知序 Knowledge Hub</span>
            <span>v0.8</span>
          </div>
        </div>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <div className="topbar-left">
            <Button
              type="text"
              aria-label={
                isMobile ? "打开导航" : collapsed ? "展开导航" : "收起导航"
              }
              icon={
                collapsed ? (
                  <MenuUnfoldOutlined aria-hidden="true" />
                ) : (
                  <MenuFoldOutlined aria-hidden="true" />
                )
              }
              onClick={() =>
                isMobile ? setMobileNav(true) : setCollapsed(!collapsed)
              }
            />
            <span className="breadcrumb">
              工作空间 <span>/</span>
              <strong>{current.label}</strong>
              {currentKB && (
                <>
                  <span>/</span>
                  <strong>{currentKB.name}</strong>
                </>
              )}
            </span>
          </div>
          <div className="topbar-right">
            <Segmented
              className="data-mode-switch"
              aria-label="数据模式"
              size="small"
              value={mode}
              disabled={syncing}
              onChange={(value) => setMode(value as "demo" | "live")}
              options={[
                { value: "demo", label: "演示" },
                { value: "live", label: "真实后端" },
              ]}
            />
            <button
              className="global-search"
              onClick={() => setSearchOpen(true)}
            >
              <SearchOutlined aria-hidden="true" />
              <span>搜索知识库、文档</span>
              <kbd>⌘ K</kbd>
            </button>
            <Popover
              trigger="click"
              placement="bottomRight"
              title="需要关注"
              onOpenChange={(opened)=>{if(opened&&alerts.length){const latest=alerts.reduce((a,b)=>a.createdAt>b.createdAt?a:b).createdAt;localStorage.setItem("zhixu-alerts-seen",latest);setAlertsSeen(latest)}}}
              content={
                <div className="notification-list">
                  {alerts.map(item=><button key={item.id} onClick={()=>navigate("/observability?domain=usage")}><span><strong>{item.kind==="failure_spike"?`单小时记录 ${item.count} 次失败`:`${item.month} 预算已达到`}</strong><small>{item.kind==="failure_spike"?"按已记录的失败运行统计":`已知估算 ¥${item.estimatedKnown} / 预算 ¥${item.limit}，${item.unknownCount} 项未知`}</small></span></button>)}
                  {failed.length ? (
                    failed.map((d) => (
                      <button
                        key={d.id}
                        onClick={() =>
                          navigate(`/knowledge/${d.kbId}?doc=${d.id}`)
                        }
                      >
                        <FileIcon format={d.format} small />
                        <span>
                          <strong>{d.name}</strong>
                          <small>{d.error}</small>
                        </span>
                      </button>
                    ))
                  ) : (
                    !alerts.length&&<p>目前没有异常任务。</p>
                  )}
                </div>
              }
            >
              <Badge dot={canManage && (failed.length > 0 || unreadAlerts)}>
                <Button
                  type="text"
                  aria-label="通知"
                  icon={<BellOutlined aria-hidden="true" />}
                />
              </Badge>
            </Popover>
            <div className="topbar-divider" />
            <Avatar size={32} className="user-avatar">
              <TeamOutlined aria-hidden="true" />
            </Avatar>
            <AccountSession/>
            <Select
              aria-label="当前角色"
              disabled={syncing || auth.enabled}
              className="role-select"
              variant="borderless"
              value={state.role}
              onChange={changeRole}
              options={(Object.entries(ROLE_LABELS) as [Role, string][]).map(
                ([value, label]) => ({ value, label }),
              )}
            />
          </div>
        </header>
        <div className="prototype-strip">
          <ExperimentOutlined aria-hidden="true" />
          <span>{mode === "live" ? "真实后端模式" : "前端交互原型"}</span>
          <span className="strip-divider" />
          <span>
            {mode === "live"
              ? syncing
                ? "正在保存变更，请稍候…"
                : loading
                  ? "正在连接本机服务…"
                  : `使用真实上传文件与模型 API · ${auth.enabled ? "已启用本机账号" : "角色选择不验证身份"}`
              : "使用示例数据体验完整知识流程"}
          </span>
          <button onClick={() => setHelp(true)}>
            查看说明 <ArrowRightOutlined aria-hidden="true" />
          </button>
        </div>
        {connectionError && (
          <Alert
            type="error"
            showIcon
            title={connectionError}
            action={
              <Button size="small" onClick={reconnect}>
                重新连接
              </Button>
            }
          />
        )}
        {storageError && (
          <Alert
            type="warning"
            showIcon
            title="浏览器存储空间不足，当前变更只保留在本次会话中。"
          />
        )}
        <main
          className={`page-container ${current.path === "/chat" ? "chat-container" : ""}`}
        >
          {loading || !connected ? (
            <div className="page-loading">
              {loading
                ? "正在连接后端工作空间…"
                : "后端尚未连接，请启动服务后点击重新连接。"}
            </div>
          ) : (
            <PageLoadBoundary key={location.pathname}><Suspense
              fallback={<div className="page-loading">正在准备工作空间…</div>}
            >
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/knowledge" element={<KnowledgeList />} />
                <Route path="/knowledge/:id" element={<KnowledgeDetail />} />
                <Route path="/assistants" element={<Assistants />} />
                <Route path="/platform/apps" element={<PlatformApps />} />
                <Route path="/platform/apps/:appId/edit" element={<AppStudio />} />
                <Route path="/platform/settings" element={isAdmin ? <PlatformSettings /> : <AccessDenied />} />
                <Route path="/platform/tools" element={canManage ? <PlatformTools /> : <AccessDenied />} />
                <Route path="/platform/insights" element={canManage ? <PlatformInsights /> : <AccessDenied />} />
                <Route path="/platform/chat/:appId" element={<PlatformChat />} />
                <Route path="/chat" element={<Chat />} />
                <Route
                  path="/logs"
                  element={canManage ? <Logs /> : <AccessDenied />}
                />
                <Route
                  path="/logs/design"
                  element={canManage ? <ObservabilityDesign /> : <AccessDenied />}
                />
                <Route path="/observability" element={canManage ? <Observability /> : <AccessDenied />} />
                <Route path="/evaluation" element={canManage ? <EvaluationCases /> : <AccessDenied />} />
                <Route
                  path="/models"
                  element={isAdmin ? <Models /> : <AccessDenied />}
                />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense></PageLoadBoundary>
          )}
        </main>
      </div>
      <Drawer
        title="企业工作空间"
        placement="left"
        size={260}
        open={mobileNav}
        onClose={() => setMobileNav(false)}
      >
        <div style={{ marginBottom: 20 }}>
          <Segmented
            aria-label="窄屏数据模式"
            value={mode}
            disabled={syncing}
            onChange={(value) => setMode(value as "demo" | "live")}
            options={[
              { value: "demo", label: "演示" },
              { value: "live", label: "真实后端" },
            ]}
          />
        </div>
        <nav aria-label="窄屏导航" className="mobile-navigation">
          {visibleNav.map(({ path, label, icon: Icon }) => (
            <NavLink
              key={path}
              to={path}
              end={path === "/"}
              onClick={() => setMobileNav(false)}
              className={({ isActive }) =>
                `nav-item ${isActive ? "active" : ""}`
              }
            >
              <Icon aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <p className="hint">
          {mode === "live" ? "真实后端模式" : "前端交互原型"} · 当前角色：
          {ROLE_LABELS[state.role]}
        </p>
      </Drawer>
      <Modal
        open={help}
        onCancel={() => setHelp(false)}
        title="欢迎体验知序知识中台"
        width={610}
        footer={
          <>
            {mode === "demo" && (
              <Button
                onClick={() =>
                  modal.confirm({
                    title: "恢复初始演示数据？",
                    content:
                      "只会重置此原型的知识库、会话与设置，不影响本机文件。",
                    okText: "恢复示例",
                    cancelText: "保留当前数据",
                    onOk: () => {
                      reset();
                      navigate("/");
                      setHelp(false);
                    },
                  })
                }
              >
                恢复示例数据
              </Button>
            )}
            <Button type="primary" onClick={() => setHelp(false)}>
              开始体验
            </Button>
          </>
        }
      >
        <p className="modal-description">
          {mode === "live"
            ? "真实后端模式会将你选择的文件发送至本机后端，解析和问答按配置调用中国内地通义 API。API Key 由服务端保存，浏览器不持久化凭证。"
            : "这是方案 A 的前端原型，供你确认功能、布局和操作流程。"}
        </p>
        <div className="guide-steps">
          {[
            {
              icon: SettingOutlined,
              title: "01 · 配置模型",
              desc:
                mode === "live"
                  ? "在服务连接保存 API Key，逐一测试所需的五类模型能力。"
                  : "五类模型能力可编辑并模拟测试，无需真实密钥。",
            },
            {
              icon: DatabaseOutlined,
              title: "02 · 导入并检查知识",
              desc:
                mode === "live"
                  ? "新建知识库、上传本地文件，等待构建完成后检查实际解析与分块。"
                  : "新建知识库、导入示例文件，体验构建、失败重试和分块预览。",
            },
            {
              icon: CommentOutlined,
              title: "03 · 检索、提问与溯源",
              desc:
                mode === "live"
                  ? "创建助手并选择已就绪知识库，测试检索、对话与引用依据。"
                  : "使用真实问题走查示例检索、连续对话和回答依据。",
            },
          ].map(({ icon: Icon, title, desc }) => (
            <div key={title}>
              <span>
                <Icon aria-hidden="true" />
              </span>
              <section>
                <h4>{title}</h4>
                <p>{desc}</p>
              </section>
            </div>
          ))}
        </div>
        <Alert
          type="info"
          showIcon
          title={
            mode === "live"
              ? "真实模式与演示数据相互独立。调用会产生模型费用；角色选择用于本机演示，不验证身份。"
              : "所有解析内容、回答和 Token 用量均为模拟，不上传文件，不调用模型。角色选择用于演示，不验证身份。"
          }
        />
        <p className="hint">
          当前角色选择不验证身份，适用于本机演示。切换数据模式不会迁移演示资料。
        </p>
      </Modal>
      <Modal
        open={searchOpen}
        onCancel={() => setSearchOpen(false)}
        title="搜索工作空间"
        footer={null}
        width={620}
      >
        <Input
          aria-label="输入知识库或文件名称"
          autoFocus
          prefix={<SearchOutlined aria-hidden="true" />}
          placeholder="输入知识库或文件名称"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          allowClear
          size="large"
        />
        <div className="search-results">
          <div className="section-label">知识库</div>
          {state.knowledgeBases
            .filter((k) => !matches || k.name.toLowerCase().includes(matches))
            .map((k) => (
              <button
                key={k.id}
                onClick={() => openSearchResult(`/knowledge/${k.id}`)}
              >
                <DatabaseOutlined aria-hidden="true" />
                <span>{k.name}</span>
                <ArrowRightOutlined aria-hidden="true" />
              </button>
            ))}
          <div className="section-label">文档</div>
          {state.documents
            .filter(
              (d) =>
                (!matches || d.name.toLowerCase().includes(matches)) &&
                (canManage || isRetrievable(d)),
            )
            .slice(0, 8)
            .map((d) => (
              <button
                key={d.id}
                onClick={() =>
                  openSearchResult(`/knowledge/${d.kbId}?doc=${d.id}`)
                }
              >
                <FileIcon format={d.format} small />
                <span>{d.name}</span>
                <ArrowRightOutlined aria-hidden="true" />
              </button>
            ))}
          {matches &&
            !state.knowledgeBases.some((k) => k.name.includes(search)) &&
            !state.documents.some((d) =>
              d.name.toLowerCase().includes(matches),
            ) && (
              <p className="search-empty">
                没有找到匹配内容，试试“制度”或“报销”。
              </p>
            )}
        </div>
      </Modal>
      {isAdmin && mode === "live" && <RuntimePanel open={runtimeOpen} onClose={() => setRuntimeOpen(false)} />}
    </div>
  );
}

function AccessDenied() {
  const auth=useAuth();
  const navigate = useNavigate();
  return (
    <div className="access-denied">
      <InfoCircleOutlined aria-hidden="true" />
      <h2>当前角色没有此操作入口</h2>
      <p>{auth.enabled ? "请联系管理员调整权限，或返回工作台。" : "你可以在右上角切换演示角色，或返回工作台。"}</p>
      <Button type="primary" onClick={() => navigate("/")}>
        返回工作台
      </Button>
    </div>
  );
}
