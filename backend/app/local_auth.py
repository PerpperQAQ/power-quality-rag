"""Opt-in local account authentication for the loopback-only workbench."""
import base64
import hashlib
import hmac
import secrets
import time
import threading
from datetime import datetime,timedelta,timezone
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from .db import Job,Record,revision_lock,bump
from .security import AppError,safe_id,require
from .state import now,put,record,text_field
from .services import uid
from .platform_audit import write_audit

COOKIE='zhixu_session'
DUMMY_SALT=b'zhixu-dummy-salt'
_ATTEMPTS={}
_ATTEMPT_LOCK=threading.Lock()

def reserve_attempt(key):
    current=time.monotonic()
    with _ATTEMPT_LOCK:
        if key not in _ATTEMPTS and len(_ATTEMPTS)>=1000:
            for k in list(_ATTEMPTS):
                if not any(current-t<60 for t in _ATTEMPTS[k]):_ATTEMPTS.pop(k,None)
            if len(_ATTEMPTS)>=1000:raise AppError("LOGIN_RATE_LIMIT","尝试次数过多，请一分钟后再试。",429)
        attempts=[t for t in _ATTEMPTS.get(key,[]) if current-t<60]
        if len(attempts)>=5:raise AppError("LOGIN_RATE_LIMIT","尝试次数过多，请一分钟后再试。",429)
        attempts.append(current);_ATTEMPTS[key]=attempts

def clear_attempts(key):
    with _ATTEMPT_LOCK:_ATTEMPTS.pop(key,None)

def password_hash(password,salt):return hashlib.scrypt(password.encode('utf-8'),salt=salt,n=16384,r=8,p=1,dklen=32)
def credentials(body):
    username=text_field(body,'username',60,True).strip().lower();password=body.get('password')
    import re
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,59}',username):raise AppError('INVALID_USERNAME','账号须为 3–60 位英文字母、数字、点、下划线或短横线。')
    if not isinstance(password,str) or not 12<=len(password)<=128:raise AppError('INVALID_PASSWORD','密码须为 12–128 个字符。')
    return username,password

def new_account(username,password,role):
    salt=secrets.token_bytes(16);digest=password_hash(password,salt)
    return {'id':uid('account'),'username':username,'role':role,'enabled':True,'salt':base64.b64encode(salt).decode(),'passwordHash':base64.b64encode(digest).decode(),'createdAt':now()}

def public_account(value):return {k:value[k] for k in ('id','username','role','enabled','createdAt')}
def hash_token(token):return hashlib.sha256(token.encode()).hexdigest()
def find_account(session,username):return session.scalars(select(Record).where(Record.collection=='platformUsers',Record.data['username'].astext==username)).first()
def active_identity(session,token):
    if not isinstance(token,str) or len(token)!=43:return None
    try: token.encode('ascii')
    except UnicodeEncodeError:return None
    row=record(session,'platformSessions',hash_token(token))
    if not row:return None
    try:
        expiry=datetime.fromisoformat(row.data['expiresAt'])
        if expiry<=datetime.now(timezone.utc):return None
    except (ValueError,KeyError,TypeError):return None
    user=record(session,'platformUsers',row.data.get('userId',''))
    if not user or not user.data.get('enabled') or user.data.get('role') not in {'admin','manager','user'}:return None
    return public_account(user.data)

def issue_session(session,account,response):
    token=secrets.token_urlsafe(32);identifier=hash_token(token)
    expires=datetime.now(timezone.utc)+timedelta(hours=12)
    put(session,'platformSessions',identifier,{'userId':account['id'],'expiresAt':expires.isoformat()})
    response.set_cookie(COOKIE,token,max_age=12*3600,httponly=True,samesite='strict',secure=False,path='/')

