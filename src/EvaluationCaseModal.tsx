import { useEffect, useState } from "react";
import { App, Input, Modal, Radio } from "antd";
import { api } from "./live-api";
import { useStore } from "./store-context";
import type { RunLog } from "./types";

type Label = "good" | "bad";
export function EvaluationCaseModal({ log, open, onClose }: { log: RunLog | undefined; open: boolean; onClose: () => void }) {
  const { state } = useStore();
  const { message } = App.useApp();
  const [label, setLabel] = useState<Label>("good");
  const [note, setNote] = useState("");
  const [expectedAnswer, setExpectedAnswer] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (open) { setLabel("good"); setNote(""); setExpectedAnswer(""); } }, [open, log?.id]);
  const save = async () => {
    if (!log || saving) return;
    setSaving(true);
    try {
      await api("/evaluation/cases", state.role, { method: "POST", body: JSON.stringify({ runId: log.id, label, note, expectedAnswer }) });
      message.success("已收录到线上案例评测集"); onClose();
    } catch (error) { message.error(error instanceof Error ? error.message : "收录失败，请重试。"); }
    finally { setSaving(false); }
  };
  return <Modal title="收录线上案例" open={open} onCancel={onClose} onOk={save} okText="收录案例" confirmLoading={saving} destroyOnHidden>
    <p className="muted">保存当次问题、可用回答与引用快照；分类由你手动选择。</p>
    <p><strong>{log?.query}</strong></p>
    <Radio.Group aria-label="案例分类" value={label} onChange={e => setLabel(e.target.value)} options={[{ label: "Good Case", value: "good" }, { label: "Bad Case", value: "bad" }]} />
    <p className="muted" style={{ marginTop: 16 }}>备注（选填）</p>
    <Input.TextArea value={note} onChange={e => setNote(e.target.value)} maxLength={2000} showCount rows={3} placeholder="例如：回答遗漏了有效期" />
    <p className="muted" style={{marginTop:20}}>参考答案（选填，用于文本匹配和模型辅助评判）</p>
    <Input.TextArea value={expectedAnswer} onChange={e=>setExpectedAnswer(e.target.value)} maxLength={10000} rows={3} placeholder="留空时评测只展示输出，不计算匹配结果"/>
  </Modal>;
}
