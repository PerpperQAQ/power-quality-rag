import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Button, Table, Tag } from "antd";
import { api } from "../live-api";
import { useStore } from "../store-context";
import { PageHeading } from "../components";
import { LogPolicy } from "../LogPolicy";
import { AccountManager } from "../AccountManager";
import { useAuth } from "../AuthGate";
type Audit={id:string;action:string;objectType:string;objectId:string;actorRole:string;actorSessionId:string;createdAt:string};
const actions:Record<string,string>={"auth.setup":"启用账号模式","auth.account_create":"创建账号","auth.account_edit":"修改账号","auth.password":"修改密码","logs.policy":"更新日志策略","logs.retention":"清理到期日志","mcp.credential":"更新 MCP 凭据","mcp.create":"注册 MCP 工具","mcp.enable":"启用 MCP 工具","mcp.disable":"停用 MCP 工具","app.debug":"调试草稿","tool.create":"创建 HTTPS 工具","tool.edit":"编辑 HTTPS 工具","budget.edit":"修改月预算","evaluation.review":"评测人工评分","app.create":"创建应用","app.edit":"编辑应用","app.publish":"发布应用","app.rollback":"切换应用版本","app.unpublish":"下线应用","skill.create":"创建 Skill","skill.edit":"编辑 Skill","price.create":"添加价格规则","case.create":"收录案例","case.edit":"编辑案例","case.delete":"移除案例","evaluation.start":"开始评测","evaluation.cancel":"停止评测"};
export function PlatformSettings(){
 const auth=useAuth();const {state,mode}=useStore();const [items,setItems]=useState<Audit[]>([]);const [total,setTotal]=useState(0);const [page,setPage]=useState(1);const [loading,setLoading]=useState(false);const [error,setError]=useState("");
 useEffect(()=>{let alive=true;if(mode!=="live")return;setLoading(true);api<{items:Audit[];total:number}>(`/platform/audit?page=${page}&pageSize=20`,state.role).then(r=>{if(alive){setItems(r.items);setTotal(r.total);setError("")}}).catch(e=>{if(alive)setError(e instanceof Error?e.message:"操作记录加载失败")}).finally(()=>{if(alive)setLoading(false)});return()=>{alive=false}},[mode,state.role,page]);
 return <><PageHeading eyebrow="SETTINGS" title="平台设置" description="查看本机角色边界与平台操作记录。" actions={<Link to="/models"><Button>管理模型</Button></Link>}/>{!auth.enabled&&<Alert showIcon type="warning" title="当前角色为演示选择" description="本机版本未启用账号认证；角色和浏览器会话标识不能作为企业身份或权限隔离。"/>}
 <div className="obs-panel obs-cost" style={{marginTop:20}}><h2>本机角色</h2><p>管理员可配置模型和查看平台操作记录；知识库管理人员管理知识与应用，并查看当前会话的对话；使用者使用已发布应用和自己的会话。本机账号可选启用；多租户隔离尚未接入。</p><Tag style={{marginTop:16}}>管理员</Tag><Tag>知识库管理人员</Tag><Tag>使用者</Tag></div>
 {mode==="live"&&<><AccountManager/><LogPolicy/></>}
 <div className="obs-panel" style={{marginTop:20}}><div className="obs-panel-head"><h2>平台操作记录</h2><p>仅覆盖应用、工具、Skill、案例、评测、预算及价格操作；不保存输入正文或密钥。历史操作和其他模块不在此列表。</p></div>{error&&<Alert type="error" title={error}/>}<Table<Audit> rowKey="id" loading={loading} dataSource={items} scroll={{x:760}} pagination={{current:page,pageSize:20,total,onChange:setPage,showSizeChanger:false,showTotal:n=>`共 ${n} 条`}} locale={{emptyText:mode==="live"?"暂无平台操作记录":"请切换到真实后端"}} columns={[{title:"时间",render:(_,r)=>new Date(r.createdAt).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false})},{title:"操作",render:(_,r)=>actions[r.action]||r.action},{title:"对象标识",dataIndex:"objectId"},{title:"角色",dataIndex:"actorRole"},{title:"账号或浏览器会话",dataIndex:"actorSessionId"}]}/></div></>;
}
