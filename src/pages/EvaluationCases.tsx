import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Alert, App, Button, Drawer, Input, Modal, Radio, Select, Space, Table, Tag } from "antd";
import { useStore } from "../store-context";
import { api } from "../live-api";
import { PageHeading } from "../components";
import { EvaluationRuns } from "../EvaluationRuns";
import { numberText, timeText } from "../demo";

type CaseLabel = "good" | "bad";
const statusLabels: Record<string, string> = { completed: "已完成", no_evidence: "依据不足", failed: "失败", canceled: "已停止", running: "运行中" };
interface CaseItem { id: string; runId: string; conversationId?: string; label: CaseLabel; note: string; expectedAnswer?: string; query: string; output: string | null; status: string; assistantName: string; applicationName?: string; model: string; tokens: number | null; citations: { name: string; location: string; content: string }[]; runCreatedAt?: string; createdAt: string }
interface CasePage { items: CaseItem[]; total: number; page: number; pageSize: number }
export function EvaluationCases() {
 const {state, mode} = useStore(); const {message, modal} = App.useApp();
 const [data,setData]=useState<CasePage>({items:[],total:0,page:1,pageSize:10});
 const [selectedCases,setSelectedCases]=useState<string[]>([]);
 const [page,setPage]=useState(1);const [label,setLabel]=useState("all");const [search,setSearch]=useState("");const [appliedSearch,setAppliedSearch]=useState("");
 const requestSequence=useRef(0);
 const [loading,setLoading]=useState(false);const [error,setError]=useState("");const [selected,setSelected]=useState<CaseItem|null>(null);
 const [editing,setEditing]=useState(false);const [editLabel,setEditLabel]=useState<CaseLabel>("good");const [note,setNote]=useState("");const [expectedAnswer,setExpectedAnswer]=useState("");const [saving,setSaving]=useState(false);
 const load=useCallback(async()=>{const sequence=++requestSequence.current;if(mode!=="live"){setData({items:[],total:0,page:1,pageSize:10});return}setLoading(true);setError("");try {const query=new URLSearchParams({page:String(page),pageSize:"10",label,query:appliedSearch});const result=await api<CasePage>(`/evaluation/cases?${query}`,state.role);if(sequence===requestSequence.current)setData(result)}catch(e){if(sequence===requestSequence.current)setError(e instanceof Error?e.message:"加载失败")}finally{if(sequence===requestSequence.current)setLoading(false)}},[mode,state.role,page,label,appliedSearch]);
 useEffect(()=>{void load();return ()=>{requestSequence.current++}},[load]);
 const openEdit=(item:CaseItem)=>{setSelected(item);setEditLabel(item.label);setNote(item.note);setExpectedAnswer(item.expectedAnswer || "");setEditing(true)};
 const save=async()=>{if(!selected||saving)return;setSaving(true);try{const result=await api<{item:CaseItem}>(`/evaluation/cases/${selected.id}`,state.role,{method:"PATCH",body:JSON.stringify({label:editLabel,note,expectedAnswer})});setSelected(result.item);setEditing(false);message.success("已更新案例");await load()}catch(e){message.error(e instanceof Error?e.message:"更新失败")}finally{setSaving(false)}};
 const remove=(item:CaseItem)=>modal.confirm({title:"从评测集中移除案例？",content:"只移除此案例，原始对话、日志及已产生的评测历史快照仍保留。",okText:"移除",okButtonProps:{danger:true},cancelText:"取消",onOk:async()=>{try{await api(`/evaluation/cases/${item.id}`,state.role,{method:"DELETE"});setSelected(null);setSelectedCases(ids=>ids.filter(id=>id!==item.id));message.success("已移除案例");if(data.items.length===1&&page>1)setPage(page-1);else await load()}catch(e){message.error(e instanceof Error?e.message:"移除失败")}}});
 return <><PageHeading eyebrow="EVALUATION DATASET" title="评测集" description="把线上对话中的好案例和问题案例收集起来，供后续分析与评测。" />
  {mode!=="live" ? <Alert showIcon type="info" title="切换到真实后端后使用评测集" description="从日志监测中的对话详情收录案例。案例保存在本机后端，演示模式不使用真实数据。" /> : <>
   <Alert showIcon type="info" title="线上案例评测集" description="Good / Bad 是人工分类。填写参考答案后可使用文本匹配，并对已完成结果发起模型辅助评判；其他案例只记录输出。" />
   <div className="case-panel"><div className="case-toolbar"><Space wrap><Input.Search aria-label="搜索案例" placeholder="搜索问题、助手或运行 ID" allowClear value={search} onChange={e=>setSearch(e.target.value)} onSearch={v=>{setPage(1);setAppliedSearch(v.trim())}} style={{width:260}} /><Select aria-label="案例分类筛选" value={label} onChange={v=>{setLabel(v);setPage(1)}} style={{width:150}} options={[{value:"all",label:"全部案例"},{value:"good",label:"Good Case"},{value:"bad",label:"Bad Case"}]} /></Space><span className="muted">共 {data.total} 条</span></div>
    {error && <Alert type="error" showIcon title={error} action={<Button onClick={()=>void load()}>重试</Button>} />}
    <Table<CaseItem> rowKey="id" rowSelection={{selectedRowKeys:selectedCases,onChange:keys=>setSelectedCases(keys.map(String)),preserveSelectedRowKeys:true}} loading={loading} dataSource={data.items} scroll={{x:800}} pagination={{current:page,pageSize:10,total:data.total,onChange:setPage,showSizeChanger:false,showTotal:n=>`共 ${n} 条`}} locale={{emptyText:"暂无案例，可从日志监测的对话详情中收录。"}} columns={[
     {title:"问题 / 助手",render:(_,item)=><button className="log-query" onClick={()=>setSelected(item)}><strong>{item.query}</strong><small>{item.applicationName || item.assistantName} · {item.runId}</small></button>},
     {title:"人工分类",dataIndex:"label",width:140,render:(v:CaseLabel)=><Tag color={v==="good"?"success":"error"}>{v==="good"?"Good Case":"Bad Case"}</Tag>},
     {title:"收录时间",dataIndex:"createdAt",width:140,render:(v:string)=>timeText(v)},
     {title:"",width:140,render:(_,item)=><Space><Button type="link" onClick={()=>setSelected(item)}>查看</Button><Button type="link" onClick={()=>openEdit(item)}>编辑</Button></Space>}
    ]}/></div>
    <EvaluationRuns selectedCases={selectedCases} onStarted={()=>setSelectedCases([])} />
  </>}
  <Drawer title="案例详情" open={!!selected&&!editing} onClose={()=>setSelected(null)} size={700} className="obs-drawer">
   {selected&&<><Space><Tag color={selected.label==="good"?"success":"error"}>{selected.label==="good"?"Good Case":"Bad Case"}</Tag><Tag>人工收录</Tag></Space><h2>{selected.query}</h2><p className="muted">{selected.applicationName || selected.assistantName} · {selected.model} · 运行 {selected.runId}</p>
   <h3>当次回答快照</h3><pre className="actual-source-content">{selected.output || "历史记录未保存回答快照"}</pre><h3>人工备注</h3><p>{selected.note || "暂无备注"}</p><h3>参考答案</h3><pre className="actual-source-content">{selected.expectedAnswer || "未设置；评测时不计入完全一致匹配"}</pre><h3>调用信息</h3><p className="muted">状态：{statusLabels[selected.status] || "未记录"} · Token：{selected.tokens==null?"未知":numberText(selected.tokens)} · 引用：{selected.citations?.length || 0} 条</p>
   {selected.citations?.map((c,i)=><div className="case-citation" key={i}><strong>{c.name} · {c.location}</strong><pre className="actual-source-content">{c.content}</pre></div>)}<Space wrap style={{marginTop:20}}><Link to={`/logs?type=chat&runId=${encodeURIComponent(selected.runId)}`}><Button>查看原始日志</Button></Link><Button onClick={()=>openEdit(selected)}>修改分类和备注</Button><Button danger onClick={()=>remove(selected)}>移除案例</Button></Space></>}
  </Drawer>
  <Modal title="编辑案例" open={editing} onCancel={()=>setEditing(false)} onOk={save} okText="保存" confirmLoading={saving}><p className="muted">修改人工分类和备注，不改变原始对话快照。</p><Radio.Group aria-label="案例分类" value={editLabel} onChange={e=>setEditLabel(e.target.value)} options={[{value:"good",label:"Good Case"},{value:"bad",label:"Bad Case"}]} /><p className="muted" style={{marginTop:16}}>备注</p><Input.TextArea rows={4} maxLength={2000} showCount value={note} onChange={e=>setNote(e.target.value)} /><p className="muted" style={{marginTop:20}}>参考答案（选填，用于文本匹配及模型辅助评判）</p><Input.TextArea rows={3} maxLength={10000} value={expectedAnswer} onChange={e=>setExpectedAnswer(e.target.value)} /></Modal>
 </>;
}
