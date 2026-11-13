import data from "../backend/app/tongyi_models.json";
import type { Capability, RegisteredModel } from "./types";

export interface TongyiModelInfo {
  model: string;
  capabilities: string[];
  group: string;
  recommendedFor?: string[];
  disabledReason?: string;
  legacy?: boolean;
  snapshot?: boolean;
  thinking?: string;
  streamOnly?: boolean;
}
export const TONGYI_MODELS: TongyiModelInfo[] = data.models;
export const CATALOG_VERSION = data.version;
const info = new Map(TONGYI_MODELS.map((m) => [m.model, m]));
export const modelInfo = (model: string) => info.get(model);
export function mergeBuiltinCatalog(existing: RegisteredModel[]): RegisteredModel[] {
  const result = [...existing];
  const known = new Set(existing.map((m) => `${m.capability}:${m.model}`));
  for (const item of TONGYI_MODELS) {
    for (const capability of item.capabilities as Capability[]) {
      const key = `${capability}:${item.model}`;
      if (known.has(key)) continue;
      result.push({
        id: `tongyi-${capability}-${item.model.replace(/[^A-Za-z0-9_-]/g, "-")}`,
        model: item.model, capability, enabled: !item.disabledReason,
      });
      known.add(key);
    }
  }
  return result;
}

export function modelOptions(catalog: RegisteredModel[], capability: Capability) {
  const groups = new Map<string, {value: string; label: string}[]>();
  const models = catalog.filter((m) => m.capability === capability && m.enabled && !modelInfo(m.model)?.disabledReason);
  for (const m of models) {
    const item = modelInfo(m.model);
    const recommended = item?.recommendedFor?.includes(capability);
    const group = recommended ? "推荐模型" : item?.group || "自定义模型";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group)!.push({value: m.model, label: `${m.model}${item?.legacy ? "（历史版本）" : ""}`});
  }
  return [...groups].sort(([a], [b]) => Number(b === "推荐模型") - Number(a === "推荐模型"))
    .map(([label, options]) => ({label, options}));
}
