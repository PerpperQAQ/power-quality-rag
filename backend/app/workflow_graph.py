"""Validation of finite, single-path-at-a-time workflow graphs."""
import re
from .security import AppError

ID = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")

def invalid(message):
    raise AppError("INVALID_WORKFLOW_GRAPH", message)

def validate_graph(graph, depth=0, budget=None):
    if budget is None:budget=[0]
    if depth>2:invalid("子流程嵌套最多 2 层。")
    from .workflow_runtime import STEP_TYPES, validate_steps
    if not isinstance(graph,dict) or set(graph)!={"nodes","edges"}:invalid("工作流图必须包含节点和连线。")
    nodes=graph['nodes'];edges=graph['edges']
    if not isinstance(nodes,list) or not 2<=len(nodes)<=20 or not isinstance(edges,list) or len(edges)>40:invalid("工作流需要 2 至 20 个节点，最多 40 条连线。")
    budget[0]+=len(nodes)
    if budget[0]>50:invalid("包含子流程的节点总数不能超过 50。")
    by_id={};clean_nodes=[]
    for n in nodes:
        if not isinstance(n,dict) or set(n)-{'id','type','config','x','y'}:invalid("节点配置无效。")
        ident=n.get('id');kind=n.get('type');config=n.get('config',{})
        if not isinstance(ident,str) or not ID.fullmatch(ident) or ident in by_id or ident=="input":invalid("节点标识无效或重复。")
        if not isinstance(kind,str) or kind not in STEP_TYPES|{'start','subflow','repeat','workflow_ref'} or not isinstance(config,dict) or "type" in config:invalid("不支持该节点类型。")
        if kind=='start':
            if config:invalid("开始节点无需配置。")
            step={'type':kind}
        elif kind=='workflow_ref':
            from .security import safe_id
            if set(config)!={'appId','versionId'}:invalid("引用工作流必须选择应用与版本。")
            step={'type':kind,'appId':safe_id(config['appId']),'versionId':safe_id(config['versionId'])}
        elif kind in {'subflow','repeat'}:
            allowed={'graph'} if kind=='subflow' else {'graph','maxItems'}
            if set(config)!=allowed:invalid("子流程节点配置无效。")
            if kind=='repeat':
                maximum=config.get('maxItems')
                if isinstance(maximum,bool) or not isinstance(maximum,int) or not 1<=maximum<=10:invalid("循环最多处理 1 至 10 行。")
            step={'type':kind,'graph':validate_graph(config['graph'],depth+1,budget),**({'maxItems':maximum} if kind=='repeat' else {})}
        elif kind=='condition':
            if set(config)!={'contains'} or not isinstance(config['contains'],str) or not config['contains'].strip() or len(config['contains'])>10000:invalid("条件节点需要填写匹配文本。")
            step={'type':kind,'contains':config['contains']}
        else:
            raw={'type':kind,**config}
            step=validate_steps([raw] if kind=='answer' else [raw,{'type':'answer'}])[0]
        position={}
        for key in ('x','y'):
            v=n.get(key,0)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not -10000<=v<=10000:invalid("节点位置无效。")
            position[key]=v
        clean={'id':ident,'type':kind,'config':{k:v for k,v in step.items() if k!='type'},**position};by_id[ident]=clean;clean_nodes.append(clean)
    starts=[n for n in clean_nodes if n['type']=='start'];answers=[n for n in clean_nodes if n['type']=='answer']
    if len(starts)!=1 or len(answers)!=1:invalid("必须有且只有一个开始和一个回复节点。")
    outgoing={k:[] for k in by_id};incoming={k:0 for k in by_id};clean_edges=[]
    for edge in edges:
        if not isinstance(edge,dict) or set(edge)-{'source','target','branch'}:invalid("连线格式无效。")
        source=edge.get('source');target=edge.get('target');branch=edge.get('branch','')
        if not isinstance(source,str) or not isinstance(target,str) or source not in by_id or target not in by_id or source==target:invalid("连线端点无效。")
        if not isinstance(branch,str) or branch not in ('','yes','no'):invalid("连线分支无效。")
        clean={'source':source,'target':target,'branch':branch}
        if clean in clean_edges:invalid("连线重复。")
        outgoing[source].append(clean);incoming[target]+=1;clean_edges.append(clean)
    if incoming[starts[0]['id']]!=0:invalid("开始节点不能有输入连线。")
    for n in clean_nodes:
        out=outgoing[n['id']];kind=n['type']
        if kind=='answer' and out:invalid("回复节点不能有输出连线。")
        if kind=='condition' and (len(out)!=2 or {e['branch'] for e in out}!={'yes','no'}):invalid("条件节点必须分别连接命中和未命中两个分支。")
        if kind not in {'condition','answer'} and (len(out)!=1 or out[0]['branch']!=''):invalid("普通节点必须有且只有一条输出连线。")
        if kind!='start' and incoming[n['id']]==0:invalid("存在未连接的节点。")
    visited=set();active=set()
    def visit(ident):
        if ident in active:invalid("工作流图不能包含环，请使用受限的逐行处理节点。")
        if ident in visited:return
        active.add(ident)
        for e in outgoing[ident]:visit(e['target'])
        active.remove(ident);visited.add(ident)
    visit(starts[0]['id'])
    if len(visited)!=len(by_id):invalid("所有节点必须从开始节点可达。")
    return {'nodes':clean_nodes,'edges':clean_edges}