def check_origin(request):
    host=request.headers.get('host','').lower();origin=request.headers.get('origin')
    from urllib.parse import urlsplit
    try:
        parsed=urlsplit('http://'+host)
        if parsed.hostname not in {'127.0.0.1','localhost'} or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or parsed.port is not None and not 1<=parsed.port<=65535:raise ValueError()
    except ValueError:raise AppError('INVALID_HOST','本机账号模式只接受本机地址。',403) from None
    if origin and origin not in {f'http://{host}'}:raise AppError('INVALID_ORIGIN','请求来源不允许。',403)

def router_for(context,session_factory):
    router=APIRouter(prefix='/api/auth')
    @router.get('/status')
    def status(request:Request):
        with session_factory()() as session:
            config=record(session,'platformAuth','local');enabled=bool(config and config.data.get('enabled'))
            identity=active_identity(session,request.cookies.get(COOKIE,'')) if enabled else None
            return {'enabled':enabled,'authenticated':bool(identity),'user':identity}
    @router.post('/setup')
    def setup(body:dict,request:Request,response:Response,ctx=Depends(context)):
        require(ctx[0],'admin');check_origin(request);username,password=credentials(body)
        if body.get('adoptExisting') is not True:raise AppError('MIGRATION_REQUIRED','请确认将已有会话和案例归属首位管理员。')
        account=new_account(username,password,'admin')
        with session_factory().begin() as session:
            revision_lock(session);existing=record(session,'platformAuth','local')
            if existing and existing.data.get('enabled'):raise AppError('AUTH_ALREADY_ENABLED','账号模式已启用。',409)
            if session.scalar(select(Job.id).where(Job.status.in_(['queued','running'])).limit(1)):raise AppError('BUSY','请等待现有任务结束后启用账号模式。',409)
            if find_account(session,username):raise AppError('USERNAME_EXISTS','账号已存在。',409)
            for row in session.scalars(select(Record).where(Record.collection.in_(['conversations','logs','evaluationCases','evaluationRuns','evaluationJudgeBatches']))):row.session_id=account['id']
            for row in session.scalars(select(Job)):row.session_id=account['id']
            put(session,'platformUsers',account['id'],account);put(session,'platformAuth','local',{'enabled':True,'createdAt':now()})
            issue_session(session,account,response);write_audit(session,('admin',account['id']),'auth.setup','account',account['id']);bump(session)
        return {'user':public_account(account)}
    @router.post('/login')
    def login(body:dict,request:Request,response:Response):
        check_origin(request);raw=body.get('username');password=body.get('password')
        username=raw.strip().lower() if isinstance(raw,str) and len(raw)<=60 else ''
        if not isinstance(password,str) or len(password)>128:password=''
        key=('login',request.client.host if request.client else 'local',username);reserve_attempt(key)
        with session_factory().begin() as session:
            config=record(session,'platformAuth','local')
            if not config or not config.data.get('enabled'):raise AppError('AUTH_DISABLED','账号模式尚未启用。',409)
            user=find_account(session,username);value=user.data if user else None
            try:salt=base64.b64decode(value['salt'],validate=True) if value else DUMMY_SALT;expected=base64.b64decode(value['passwordHash'],validate=True) if value else b'\0'*32
            except (ValueError,KeyError):salt=DUMMY_SALT;expected=b'\0'*32
            valid=hmac.compare_digest(password_hash(password,salt),expected)
            if not valid or not value or not value.get('enabled'):
                raise AppError('LOGIN_FAILED','账号或密码错误。',401)
            clear_attempts(key);issue_session(session,value,response);return {'user':public_account(value)}
    @router.post('/logout')
    def logout(request:Request,response:Response,ctx=Depends(context)):
        if not getattr(request.state,'identity',None):raise AppError('AUTH_REQUIRED','请先登录。',401)
        with session_factory().begin() as session:
            token=request.cookies.get(COOKIE,'')
            if token and len(token)==43:
                row=record(session,'platformSessions',hash_token(token))
                if row:session.delete(row)
        response.delete_cookie(COOKIE,path='/');return {'ok':True}
    @router.get('/accounts')
    def accounts(request:Request,ctx=Depends(context)):
        if not getattr(request.state,'identity',None):raise AppError('AUTH_REQUIRED','请先登录。',401)
        require(ctx[0],'admin')
        with session_factory()() as session:return {'items':[public_account(row.data) for row in session.scalars(select(Record).where(Record.collection=='platformUsers'))]}
    @router.post('/accounts')
    def add_account(body:dict,request:Request,ctx=Depends(context)):
        if not getattr(request.state,'identity',None):raise AppError('AUTH_REQUIRED','请先登录。',401)
        require(ctx[0],'admin');username,password=credentials(body);role=body.get('role')
        if not isinstance(role,str) or role not in {'admin','manager','user'}:raise AppError('INVALID_ROLE','角色无效。')
        account=new_account(username,password,role)
        with session_factory().begin() as session:
            revision_lock(session)
            if find_account(session,username):raise AppError('USERNAME_EXISTS','账号已存在。',409)
            put(session,'platformUsers',account['id'],account);write_audit(session,ctx,'auth.account_create','account',account['id']);bump(session)
        return {'item':public_account(account)}
    @router.patch('/accounts/{account_id}')
    def update_account(account_id:str,body:dict,request:Request,ctx=Depends(context)):
        if not getattr(request.state,'identity',None):raise AppError('AUTH_REQUIRED','请先登录。',401)
        require(ctx[0],'admin');enabled=body.get('enabled');role=body.get('role')
        if not isinstance(enabled,bool) or not isinstance(role,str) or role not in {'admin','manager','user'}:raise AppError('INVALID_ACCOUNT','角色或状态无效。')
        with session_factory().begin() as session:
            revision_lock(session);row=record(session,'platformUsers',safe_id(account_id))
            if not row:raise AppError('NOT_FOUND','账号不存在。',404)
            if row.data['id']==ctx[1] and (not enabled or role!='admin'):raise AppError('SELF_LOCKOUT','不能停用或降级当前管理员。',409)
            if not enabled:
                for token in session.scalars(select(Record).where(Record.collection=='platformSessions',Record.data['userId'].astext==account_id)):session.delete(token)
            row.data={**row.data,'enabled':enabled,'role':role,'updatedAt':now()};write_audit(session,ctx,'auth.account_edit','account',account_id);bump(session);value=public_account(row.data)
        return {'item':value}
    @router.post('/accounts/{account_id}/password')
    def change_password(account_id:str,body:dict,request:Request,ctx=Depends(context)):
        identity=getattr(request.state,'identity',None)
        if not identity:raise AppError('AUTH_REQUIRED','请先登录。',401)
        account_id=safe_id(account_id)
        if ctx[1]!=account_id:require(ctx[0],'admin')
        new_password=body.get('newPassword')
        if not isinstance(new_password,str) or not 12<=len(new_password)<=128:raise AppError('INVALID_PASSWORD','新密码须为 12–128 个字符。')
        with session_factory().begin() as session:
            revision_lock(session);row=record(session,'platformUsers',account_id)
            if not row:raise AppError('NOT_FOUND','账号不存在。',404)
            if ctx[1]==account_id:
                attempt_key=('password',account_id);reserve_attempt(attempt_key)
                old=body.get('oldPassword')
                if not isinstance(old,str) or len(old)>128:raise AppError('INVALID_PASSWORD','当前密码不正确。',400)
                salt=base64.b64decode(row.data['salt']);expected=base64.b64decode(row.data['passwordHash'])
                if not hmac.compare_digest(password_hash(old,salt),expected):raise AppError('INVALID_PASSWORD','当前密码不正确。',400)
                clear_attempts(attempt_key)
            salt=secrets.token_bytes(16);hashed=password_hash(new_password,salt)
            row.data={**row.data,'salt':base64.b64encode(salt).decode(),'passwordHash':base64.b64encode(hashed).decode(),'updatedAt':now()}
            for token in session.scalars(select(Record).where(Record.collection=='platformSessions',Record.data['userId'].astext==account_id)):session.delete(token)
            write_audit(session,ctx,'auth.password','account',account_id);bump(session)
        return {'ok':True,'relogin':ctx[1]==account_id}
    return router
