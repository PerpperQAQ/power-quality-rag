import methods from "../backend/app/chunking_methods.json";
import type { ChunkMethod } from "./types";

export const CHUNKING_METHODS = methods.map((m) => ({...m, value: m.value as ChunkMethod}));
export const CHUNKING_LABELS = Object.fromEntries(CHUNKING_METHODS.map((m) => [m.value, m.label])) as Record<ChunkMethod,string>;

export function chunkingExample(method: ChunkMethod): string {
  if (method === "table") return "| 产品 | 版本 | 负责人 |\n| --- | --- | --- |\n| 知识中台 | v1.0 | 王敏 |\n| 订单系统 | v2.1 | 李明 |";
  if (method === "qa") return "问：差旅报销需要什么材料？\n答：需要发票、行程单和审批记录。\n\n问：报销由谁审批？\n答：部门负责人先审批，财务随后审核。";
  if (method === "laws") return "第一章 总则\n第一条 本制度适用于正式员工。\n员工提交的资料应当真实完整。\n第二条 部门负责人负责审批。\n财务负责票据审核。";
  if (method === "book") return "第一章 入职\n入职当天领取办公设备并确认账号。\n第一节 设备领取\n由行政登记设备编号。\n第二章 日常协作\n每周同步项目进展。";
  if (method === "paper") return "摘要\n本文讨论企业知识检索的资料组织方式。\n1 Introduction\n检索效果受到资料结构和分块边界影响。\n2 Methods\n按章节保留来源信息。\n3 Results\n记录片段数量，保留原文供复核。\n参考文献\n本段为示例文献内容。";
  if (method === "page") return "第一页：项目背景和适用范围。\n===PAGE===\n第二页：操作流程和注意事项。";
  if (method === "delimiter") return "入职流程：领取电脑，开通邮箱。\n报销流程：提交发票，由部门负责人审批。\n离职流程：完成工作交接与设备归还。";
  if (method === "one") return "企业知识库演示说明：使用者可以查询资料，知识库管理人员负责维护文档，管理员配置模型服务。";
  return "# 员工手册\n\n## 入职流程\n入职当天由行政确认材料，并办理办公设备领取手续。设备编号和领用日期需要登记。\n\n## 差旅报销\n出差前应提交申请，说明出行时间、地点和预算。报销时提交发票、行程单和审批记录。\n\n## 常见问题\n问：资料不完整时如何处理？\n答：补齐所需资料后再提交审核。";
}
