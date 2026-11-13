import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../live-api";
import { PricingRules } from "../PricingRules";
import { BudgetNotice } from "../BudgetNotice";
import { SupplierBills } from "../SupplierBills";
import { Link, useSearchParams } from "react-router-dom";
import { Alert, Button, DatePicker, Input, Segmented, Select, Space, Table, Tag } from "antd";
import type { GetProps } from "antd";
import { SearchOutlined } from "@ant-design/icons";
import { useStore } from "../store-context";
import { PageHeading, RunStatusTag } from "../components";
import { numberText, timeText } from "../demo";
import { aggregateUsage, estimateCost } from "../observability";
import type { PriceRule } from "../observability";
import type { RunLog } from "../types";

type Domain = "build" | "chat" | "agent" | "usage";
const descriptions: Record<Domain,string> = {build:"查看文件构建状态、阶段与索引发布情况。",chat:"查看每次对话及其检索、生成和输出记录。",agent:"查看 Agent、工具和 Skill 的运行记录。",usage:"按模型与能力查看已知用量及缺失情况。"};
export function Observability() {
 const { state, mode }=useStore();
 const [prices,setPrices]=useState<PriceRule[]>([]);const [priceError,setPriceError]=useState("");
 const loadPrices=useCallback(async()=>{if(mode!=="live"){setPrices([]);return}try{setPrices((await api<{items:PriceRule[]}>("/platform/prices",state.role)).items);setPriceError("")}catch(e){setPriceError(e instanceof Error?e.message:"价格加载失败")}},[mode,state.role]);
 useEffect(()=>{void loadPrices()},[loadPrices]);
 const [params]=useSearchParams();
 const [domain,setDomain]=useState<Domain>(params.get("domain")==="usage"?"usage":"build");
 useEffect(()=>{if(params.get("domain")==="usage")setDomain("usage")},[params]); const [search,setSearch]=useState(""); const [status,setStatus]=useState("all");
 const [picker,setPicker]=useState<GetProps<typeof DatePicker.RangePicker>["value"]>(null);
 const [range,setRange]=useState<[string,string] | null>(null);
 const timeFiltered=useMemo(()=>state.logs.filter(log=>!range || (log.createdAt >= range[0] && log.createdAt <= range[1])),[state.logs,range]);
 const kind=domain === "build" ? "ingestion" : domain === "chat" ? "chat" : null;
 const rows=useMemo(()=>timeFiltered.filter(log=>(domain === "agent" ? log.executionType === "agent" : log.kind===kind) && (status==="all" || log.status===status) && `${log.query} ${log.assistantName} ${log.applicationName || ""} ${log.model} ${log.id}`.toLowerCase().includes(search.toLowerCase())),[timeFiltered,kind,status,search,domain]);
 const usage=useMemo(()=>aggregateUsage(timeFiltered),[timeFiltered]);
 const cost=useMemo(()=>estimateCost(timeFiltered,prices),[timeFiltered,prices]);
 const unknown=rows.filter(log=>log.tokens==null).length;
 const costRows=useMemo(()=>timeFiltered.map(log=>({log,...estimateCost([log],prices)})),[timeFiltered,prices]);
 const failures=useMemo(()=>{const groups=new Map<string,{code:string;count:number;last:RunLog}>();for(const log of timeFiltered){if(log.status!=="failed")continue;const code=log.errorCode||"未记录错误码";const group=groups.get(code);if(group){group.count++;if(log.createdAt>group.last.createdAt)group.last=log}else groups.set(code,{code,count:1,last:log})}return [...groups.values()].sort((a,b)=>b.count-a.count)},[timeFiltered]);
 const options=[{label:"知识库构建",value:"build"},{label:"对话日志",value:"chat"},{label:"Agent 调用",value:"agent"},{label:"用量与费用",value:"usage"}];
 const reset=(value:string)=>{setDomain(value as Domain);setSearch("");setStatus("all")};
 const logLink=(row:RunLog)=>`/logs?type=${row.kind}&runId=${encodeURIComponent(row.id)}`;
 return <div className="obs-design">
  <PageHeading eyebrow="OBSERVABILITY" title="可观测中心" description="从构建、调用到消耗，定位每次执行发生了什么。" actions={<Space wrap><Tag>{mode === "live" ? "实际记录" : "演示数据"}</Tag><Link to="/logs/design"><Button>查看设计原型</Button></Link></Space>} />
  <Alert showIcon type="info" title={mode === "live" ? "展示当前角色可访问的本机记录" : "演示模式使用示例记录"} description="实际记录只包含已采集内容；历史缺失与未返回用量不会按零计算。" />
  <div className="obs-header"><Segmented aria-label="观测类型" value={domain} options={options} onChange={reset}/></div>
  <div className="obs-period"><span>时间范围</span><DatePicker.RangePicker aria-label="观测时间范围" value={picker} onChange={values=>{setPicker(values);setRange(values?.[0]&&values?.[1]?[values[0].startOf("day").toISOString(),values[1].endOf("day").toISOString()]:null)}} /></div>
  {domain === "usage" ? <>
   <div className="obs-summary"><div><span>已知 Token</span><strong>{usage.knownCount ? numberText(usage.known) : "未知"}</strong><small>仅统计已返回的项目</small></div><div><span>用量未知</span><strong>{usage.unknownCount}</strong><small>逐次调用或历史记录</small></div><div><span>已知估算费用</span><strong>{mode === "live" && cost.knownCount ? `¥ ${cost.total.toFixed(6)}` : "未知"}</strong><small>{cost.unknownCount} 项无法估算；非账单</small></div></div>
   <div className="obs-panel obs-cost"><h2>运行费用明细</h2><p>按每次运行记录展示估算和未计价项；单次调用重试是否完整，以记录中实际返回的用量为准。</p><Table rowKey={r=>r.log.id} dataSource={costRows} pagination={{pageSize:10,hideOnSinglePage:true}} scroll={{x:800}} locale={{emptyText:"所选时间范围暂无记录"}} columns={[{title:"运行",render:(_,r)=><Link to={logLink(r.log)}>{r.log.applicationName||r.log.assistantName||r.log.query} · {r.log.id}</Link>},{title:"状态",render:(_,r)=><RunStatusTag status={r.log.status}/>},{title:"已知估算",render:(_,r)=>r.knownCount?`¥ ${r.total.toFixed(6)}`:r.log.tokens===0&&!r.unknownCount?"无可计价明细":"未知"},{title:"已计价 / 未知项",render:(_,r)=>`${r.knownCount} / ${r.unknownCount}`},{title:"开始时间",render:(_,r)=>timeText(r.log.createdAt)}]} /></div>
   <div className="obs-panel obs-cost"><h2>异常分布</h2><p>按失败运行记录中的错误码汇总；未记录错误码不推断原因。</p><Table rowKey="code" dataSource={failures} pagination={{pageSize:10,hideOnSinglePage:true}} locale={{emptyText:"所选时间范围没有失败记录"}} columns={[{title:"错误码",dataIndex:"code"},{title:"失败运行数",dataIndex:"count"},{title:"最近一次",render:(_,r)=><Link to={logLink(r.last)}>{timeText(r.last.createdAt)} · 查看记录</Link>}]}/></div>
   <div className="obs-panel obs-cost"><h2>按能力与模型</h2><p>统计当前角色可见的全部类型记录，包含失败、取消和模型测试；未知项不计入已知合计。</p><Table rowKey="key" dataSource={usage.groups} pagination={{pageSize:10,hideOnSinglePage:true}} scroll={{x:650}} locale={{emptyText:"所选时间范围没有可用记录"}} columns={[{title:"调用能力",dataIndex:"kind"},{title:"模型",dataIndex:"model"},{title:"已知 Token",render:(_,v)=>v.knownCount?numberText(v.known):"未知"},{title:"未知项",dataIndex:"unknownCount"},{title:"关联运行数",dataIndex:"runs"}]} /><div className="obs-notice">没有逐次明细的历史记录单列为“仅总量”；不同计费单位不会转换为 Token。费用根据当前手动规则和运行开始时间估算，未知项不计入；缓存单价需手动配置并且供应商返回命中数；阶梯等复杂计价不在估算范围内。供应商账单可手动导入并核对范围。</div>{mode==="live"&&<>{priceError&&<Alert type="error" title={priceError} action={<Button onClick={()=>void loadPrices()}>重试</Button>}/>}<PricingRules prices={prices} onSaved={()=>void loadPrices()}/><BudgetNotice prices={prices}/></>}</div>
   {mode==="live"&&<SupplierBills logs={state.logs} prices={prices}/>}
  </> : <>
   <div className="obs-summary"><div><span>匹配记录数</span><strong>{rows.length}</strong><small>受时间和筛选条件影响</small></div><div><span>失败与中断</span><strong>{rows.filter(l=>l.status==="failed"||l.status==="canceled").length}</strong><small>包括失败与取消</small></div><div><span>总用量未知</span><strong>{unknown}</strong><small>未知不按零计算</small></div></div>
   <div className="obs-panel"><div className="obs-panel-head"><h2>{domain === "build" ? "知识库构建" : domain === "agent" ? "Agent 调用" : "对话日志"}</h2><p>{descriptions[domain]}</p></div>
   <Space wrap className="obs-filters"><Input aria-label="搜索观测记录" prefix={<SearchOutlined />} placeholder="搜索问题、文档、模型或 ID" value={search} onChange={e=>setSearch(e.target.value)} allowClear/><Select aria-label="筛选状态" value={status} onChange={setStatus} style={{width:135}} options={[{label:"全部状态",value:"all"},{label:"已完成",value:"completed"},{label:"失败",value:"failed"},{label:"已停止",value:"canceled"},{label:"依据不足",value:"no_evidence"},{label:"执行中",value:"running"}]}/></Space>
   <Table<RunLog> rowKey="id" dataSource={rows} pagination={{pageSize:10,hideOnSinglePage:true,showTotal:n=>`共 ${n} 条`}} scroll={{x:830}} locale={{emptyText:"暂无匹配记录"}} columns={[
    {title:domain==="build"?"文档 / 知识库":domain==="agent"?"输入 / Agent":"问题 / 助手",render:(_,r)=><Link className="obs-record" to={logLink(r)}><strong>{r.query}</strong><small>{r.applicationName || r.assistantName} · {r.id}</small></Link>},
    {title:"状态",render:(_,r)=><RunStatusTag status={r.status}/>},{title:"Token",render:(_,r)=>r.tokens==null?"未知":numberText(r.tokens)},
    {title:"耗时",render:(_,r)=>r.status==="running"?"执行中":`${r.duration.toFixed(1)} s`},{title:"开始时间",render:(_,r)=>timeText(r.createdAt)},
    {title:"",render:(_,r)=><Link to={logLink(r)}><Button type="link">查看详情</Button></Link>}
   ]}/></div>
  </>}
 </div>;
}
