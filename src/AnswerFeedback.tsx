import { useEffect, useState } from "react";
import { App, Button, Space } from "antd";
import { api } from "./live-api";
import { useStore } from "./store-context";

type Vote = "positive" | "negative" | null;
export function AnswerFeedback({ runId }: { runId: string }) {
  const { state, mode } = useStore();
  const { message } = App.useApp();
  const stored = state.logs.find(log => log.id === runId)?.feedback?.value ?? null;
  const [vote, setVote] = useState<Vote>(stored);
  const [saving, setSaving] = useState(false);
  useEffect(() => setVote(stored), [stored, runId]);
  if (mode !== "live") return null;
  const submit = async (next: Vote) => {
    if (saving) return;
    setSaving(true);
    try {
      const result = await api<{feedback: {value: Vote} | null}>(`/insights/feedback/${encodeURIComponent(runId)}`, state.role, {method:"PUT", body:JSON.stringify({value:vote===next?null:next})});
      setVote(result.feedback?.value ?? null);
      message.success(result.feedback ? "反馈已记录" : "已撤回反馈");
    } catch (error) { message.error(error instanceof Error ? error.message : "反馈保存失败"); }
    finally { setSaving(false); }
  };
  return <Space size={4} aria-label="回答反馈"><Button size="small" type={vote === "positive" ? "primary" : "default"} disabled={saving} aria-pressed={vote === "positive"} onClick={() => void submit("positive")}>有帮助</Button><Button size="small" type={vote === "negative" ? "primary" : "default"} disabled={saving} aria-pressed={vote === "negative"} onClick={() => void submit("negative")}>未解决</Button></Space>;
}
