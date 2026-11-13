"""Embedding similarity boundaries with checkpointed paid batches."""
import copy
import math
from .chunking import split_units
from .services import result_usages,validate_vectors
from .security import AppError

def cosine(a,b):
    denominator=math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
    return sum(x*y for x,y in zip(a,b))/denominator if denominator else 0

async def semantic_segments(units,doc,kb,provider,cached,checkpoint):
    size=doc.get('chunkSizeOverride') or kb.get('chunkSize',600)
    probe_doc={**doc,'parsing':{**doc.get('parsing',{}),'strategy':'general'},'chunkSizeOverride':min(size,250),'overlapOverride':0}
    probes=cached.get('semanticProbes')
    if probes is None:
        probes=split_units(units,probe_doc,kb);cached['semanticProbes']=probes;checkpoint()
    vectors=cached.setdefault('semanticVectors',[])
    if len(vectors)>len(probes):raise AppError('INVALID_CHECKPOINT','语义切分检查点不一致。',409)
    while len(vectors)<len(probes):
        checkpoint();batch=probes[len(vectors):len(vectors)+16];result=await provider.embed([p['content'] for p in batch],kb['embedding'])
        cached['usage'].extend(result_usages(result));validate_vectors(result.data,len(batch));vectors.extend(result.data);checkpoint()
    from .model_catalog import embedding_input_budget
    byte_limit=embedding_input_budget(kb["embedding"])
    threshold={**kb.get('parsing',{}),**doc.get('parsing',{})}.get('semanticThreshold',.72)
    groups=[]
    for i,probe in enumerate(probes):
        previous=groups[-1] if groups else None
        same=previous and probe.get('sectionPath')==previous['sectionPath'] and not probe.get('table') and not previous.get('table')
        if same and len(previous['content'])+len(probe['content'])+2<=size and len((previous['content']+'\n\n'+probe['content']).encode())<=byte_limit and cosine(vectors[i-1],vectors[i])>=threshold:
            previous['content']+='\n\n'+probe['content'];previous['sourceLocations']=list(dict.fromkeys(previous['sourceLocations']+probe['sourceLocations']));previous['location']=' / '.join(previous['sourceLocations'])
        else:groups.append(copy.deepcopy(probe))
    for group in groups:group['chunkMethod']='semantic';group['splitNote']='以真实向量相似度 '+str(threshold)+' 判断相邻片段边界；保留来源位置。'
    return groups
