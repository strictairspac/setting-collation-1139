import { api } from "../api/workbench.js";
import { errorText, field } from "../components/common/fields.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { resultTable } from "../components/common/table.js";
import { submit } from "../hooks/submit.js";
import { el } from "../utils/dom.js";
import { cell, download, time } from "../utils/format.js";

function reviewForm(id, saved) {
  const form = el("form", {id: "review-form"},
    field("复核意见", el("textarea", {name: "note", required: true, minlength: 3, maxlength: 500, rows: 3})),
    errorText(), el("button", {type: "submit"}, "签署复核"));
  submit(form, async values => saved(await api.submitReview(id, {note: values.get("note")})));
  return form;
}

function signature(review) {
  return el("div", {"data-testid": "reviewed", class: "signature"},
    el("strong", {}, `已签署 · ${review.actor}`), el("p", {}, review.note), el("time", {}, time(review.created_at)));
}

function exportButton(run, issue) {
  const button = el("button", {type: "button", onclick: async () => {
    button.disabled = true;
    issue.textContent = "";
    try {
      await download(await api.export(run.id), `evidence-${run.id}.json`);
    } catch (error) {
      issue.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }}, run.status === "archived" ? "下载归档交接单" : "导出证据");
  return button;
}

export async function evidencePage(config, user, route) {
  const runs = (await api.runs()).filter(item => ["done", "archived", "cleared"].includes(item.status));
  const run = route.id ? await api.run(route.id) : runs[0];
  const issue = errorText();
  if (!run) return el("div", {class: "page"},
    pageHeading(config.modules[3], null), empty("成功执行一个批次后可以查看、签核和下载证据。"),
    el("a", {href: "#runs"}, config.modules[2]));

  const options = el("select", {"aria-label": "选择已完成的批次", onchange: event => {
    location.hash = `evidence/${event.target.value}`;
  }}, runs.map(item => el("option", {value: item.id, selected: item.id === run.id}, `#${item.id} ${item.case_title}`)));

  if (run.status === "cleared") {
    return el("div", {class: "page evidence"},
      pageHeading(config.modules[3], run.case_title),
      issue, options,
      section("批次记录", el("div", {class: "stack"},
        el("p", {}, "该批次保留期届满时仍未签署，试写结果已清理，仅保留批次记录，无交接单可下载或补签。"),
        el("dl", {class: "metrics"},
          [["批次编号", run.id], ["参数", run.parameter], ["提交时间", time(run.created_at)], ["结束时间", time(run.finished_at)]]
            .map(([label, value]) => el("div", {}, el("dt", {}, label), el("dd", {}, cell(value))))))));
  }

  if (!run.result && run.status !== "archived") {
    return el("div", {class: "page"},
      pageHeading(config.modules[3], run.case_title, null), empty("成功执行一个批次后可以查看、签核和下载证据。"),
      el("a", {href: "#runs"}, config.modules[2]));
  }

  const review = await api.review(run.id);
  const button = exportButton(run, issue);
  const signing = el("div", {});

  if (run.status === "archived") {
    // 库内原件已转走，签署与证据以归档副本为准；原入口仍可下载同一份交接单。
    signing.append(review
      ? signature(review)
      : empty("交接单已归档，签署信息以归档副本为准。"));
    return el("div", {class: "page evidence"},
      pageHeading(config.modules[3], run.case_title, button),
      issue, options,
      section("归档交接单", el("div", {class: "stack"},
        el("p", {}, "该批次保留期届满且已签署，交接单已转归档盘，批次号、校验值与签署保持不变。"),
        review ? signature(review) : null,
        el("details", {}, el("summary", {}, "归档说明"),
          el("p", {}, "下载内容来自归档盘副本，已通过 SHA-256 校验，与库内原件一致。")))));
  }

  signing.append(review ? signature(review) : user.role === "reviewer" ?
    reviewForm(run.id, data => signing.replaceChildren(signature(data))) : empty("待复核员签署。"));
  return el("div", {class: "page evidence"},
    pageHeading(config.modules[3], run.case_title, button),
    issue, options,
    el("section", {class: "feature evidence-summary"}, el("div", {}, el("h2", {}, "批次判定"), el("p", {}, run.result.summary)),
      el("details", {}, el("summary", {}, "证据摘要"), el("code", {}, run.digest))),
    el("dl", {class: "metrics"}, Object.entries(run.result.metrics).map(([label, value]) =>
      el("div", {}, el("dt", {}, label), el("dd", {}, cell(value))))),
    section("业务结果明细", run.result.rows.length ? resultTable(run.result.rows) : empty("此批次无明细。")),
    section("复核签署", signing));
}
