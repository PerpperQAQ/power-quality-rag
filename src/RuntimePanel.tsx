import { useEffect, useState } from "react";
import { Alert, App, Button, Descriptions, Modal, Space, Spin, Tag } from "antd";
import { api } from "./live-api";

interface RuntimeInfo {
  managed: boolean;
  status?: string;
  heartbeatAt?: string;
  storage?: string;
  dataDir?: string;
  backupDirectory?: string;
  backupStatus?: string;
  backupError?: string | null;
  lastBackup?: { name: string; createdAt: string; sourceFiles: number; bytes: number };
  lastRecoveryCheck?: { createdAt: string; status: string; sourceFilesVerified: number };
}
const dateText = (value?: string) => value ? new Date(value).toLocaleString("zh-CN") : "尚无记录";

export function RuntimePanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [info, setInfo] = useState<RuntimeInfo | null>(null);
  const [error, setError] = useState("");
  const [requesting, setRequesting] = useState(false);
  const { message } = App.useApp();
  useEffect(() => {
    if (!open) return;
    let canceled = false;
    const load = async () => {
      try {
        const result = await api<RuntimeInfo>("/runtime", "admin");
        if (!canceled) { setInfo(result); setError(""); }
      } catch (failure) {
        if (!canceled) setError(failure instanceof Error ? failure.message : "无法读取运行状态");
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => { canceled = true; window.clearInterval(timer); };
  }, [open]);
  const backup = async () => {
    setRequesting(true);
    try {
      await api("/runtime/backup", "admin", { method: "POST" });
      message.success("已申请备份，空闲时执行；结果会在此更新。");
    } catch (failure) {
      message.error(failure instanceof Error ? failure.message : "备份申请未成功");
    } finally { setRequesting(false); }
  };
  const healthy = info?.status === "running" && !!info.heartbeatAt && Date.now() - Date.parse(info.heartbeatAt) < 30000;
  return (
    <Modal title="本机运行与备份" open={open} onCancel={onClose} footer={<Button onClick={onClose}>关闭</Button>} width={650}>
      {error ? <Alert type="error" showIcon title={error} /> : !info ? <Spin /> : !info.managed ? (
        <Alert type="info" showIcon title="当前由开发命令启动" description="使用桌面“启动知序”入口后，可查看自动恢复与备份状态。" />
      ) : (
        <Space orientation="vertical" size={18} style={{ width: "100%" }}>
          <Alert type={healthy ? "success" : "warning"} showIcon title={healthy ? "本机服务运行正常" : "运行服务正在恢复，请稍后查看"} description="关闭浏览器后继续运行；登录 Mac 后自动启动。关机或睡眠期间暂停提供服务。" />
          <Descriptions column={1} size="small">
            <Descriptions.Item label="运行数据">内置盘</Descriptions.Item>
            <Descriptions.Item label="数据目录"><span style={{ overflowWrap: "anywhere" }}>{info.dataDir}</span></Descriptions.Item>
            <Descriptions.Item label="自动备份">每分钟检查；满 24 小时后，有新数据且空闲时执行</Descriptions.Item>
            <Descriptions.Item label="最近成功备份">{dateText(info.lastBackup?.createdAt)}</Descriptions.Item>
            <Descriptions.Item label="备份内容">{info.lastBackup ? `${info.lastBackup.sourceFiles} 份原文件、数据库、任务检查点与加密主密钥` : "尚无成功备份"}</Descriptions.Item>
            <Descriptions.Item label="备份目录"><span style={{ overflowWrap: "anywhere" }}>{info.backupDirectory}</span></Descriptions.Item>
            <Descriptions.Item label="恢复校验">{info.lastRecoveryCheck ? `${dateText(info.lastRecoveryCheck.createdAt)} · ${info.lastRecoveryCheck.status === "verified" ? "通过" : "待核对"} · ${info.lastRecoveryCheck.sourceFilesVerified} 份原文件` : "尚未执行"}</Descriptions.Item>
          </Descriptions>
          {info.backupStatus === "running" && <Tag color="processing">备份正在执行</Tag>}
          {info.backupError && <Alert type="warning" showIcon title={info.backupError} />}
          <Button onClick={() => void backup()} loading={requesting} disabled={info.backupStatus === "running"}>立即备份</Button>
          <p className="muted">桌面“知序知识库”文件夹提供启动、停止、备份和恢复入口。恢复会先校验新副本，再切换资料；原数据保留。</p>
        </Space>
      )}
    </Modal>
  );
}
