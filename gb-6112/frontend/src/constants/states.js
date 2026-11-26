export const States = Object.freeze({
  queued: "等待执行", done: "已完成", failed: "执行失败",
  archived: "已归档", cleared: "已清理",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败", review: "复核签署",
  archive: "归档交接单", archive_recover: "恢复归档", clear: "清理试写结果",
});
