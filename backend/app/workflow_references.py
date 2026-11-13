"""Freeze published workflow references and reject recursive dependencies."""
import copy
from .security import AppError
from .state import record
from .workflow_graph import validate_graph
from .workflow_runtime import validate_steps


def resolve_references(session,graph,root_app_id):
    references={};node_count=[0]
    def walk(current,depth,path):
        if depth>2:raise AppError('WORKFLOW_REFERENCE_DEPTH','包含引用的工作流最多嵌套 2 层。')
        for node in current['nodes']:
            node_count[0]+=1
            if node_count[0]>50:raise AppError('WORKFLOW_REFERENCE_LIMIT','包含引用的工作流最多 50 个节点。')
            kind=node['type'];config=node['config']
            if kind in {'subflow','repeat'}:walk(config['graph'],depth+1,path)
            elif kind=='workflow_ref':
                app_id=config['appId'];version_id=config['versionId']
                if app_id in path:raise AppError('WORKFLOW_REFERENCE_CYCLE','工作流不能直接或间接引用自身。',409)
                app=record(session,'platformApps',app_id);version=record(session,'platformVersions',version_id)
                if not app or not app.data.get('publishedVersionId'):raise AppError('WORKFLOW_REFERENCE_UNPUBLISHED','引用的工作流已下线或不存在。',409)
                if not version or version.data.get('appId')!=app_id or version.data.get('type')!='workflow' or version.data.get('debug'):raise AppError('WORKFLOW_REFERENCE_INVALID','引用的工作流版本不存在或不可用。',409)
                value=version.data;child_graph=validate_graph(value['graph']) if value.get('graph') else None
                child_steps=validate_steps(value.get('steps')) if child_graph is None else []
                references[version_id]={'appId':app_id,'versionId':version_id,'name':value['name'],'number':value['number'],'graph':copy.deepcopy(child_graph),'steps':copy.deepcopy(child_steps),'assistantSnapshot':copy.deepcopy(value['assistantSnapshot'])}
                if child_graph:walk(child_graph,depth+1,path|{app_id})
                else:
                    if depth+1>2:raise AppError('WORKFLOW_REFERENCE_DEPTH','包含引用的工作流最多嵌套 2 层。')
                    node_count[0]+=len(child_steps)
                    if node_count[0]>50:raise AppError('WORKFLOW_REFERENCE_LIMIT','包含引用的工作流最多 50 个节点。')
    walk(graph,0,{root_app_id})
    return references
