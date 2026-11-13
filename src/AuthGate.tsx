import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Alert, Button, Card, Form, Input } from "antd";
type User={id:string;username:string;role:"admin"|"manager"|"user"};
type AuthState={enabled:boolean;authenticated:boolean;user:User|null};
const defaultAuth:AuthState={enabled:false,authenticated:false,user:null};
const AuthContext=createContext<AuthState>(defaultAuth);
export function useAuth(){return useContext(AuthContext)}
export function AuthGate({children}:{children:ReactNode}){
 const [auth,setAuth]=useState<AuthState|null>(null);const [error,setError]=useState("");const [busy,setBusy]=useState(false);
 useEffect(()=>{let live=true;fetch('/api/auth/status').then(async r=>{if(!r.ok)throw new Error();return r.json() as Promise<AuthState>}).then(v=>{if(live){if(v.enabled&&v.user){localStorage.setItem('knowledge-hub-data-mode','live');localStorage.setItem('knowledge-hub-live-role',v.user.role)}setAuth(v)}}).catch(()=>{if(live)setAuth(defaultAuth)});return()=>{live=false}},[]);
 const login=async(values:{username:string;password:string})=>{setBusy(true);setError("");try{const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});const data=await r.json();if(!r.ok)throw new Error(data?.error?.message||'登录失败');localStorage.setItem('knowledge-hub-data-mode','live');localStorage.setItem('knowledge-hub-live-role',data.user.role);location.reload()}catch(e){setError(e instanceof Error?e.message:'登录失败')}finally{setBusy(false)}};
 if(!auth)return <div style={{padding:32}}>正在连接本机服务…</div>;
 if(auth.enabled&&!auth.authenticated)return <div style={{minHeight:'100vh',display:'grid',placeItems:'center',background:'#f5f7fb'}}><Card title="登录知序工作台" style={{width:400,maxWidth:'90vw'}}><p>请输入管理员创建的本机账号。</p>{error&&<Alert type="error" title={error} style={{marginBottom:12}}/>}<Form layout="vertical" onFinish={login}><Form.Item name="username" label="账号" rules={[{required:true}]}><Input autoComplete="username"/></Form.Item><Form.Item name="password" label="密码" rules={[{required:true}]}><Input.Password autoComplete="current-password"/></Form.Item><Button type="primary" htmlType="submit" loading={busy} block>登录</Button></Form></Card></div>;
 return <AuthContext.Provider value={auth}>{children}</AuthContext.Provider>
}
