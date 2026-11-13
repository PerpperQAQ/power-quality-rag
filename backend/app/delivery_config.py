"""Validated application delivery configuration, independent of publication state."""
import copy
import json
import math
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from .security import AppError

class InputField(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    key:str=Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    label:str=Field(min_length=1,max_length=100)
    type:Literal['text','textarea','number','select','boolean']='text'
    required:bool=True
    placeholder:str=Field(default='',max_length=300)
    options:list[str]=Field(default_factory=list,max_length=40)
    default:str|float|bool|None=None
    minimum:float|None=None
    maximum:float|None=None
    @model_validator(mode='after')
    def coherent(self):
        if self.type=='select' and not self.options:raise ValueError('下拉字段需要选项')
        if self.type!='select' and self.options:raise ValueError('只有下拉字段可以有选项')
        if len(set(self.options))!=len(self.options) or any(not x.strip() or len(x)>100 for x in self.options):raise ValueError('选项为空、过长或重复')
        if any(v is not None and not math.isfinite(v) for v in (self.minimum,self.maximum)):raise ValueError('数值范围无效')
        if self.minimum is not None and self.maximum is not None and self.minimum>self.maximum:raise ValueError('最小值不能大于最大值')
        if self.default is not None:validate_value(self.model_dump(),self.default)
        return self

class Guardrails(BaseModel):
    model_config=ConfigDict(extra='forbid')
    blockedTopics:list[str]=Field(default_factory=list,max_length=30)
    requireCitations:bool=False
    refuseWithoutEvidence:bool=True
    humanHandoff:bool=False
    handoffKeywords:list[str]=Field(default_factory=lambda:['人工','投诉','赔偿'],max_length=20)
    maxOutputChars:int=Field(default=12000,ge=100,le=50000)
    @model_validator(mode='after')
    def words(self):
        if any(not x.strip() or len(x)>120 for x in self.blockedTopics+self.handoffKeywords):raise ValueError('策略词须为 1–120 字')
        return self

class Delivery(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scene:Literal['qa','customer_service','report','review','content','custom']='qa'
    inputFields:list[InputField]=Field(default_factory=list,max_length=20)
    outputFormat:Literal['markdown','text','json']='markdown'
    guardrails:Guardrails=Field(default_factory=Guardrails)
    memoryTurns:int=Field(default=6,ge=0,le=30)
    opening:str=Field(default='',max_length=2000)
    suggestions:list[str]=Field(default_factory=list,max_length=8)
    agentMaxSteps:int=Field(default=6,ge=1,le=20)
    knowledgeMode:Literal['tool','always']='tool'
    @model_validator(mode='after')
    def unique_fields(self):
        keys=[f.key for f in self.inputFields]
        if len(keys)!=len(set(keys)):raise ValueError('字段变量名不能重复')
        if any(not x.strip() or len(x)>200 for x in self.suggestions):raise ValueError('推荐问题须为 1–200 字')
        return self

def validate_value(field,value):
    label=field['label'];kind=field['type']
    if value is None or value=='':
        if field.get('required'):raise ValueError(f'请填写{label}')
        return
    if kind=='boolean':
        if type(value) is not bool:raise ValueError(f'{label}必须是开关值')
    elif kind=='number':
        if type(value) not in (int,float) or not math.isfinite(value):raise ValueError(f'{label}必须是有效数字')
        if field.get('minimum') is not None and value<field['minimum']:raise ValueError(f'{label}低于最小值')
        if field.get('maximum') is not None and value>field['maximum']:raise ValueError(f'{label}超过最大值')
    elif not isinstance(value,str) or len(value)>(10000 if kind=='textarea' else 1000):raise ValueError(f'{label}内容类型或长度无效')
    elif kind=='select' and value not in field['options']:raise ValueError(f'{label}不是可选值')

def validate_delivery(value):
    try:return Delivery.model_validate(value or {}).model_dump()
    except (ValidationError,ValueError,TypeError) as e:
        details='；'.join(str(x['msg']) for x in e.errors()[:3]) if isinstance(e,ValidationError) else str(e)
        raise AppError('INVALID_DELIVERY','应用配置无效：'+details[:400]) from None

def inputs_for(config,values):
    if not isinstance(values,dict):raise AppError('INVALID_INPUTS','业务输入必须为对象。')
    fields=config.get('inputFields',[]);keys={f['key'] for f in fields}
    if set(values)-keys:raise AppError('INVALID_INPUTS','业务输入包含未定义字段。')
    result={}
    for field in fields:
        value=values.get(field['key'],field.get('default'))
        try:validate_value(field,value)
        except ValueError as e:raise AppError('INVALID_INPUTS',str(e)) from None
        if value is not None:result[field['key']]=value
    return result

def effective_assistant(session,draft):
    from .state import data_of,validate_assistant
    base=copy.deepcopy(data_of(session,'assistants',draft['assistantId']))
    override=draft.get('assistantConfig')
    if override is not None:
        allowed={'model','prompt','kbIds','topK','candidateK','useRerank','retrieval','generation','noEvidenceReply','showQuote','multiTurn','keywordAnalysis'}
        if not isinstance(override,dict) or set(override)-allowed:raise AppError('INVALID_ASSISTANT_CONFIG','应用模型配置包含无效字段。')
        base=validate_assistant(session,{**base,**override},base,allow_empty=True)
    return base

def render_inputs(assistant,config,values):
    value=copy.deepcopy(assistant)
    def literal(v):return json.dumps(v,ensure_ascii=False) if not isinstance(v,str) else v
    value['prompt']=re.sub(r'\{\{\s*([a-z][a-z0-9_]*)\s*\}\}',lambda m:literal(values.get(m.group(1),'')),value.get('prompt',''))
    if config.get('outputFormat')=='json':value['prompt']+='\n只输出有效的 JSON，不要使用 Markdown 代码围栏。'
    value['multiTurn']=config.get('memoryTurns',6)>0
    return value

TEMPLATES=[
 {'id':'qa','name':'知识问答','description':'依据制度与产品资料回答，附可核对来源。','prompt':'根据知识库回答用户问题，引用来源编号；没有依据时说明无法确认。','fields':[],'refuse':True,'citations':True},
 {'id':'customer_service','name':'售后客服','description':'解释服务政策，识别投诉或赔偿，记录人工接续请求。','prompt':'你是客服助手，先理解问题，再依据资料解释政策。不要承诺未经授权的退款、赔偿或工单结果。','fields':[],'refuse':True,'citations':True},
 {'id':'report','name':'结构化报告','description':'按主题和受众整理摘要、分析与待确认事项。','prompt':'围绕主题 {{topic}} 为 {{audience}} 撰写报告，包含摘要、主要发现、依据与待确认事项。区分事实与推断。','fields':[{'key':'topic','label':'报告主题','type':'text','required':True},{'key':'audience','label':'阅读对象','type':'text','required':False,'default':'项目团队'}],'refuse':False,'citations':False},
 {'id':'review','name':'知识审校','description':'对照知识库审校材料，逐项给出问题与修改依据。','prompt':'请审校以下材料：{{document}}。按照问题、依据、修改建议组织回答；未找到依据的部分标为待核实。','fields':[{'key':'document','label':'待审校内容','type':'textarea','required':True}],'refuse':True,'citations':True},
 {'id':'content','name':'营销内容','description':'根据产品、受众和渠道起草可编辑文案。','prompt':'为产品 {{product}} 撰写面向 {{audience}} 的文案，渠道为 {{channel}}。不编造效果数据、认证或用户评价。','fields':[{'key':'product','label':'产品与卖点','type':'textarea','required':True},{'key':'audience','label':'目标受众','type':'text','required':True},{'key':'channel','label':'发布渠道','type':'select','options':['公众号','小红书','产品介绍'],'default':'产品介绍','required':True}],'refuse':False,'citations':False},
]

def template_delivery(item):
    return validate_delivery({'scene':item['id'],'inputFields':item['fields'],'guardrails':{'refuseWithoutEvidence':item['refuse'],'requireCitations':item['citations'],'humanHandoff':item['id']=='customer_service'},'opening':item['description']})
