"""File preview in temporary storage; no document or index writes."""
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated
from fastapi import APIRouter,Depends,UploadFile,File,Form
from .security import AppError,require
from .state import data_of
from .parsing import parse_document
from .chunking import split_units,STRATEGIES

def recommended(filename):
    ext=Path(filename).suffix.lower()
    if ext in {'.xls','.xlsx','.csv'}:return 'table'
    if ext in {'.md','.doc','.docx'}:return 'heading'
    return 'general'

def router_for(context,session_factory):
    router=APIRouter(prefix='/api/delivery')
    @router.post('/knowledge/{kb_id}/file-preview')
    async def preview(kb_id:str,file:Annotated[UploadFile,File()],strategy:Annotated[str,Form()]='auto',ctx=Depends(context)):
        require(ctx[0],'manager')
        if strategy!='auto' and strategy not in STRATEGIES:raise AppError('INVALID_CHUNK_METHOD','切分方法无效。')
        with session_factory()() as session:kb=data_of(session,'knowledgeBases',kb_id)
        name=Path(file.filename or 'file.txt').name;method=recommended(name) if strategy=='auto' else strategy
        # Preview stays local. Scanned/image PDFs requiring OCR are explicitly deferred.
        parsing={**kb.get('parsing',{}),'strategy':method,'pdfParser':'text','understandImages':False}
        with TemporaryDirectory(prefix='zhixu-preview-') as temp:
            path=Path(temp)/name;size=0
            with path.open('wb') as stream:
                while chunk:=await file.read(1024*1024):
                    size+=len(chunk)
                    if size>50*1024*1024:raise AppError('FILE_TOO_LARGE','预览文件超过 50 MB。',413)
                    stream.write(chunk)
            parsed=await parse_document(path,name,parsing)
            chunks=split_units([asdict(u) for u in parsed.units],{'name':name,'parsing':parsing},kb)
        return {'name':name,'strategy':method,'totalChunks':len(chunks),'chunks':chunks[:30],'previewLimit':30,'persisted':False}
    return router
