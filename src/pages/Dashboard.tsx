import { useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { api } from "../live-api";
import { Button, Input, Progress, Select, Space, Tag } from "antd";
import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  CommentOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { useStore } from "../store-context";
import { isRetrievable, numberText, timeText } from "../demo";
import {
  DocStatusTag,
  FileName,
  KBIcon,
  PageHeading,
  TextLink,
} from "../components";

function loadIds(key:string):string[]{try{const value=JSON.parse(localStorage.getItem(key)||"[]");return Array.isArray(value)?value.filter((v):v is string=>typeof v==="string"):[]}catch{return []}}

export function Dashboard() {
  const { state, mode } = useStore();
  const navigate = useNavigate();
  const canManage = state.role !== "user";
  const [publishedApps,setPublishedApps]=useState<{id:string;publishedName:string;publishedDescription?:string;publishedVersionId:string | null;publishedNumber:number;type:string}[]>([]);
  const [appQuery,setAppQuery]=useState("");const [appFilter,setAppFilter]=useState("all");
  const [favorites,setFavorites]=useState<string[]>(()=>loadIds("zhixu-app-favorites"));
  const [recent,setRecent]=useState<string[]>(()=>loadIds("zhixu-app-recent"));
  const toggleFavorite=(id:string)=>{const next=favorites.includes(id)?favorites.filter(x=>x!==id):[...favorites,id];setFavorites(next);localStorage.setItem("zhixu-app-favorites",JSON.stringify(next))};
  const openApp=(id:string)=>{const next=[id,...recent.filter(x=>x!==id)].slice(0,20);setRecent(next);localStorage.setItem("zhixu-app-recent",JSON.stringify(next));navigate(`/platform/chat/${id}`)};
  const shownApps=publishedApps.filter(app=>(app.publishedName+" "+(app.publishedDescription||"")).toLowerCase().includes(appQuery.toLowerCase())&&(appFilter==="all"||(appFilter==="favorites"?favorites.includes(app.id):appFilter==="recent"?recent.includes(app.id):app.type===appFilter))).sort((a,b)=>appFilter==="recent"?recent.indexOf(a.id)-recent.indexOf(b.id):0);
  const [appsError,setAppsError]=useState("");
  useEffect(()=>{let alive=true;if(mode!=="live"){setPublishedApps([]);return}api<{items:typeof publishedApps}>("/platform/apps",state.role).then(r=>{if(alive){setPublishedApps(r.items.filter(x=>x.publishedVersionId));setAppsError("")}}).catch(()=>{if(alive)setAppsError("应用列表加载失败，请稍后重试。")});return()=>{alive=false}},[mode,state.role]);
  const ready = state.documents.filter(isRetrievable).length;
  const attention = state.documents.filter((d) =>
    ["failed", "review"].includes(d.status),
  );
  const metrics = [
    {
      label: "知识库",
      value: state.knowledgeBases.length,
      unit: "个",
      desc: "集中管理企业知识资产",
      icon: DatabaseOutlined,
      color: "blue",
    },
    {
      label: "知识文档",
      value: state.documents.length,
      unit: "份",
      desc: `${ready} 份已就绪，可用于问答`,
      icon: FileTextOutlined,
      color: "green",
    },
    {
      label: "问答助手",
      value: state.assistants.length,
      unit: "个",
      desc: `${state.assistants.filter((a) => a.enabled).length} 个助手已启用`,
      icon: CommentOutlined,
      color: "purple",
    },
    {
      label: mode === "live" ? "对话查询记录" : "演示对话记录",
      value: state.logs.filter((l) => l.kind === "chat").length,
      unit: "次",
      desc: "每次查询都有迹可循",
      icon: CheckCircleOutlined,
      color: "orange",
    },
  ];
  return (
    <div className="dashboard-page">
      <PageHeading
        eyebrow="WORKSPACE OVERVIEW"
        title={canManage?"应用交付工作台":"业务应用工作台"}
        description={canManage?"从知识准备、应用编排到评测发布，持续交付可追溯的业务应用。":"选择已发布应用，填写业务信息并查看回答依据。"}
        actions={
          <Button
            type="primary"
            icon={<CommentOutlined aria-hidden="true" />}
            onClick={() => navigate("/platform/apps")}
          >
            {canManage?"管理应用":"浏览应用"}
          </Button>
        }
      />
      {mode === "live" && <><div className="section-heading"><div><h2>已发布应用 <span>{publishedApps.length}</span></h2><p>搜索、收藏或继续最近使用；收藏和最近记录保存在本浏览器。</p></div><TextLink onClick={()=>navigate("/platform/apps")}>查看全部</TextLink></div>
        <Space wrap style={{marginBottom:16}}><Input.Search aria-label="搜索应用" value={appQuery} onChange={e=>setAppQuery(e.target.value)} allowClear placeholder="搜索应用名称或描述" style={{width:240}}/><Select aria-label="应用分类" value={appFilter} onChange={setAppFilter} style={{width:150}} options={[{value:"all",label:"全部应用"},{value:"favorites",label:"我的收藏"},{value:"recent",label:"最近使用"},{value:"simple",label:"知识助手"},{value:"agent",label:"Agent"},{value:"workflow",label:"工作流"}]}/></Space>
        {appsError ? <p className="muted">{appsError}</p> : shownApps.length ? <div className="kb-grid dashboard-kbs">{shownApps.map(app=><div className="kb-card" key={app.id}><div className="kb-card-top"><KBIcon color="blue"/><Tag color="success">v{app.publishedNumber}</Tag></div><h3>{app.publishedName}</h3><p>{app.publishedDescription || "知识应用"}</p><div className="kb-card-footer"><Button type="link" onClick={()=>openApp(app.id)}>进入应用 <ArrowRightOutlined /></Button><Button type="text" onClick={()=>toggleFavorite(app.id)}>{favorites.includes(app.id)?"取消收藏":"收藏"}</Button></div></div>)}</div> : <p className="muted">{publishedApps.length?"没有符合条件的应用。":"暂无已发布应用，可在应用模块创建和发布。"}</p>}</>}
      {canManage && <div className="metric-grid">
        {metrics.map(({ label, value, unit, desc, icon: Icon, color }) => (
          <div className="metric-card" key={label}>
            <div className="metric-top">
              <span>{label}</span>
              <span className={`metric-icon ${color}`}>
                <Icon aria-hidden="true" />
              </span>
            </div>
            <div className="metric-value">
              {numberText(value)}
              <small>{unit}</small>
            </div>
            <p>{desc}</p>
          </div>
        ))}
      </div>}
      {canManage && (
        <div className="quick-start">
          <div className="quick-start-intro">
            <div className="section-label">从知识到答案</div>
            <h2>准备知识，交付业务应用</h2>
            <p>完成配置后，通过应用编排、调试与评测检查交付效果。</p>
          </div>
          <div className="quick-start-steps">
            {[
              {
                title: "配置模型",
                text: "配置通义或兼容模型服务",
                icon: SettingOutlined,
                path: state.role === "admin" ? "/models" : "/knowledge",
                number: "01",
              },
              {
                title: "构建知识库",
                text: "导入文件，检查解析结果",
                icon: UploadOutlined,
                path: "/knowledge",
                number: "02",
              },
              {
                title: "编排与发布",
                text: "配置应用，通过评测后发布",
                icon: CommentOutlined,
                path: "/platform/apps",
                number: "03",
              },
            ].map(({ title, text, icon: Icon, path, number }) => (
              <button
                key={title}
                disabled={title === "配置模型" && state.role !== "admin"}
                onClick={() => navigate(path)}
              >
                <span className="step-number">{number}</span>
                <span className="step-icon">
                  <Icon aria-hidden="true" />
                </span>
                <strong>{title}</strong>
                <small>{text}</small>
                <ArrowRightOutlined aria-hidden="true" />
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="section-heading">
        <div>
          <h2>
            我的知识库 <span>{state.knowledgeBases.length}</span>
          </h2>
          <p>有序沉淀，让每一份资料都产生价值</p>
        </div>
        <TextLink onClick={() => navigate("/knowledge")}>查看全部</TextLink>
      </div>
      <div className="kb-grid dashboard-kbs">
        {state.knowledgeBases.slice(0, 3).map((k) => {
          const docs = state.documents.filter((d) => d.kbId === k.id);
          const available = docs.filter((d) => isRetrievable(d)).length;
          return (
            <button
              className="kb-card"
              key={k.id}
              onClick={() => navigate(`/knowledge/${k.id}`)}
            >
              <div className="kb-card-top">
                <KBIcon color={k.color} icon={k.icon} />
                <Tag variant="filled">
                  {docs.length ? "共享知识库" : "等待导入"}
                </Tag>
              </div>
              <h3>{k.name}</h3>
              <p>{k.description}</p>
              <div className="kb-card-meta">
                <span>
                  <FileTextOutlined aria-hidden="true" />
                  {docs.length} 份文档
                </span>
                <span>
                  <CheckCircleOutlined aria-hidden="true" />
                  {available} 份已就绪
                </span>
              </div>
              <div className="kb-card-footer">
                <span>更新于 {timeText(k.updatedAt)}</span>
                <ArrowRightOutlined aria-hidden="true" />
              </div>
            </button>
          );
        })}
      </div>
      <div className="dashboard-lower">
        <section className="panel">
          <div className="panel-heading">
            <h2>最近更新</h2>
            <Button
              type="text"
              size="small"
              onClick={() => navigate("/knowledge")}
            >
              全部文档 <ArrowRightOutlined aria-hidden="true" />
            </Button>
          </div>
          <div className="recent-docs">
            {state.documents.slice(0, 4).map((d) => (
              <div key={d.id}>
                <FileName
                  doc={d}
                  onClick={() => navigate(`/knowledge/${d.kbId}?doc=${d.id}`)}
                />
                <DocStatusTag status={d.status} progress={d.progress} />
              </div>
            ))}
          </div>
        </section>
        <section className="panel health-panel">
          <div className="panel-heading">
            <h2>知识健康度</h2>
            <SafetyCertificateOutlined aria-hidden="true" />
          </div>
          <div className="health-progress">
            <Progress
              type="circle"
              percent={
                state.documents.length
                  ? Math.round((ready / state.documents.length) * 100)
                  : 0
              }
              size={100}
              strokeColor="#3f76eb"
              strokeWidth={7}
            />
            <div>
              <strong>{ready} 份知识已准备好</strong>
              <p>
                {attention.length
                  ? `${attention.length} 份文档需要检查，处理后可提升知识覆盖。`
                  : "当前文档均可正常用于问答。"}
              </p>
            </div>
          </div>
          {canManage && attention.length > 0 ? (
            <button
              className="attention-banner"
              onClick={() =>
                navigate(`/knowledge/${attention[0].kbId}?status=attention`)
              }
            >
              <span className="status-dot warning" />
              查看 {attention.length} 个待处理问题
              <ArrowRightOutlined aria-hidden="true" />
            </button>
          ) : (
            <Button
              block
              icon={<PlusOutlined aria-hidden="true" />}
              onClick={() =>
                navigate(canManage ? "/knowledge?create=1" : "/chat")
              }
            >
              {canManage ? "添加更多知识" : "向知识助手提问"}
            </Button>
          )}
        </section>
      </div>
    </div>
  );
}
