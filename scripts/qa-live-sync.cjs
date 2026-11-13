const assert = require('node:assert/strict');
const path = require('node:path');
const { diffState, applyChanges, mergeDocumentDetail, stateEnvelopeStatus, setStateInstance, requestHeaders } = require(path.join(process.argv[2] || '/tmp/langchina-live-check', 'live-api.js'));

const state = {
  version: 1,
  role: 'admin',
  knowledgeBases: [],
  documents: [],
  assistants: [],
  conversations: [],
  models: [{ capability: 'llm', model: 'old', status: 'unconfigured' }],
  catalog: [],
  provider: { name: '旧连接', region: '北京', endpoint: 'https://dashscope.aliyuncs.com' },
  logs: [{ id: 'log-server' }],
};
const checks = [];
function check(name, fn) {
  fn();
  checks.push(name);
  console.log(`PASS ${name}`);
}
check('恢复副本的较低修订号会触发替换而非永久忽略', () => {
  assert.equal(stateEnvelopeStatus({revision:100,instance:'original'},{revision:20,instance:'restored'}),'replace');
});
check('同一资料副本的过期响应仍会被忽略', () => {
  assert.equal(stateEnvelopeStatus({revision:100,instance:'original'},{revision:20,instance:'original'}),'stale');
});
check('写入请求携带当前资料副本标识，恢复后更新', () => {
  global.sessionStorage={getItem:()=> 'test-session'};
  setStateInstance('first');assert.equal(requestHeaders('admin')['X-State-Instance'],'first');
  setStateInstance('restored');assert.equal(requestHeaders('admin')['X-State-Instance'],'restored');
  setStateInstance();assert.equal(requestHeaders('admin')['X-State-Instance'],undefined);
  delete global.sessionStorage;
});
check('文档摘要复用同一版本详情并保留最新进度', () => {
  const summary={id:'doc-1',updatedAt:'v1',detailsOmitted:true,chunks:2,progress:80,content:'',segments:[]};
  const full={...summary,detailsOmitted:false,progress:50,content:'真实原文',segments:[{id:'chunk-1'}],metadataExtraction:{facts:[{value:'依据'}]}};
  const merged=mergeDocumentDetail(summary,full);
  assert.equal(merged.content,'真实原文');assert.equal(merged.progress,80);assert.equal(merged.detailsOmitted,false);assert.equal(merged.segments.length,1);
});
check('版本改变后不将旧分块混入新摘要', () => {
  const summary={id:'doc-1',updatedAt:'v2',detailsOmitted:true,content:'',segments:[]};
  assert.equal(mergeDocumentDetail(summary,{...summary,updatedAt:'v1',content:'旧分块'}),summary);
});
check('完整文档响应不会被旧缓存覆盖', () => {
  const detail={id:'doc-1',updatedAt:'v2',content:'最新原文',segments:[]};
  assert.equal(mergeDocumentDetail(detail,{...detail,content:'旧原文'}),detail);
});
check('摘要列表启停文档不会把未加载的空分块提交为删除', () => {
  const doc={id:'doc-1',enabled:true,detailsOmitted:true,chunks:8,segments:[],content:'',metadataExtraction:{facts:[]}};
  const change=diffState({...state,documents:[doc]},{...state,documents:[{...doc,enabled:false}]}).documents.upsert[0];
  assert.equal(change.enabled,false);
  for(const key of ['segments','content','metadataExtraction','detailsOmitted']) assert(!(key in change));
});
check('角色与服务端日志不会提交到状态修改接口', () => {
  assert.deepEqual(diffState(state, { ...state, role: 'user', logs: [] }), {});
});
const added = { ...state, knowledgeBases: [{ id: 'kb1', name: '测试' }] };
check('只提交变化的实体，不重新灌入其他数据', () => {
  assert.deepEqual(diffState(state, added), {
    knowledgeBases: { upsert: [{ id: 'kb1', name: '测试' }], delete: [] },
  });
});
check('重放本地修改保留服务端任务新增的文档与日志', () => {
  const newer = { ...state, documents: [{ id: 'doc-from-worker' }], logs: [{ id: 'new-log' }] };
  const result = applyChanges(newer, diffState(state, added));
  assert.deepEqual(result.logs, newer.logs);
  assert.deepEqual(result.documents, newer.documents);
  assert.equal(result.knowledgeBases[0].id, 'kb1');
});
check('模型默认绑定按能力替换，不产生重复绑定', () => {
  const changed = { ...state, models: [{ ...state.models[0], model: 'new' }] };
  const result = applyChanges(state, diffState(state, changed));
  assert.equal(result.models.length, 1);
  assert.equal(result.models[0].model, 'new');
});
check('删除修改只移除指定实体', () => {
  const before = { ...state, knowledgeBases: [{ id: 'a' }, { id: 'b' }] };
  const delta = diffState(before, { ...before, knowledgeBases: [{ id: 'b' }] });
  assert.deepEqual(delta.knowledgeBases.delete, ['a']);
  assert.deepEqual(applyChanges(before, delta).knowledgeBases, [{ id: 'b' }]);
});
check('服务连接白名单不会将凭证混入普通状态修改', () => {
  const after = { ...state, provider: { ...state.provider, name: '新连接', apiKey: 'must-never-save' } };
  const delta = diffState(state, after);
  assert.equal(JSON.stringify(delta).includes('must-never-save'), false);
  assert.deepEqual(Object.keys(delta.provider.upsert[0]).sort(), ['endpoint', 'name', 'region']);
});
check('多项待保存修改按顺序重放且保留新的服务端状态', () => {
  const second = { ...added, assistants: [{ id: 'assistant1' }] };
  const server = { ...state, logs: [{ id: 'new-log' }] };
  const rebased = applyChanges(applyChanges(server, diffState(state, added)), diffState(added, second));
  assert.equal(rebased.assistants[0].id, 'assistant1');
  assert.equal(rebased.knowledgeBases[0].id, 'kb1');
  assert.equal(rebased.logs[0].id, 'new-log');
});
check('重放修改不改写输入状态', () => {
  const encoded = JSON.stringify(state);
  applyChanges(state, diffState(state, added));
  assert.equal(JSON.stringify(state), encoded);
});
console.log(JSON.stringify({ passed: checks.length, failed: 0, checks }, null, 2));
