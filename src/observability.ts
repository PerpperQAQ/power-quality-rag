import type { RunLog } from "./types";

/** Each attempt is counted once; legacy logs without attempts use their total once. */
export interface UsageGroup { key: string; kind: string; model: string; known: number; knownCount: number; unknownCount: number; runs: number }
export function aggregateUsage(logs: RunLog[]): { known: number; knownCount: number; unknownCount: number; groups: UsageGroup[] } {
  const groups = new Map<string, UsageGroup>();
  let known = 0, knownCount = 0, unknownCount = 0;
  const runSets = new Map<string, Set<string>>();
  for (const log of logs) {
    const entries = log.usage?.length ? log.usage : [{ kind: "历史记录（仅总量）", model: "未区分模型", tokens: log.tokens }];
    for (const item of entries) {
      const kind = item.kind || "未标明能力", model = item.model || "未记录模型";
      const key = JSON.stringify([kind, model]);
      const group = groups.get(key) || {key, kind, model, known: 0, knownCount: 0, unknownCount: 0, runs: 0};
      const n = item.tokens;
      if (typeof n === "number" && Number.isSafeInteger(n) && n >= 0) { known += n; knownCount++; group.known += n; group.knownCount++; }
      else { unknownCount++; group.unknownCount++; }
      const ids = runSets.get(key) || new Set<string>(); ids.add(log.id);runSets.set(key,ids);group.runs=ids.size;
      groups.set(key, group);
    }
  }
  return {known,knownCount,unknownCount,groups:[...groups.values()].sort((a,b)=>b.known-a.known || a.kind.localeCompare(b.kind,"zh-CN"))};
}

export interface PriceRule {id:string;model:string;kind:string;currency:"CNY";unit:"million_tokens";inputRate:string;outputRate:string;cachedInputRate?:string|null;effectiveAt:string}
export function estimateCost(logs:RunLog[],prices:PriceRule[]):{total:number;knownCount:number;unknownCount:number} {
 let total=0,knownCount=0,unknownCount=0;
 for(const log of logs){
  if(!log.usage?.length){if(log.tokens !== 0)unknownCount++;continue;}
  for(const item of log.usage){
   const time=Date.parse(log.createdAt);const eligible=prices.filter(p=>p.model===item.model&&p.kind===item.kind&&Date.parse(p.effectiveAt)<=time).sort((a,b)=>Date.parse(b.effectiveAt)-Date.parse(a.effectiveAt));
   const rule=eligible[0];if(!rule){unknownCount++;continue;}
   const input=Number(rule.inputRate),output=Number(rule.outputRate);
   if(!Number.isFinite(input)||!Number.isFinite(output)||input<0||output<0 || (input>0 && (!Number.isSafeInteger(item.inputTokens) || (item.inputTokens as number)<0)) || (output>0 && (!Number.isSafeInteger(item.outputTokens) || (item.outputTokens as number)<0))){unknownCount++;continue;}
   const cached=item.cachedTokens, cacheRate=rule.cachedInputRate==null?null:Number(rule.cachedInputRate);
   if(cacheRate!==null){
    if(!Number.isFinite(cacheRate)||cacheRate<0||!Number.isSafeInteger(cached)||!Number.isSafeInteger(item.inputTokens)||(cached as number)<0||(cached as number)>(item.inputTokens as number)){unknownCount++;continue;}
    total+=(input*((item.inputTokens as number)-(cached as number))+cacheRate*(cached as number)+output*(item.outputTokens||0))/1_000_000;
   }else{
    if(typeof cached==="number"&&cached>0){unknownCount++;continue;}
    total+=(input*(item.inputTokens||0)+output*(item.outputTokens||0))/1_000_000;
   }
   knownCount++;
  }
 }
 return {total,knownCount,unknownCount};
}
