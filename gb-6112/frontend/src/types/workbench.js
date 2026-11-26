/**
 * @typedef {{id:number, code:string, title:string, description:string, payload:Object, created_by:string}} Case
 * @typedef {{id:number, case_id:number, case_title:string, status:"queued"|"done"|"failed"|"archived"|"cleared", parameter:number, actor:string, created_at:number, finished_at:number|null, result:Object|null, failure:string|null, digest:string|null}} Run
 * @typedef {{run_id:number, actor:string, note:string, created_at:number}} Review
 * @typedef {{id:number, run_id:number, path:string, digest:string, stage:"staged"|"verified", actor:string, created_at:number, verified_at:number|null}} Archive
 */

/** Reject a malformed configuration before rendering its navigation. */
export function validateConfig(config) {
  if (!config || !Array.isArray(config.modules) || config.modules.length !== 4 ||
      !Number.isInteger(config.default_parameter) || !config.title) {
    throw new Error("工作台配置不完整");
  }
  return config;
}
