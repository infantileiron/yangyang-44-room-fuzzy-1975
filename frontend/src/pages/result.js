import { download, get, post } from '../api/client.js';
import { bars, status, table } from '../components/common/data.js';
import { intro } from '../components/common/frame.js';
import { fieldControl, formError } from '../components/common/forms.js';
import { Decision, Role, RunStatus } from '../constants/enums.js';
import { bindDownloads, bindForm, notify } from '../hooks/actions.js';
import { formValues } from '../types/form.js';
import { esc, formatMetric, timeLabel } from '../utils/html.js';
import { runTable } from './analysis.js';

const auditionColumns = [
  {key:'ticket',label:'试音编号'}, {key:'room',label:'布置编号'},
  {label:'旧归属',html:row => esc(row.baseline ?? '—')}, {label:'新归属',html:row => esc(row.candidate ?? '—')},
  {label:'新旧中心距（s）',html:row => esc(row.distance ?? '—')},
];

function pairTable(result) {
  const rows = [
    ...result.pairs.map(pair => ({
      baseline:pair.baseline, candidate:pair.candidate, distance:pair.distance,
      basis:`共同试音 ${pair.common} 条 · 沿用 ${pair.kept} 条`,
      note:pair.kept === pair.common ? '中心接近，共同试音归属一致' : '中心接近，部分共同试音归属对不上',
    })),
    ...result.new_clusters.map(cluster => ({
      baseline:'—', candidate:cluster.label, distance:'—', basis:'—',
      note:`新混响簇（中心 ${cluster.low}s / ${cluster.mid}s），无旧号承接`,
    })),
    ...result.retired_clusters.map(cluster => ({
      baseline:cluster.label, candidate:'—', distance:'—', basis:'—',
      note:`旧簇在对照中消失（原中心 ${cluster.low}s / ${cluster.mid}s），未被承接`,
    })),
  ];
  return table([
    {key:'baseline',label:'基准簇'}, {key:'candidate',label:'对照簇'},
    {key:'distance',label:'中心距（s）'}, {key:'basis',label:'承接依据'}, {key:'note',label:'说明'},
  ], rows, '两侧都没有混响簇。');
}

function comparisonBlock(item) {
  const result = item.result;
  const summary = result.summary;
  const changed = result.auditions.filter(row => row.status === '变更');
  const pending = result.auditions.filter(row => row.status === '待核');
  const verdict = summary.changed
    ? `归属确实变化的布置：${summary.changed_rooms.join('、')}（${summary.changed} 次试音）`
    : '没有试音的混响归属确实变化';
  const metrics = [['变更试音',summary.changed,'次'],['待核试音',summary.pending,'次'],['沿用试音',summary.kept,'次'],
                   ['新增试音',summary.added,'次'],['移除试音',summary.removed,'次']]
    .map(([label,value,unit]) => `<div><dt>${label}</dt><dd>${value}<small> ${unit}</small></dd></div>`).join('');
  return `<article class="comparison">
    <header class="comparison-head">对照 #${item.id} · 基准《${esc(result.baseline.title)}》#${result.baseline.id}（${esc(result.baseline.reviewer)} ${timeLabel(result.baseline.reviewed_at)} 签核）→ 对照《${esc(result.candidate.title)}》#${result.candidate.id} · 阈值 ${esc(result.tolerance)}s · ${esc(item.created_by)} 提交于 ${timeLabel(item.created_at)}</header>
    <p class="comparison-verdict">${esc(verdict)}${summary.pending ? ` · 另有 ${summary.pending} 次试音标待核，不计入归属变化` : ''}</p>
    <dl class="metrics">${metrics}</dl>
    <h3>承接依据</h3>
    ${pairTable(result)}
    <h3>确实变了的试音</h3>
    ${table(auditionColumns, changed, '没有试音的混响归属确实变化。')}
    <h3>待核试音</h3>
    <p class="aside-note">中心接近但共同试音的归属对不上，待核不计入归属变化。</p>
    ${table(auditionColumns, pending, '无待核试音。')}
    <details class="comparison-all"><summary>全部试音归属对照（${result.auditions.length} 条）</summary>
    ${table([...auditionColumns, {key:'status',label:'状态'}], result.auditions, '两侧没有可对照的试音。')}</details>
  </article>`;
}

async function comparisonSection(spec, user, run) {
  const list = await get(`/comparisons?candidate=${run.id}`);
  const comparisons = await Promise.all(list.map(item => get(`/comparisons/${item.id}`)));
  let form = '';
  if (user.role === Role.ANALYST) {
    const runs = await get('/runs');
    const baselines = runs.filter(item => item.id !== run.id && item.review?.decision === Decision.APPROVED);
    form = baselines.length
      ? `<form id="compare-form" data-id="${run.id}" class="panel-body"><div class="form-grid"><label class="field"><span>基准（已签核结果）</span><select name="baseline_run_id" required><option value="">请选择</option>${baselines.map(item => `<option value="${item.id}">#${item.id} ${esc(item.title)} · ${timeLabel(item.review.at)} 签核</option>`).join('')}</select></label>${spec.comparison_parameters.map(field => fieldControl(field, true)).join('')}</div>${formError}<button class="primary">生成对照</button> <small class="aside-note">同一基准、观测和参数重复提交不会生成新对照。</small></form>`
      : '<p class="panel-body">暂无已签核结果可作基准。请先由复核负责人签核一份分析。</p>';
  }
  return `<section class="panel"><div class="panel-heading"><h2>换装对照</h2></div>
    ${form}
    ${comparisons.length ? comparisons.map(comparisonBlock).join('') : '<div class="empty">还没有对照。补录换装后的试音记录并重算后，选一份已签核结果作基准生成对照。</div>'}
  </section>`;
}

export async function resultPage(spec, user, route) {
  if (!route.id) {
    const runs = await get('/runs');
    return `${intro('结果与复核', spec.decision_prompt)}<section class="panel"><div class="panel-heading"><h2>选择分析结果</h2></div>${runTable(runs, '暂无结果。分析员可先在「分析任务」提交计算。')}</section>`;
  }
  const id = Number(route.id);
  if (!Number.isSafeInteger(id) || id <= 0) throw new Error('分析编号无效');
  const run = await get(`/runs/${id}`);
  const head = intro(run.title, `${run.sample_size} 条观测 · 快照末号 ${run.snapshot_id} · ${timeLabel(run.created_at)}`, status(run));
  if (run.status === RunStatus.QUEUED) return `${head}<div class="empty" data-pending="${id}">分析任务已入队，正在等待计算。此页会自动刷新。</div>`;
  if (run.status === RunStatus.FAILED) return `${head}<div class="empty" role="alert">${esc(run.error)} <a href="#analysis">调整条件并重新计算</a></div>`;
  const data = run.result;
  let review = '<p>等待复核负责人签核。签核后也可由只读角色查看。</p>';
  if (run.review) review = `<p><b>${run.review.decision === Decision.APPROVED ? '签核通过' : '退回补充'}</b> ${esc(run.review.note)}</p><small>${esc(run.review.reviewer)} · ${timeLabel(run.review.at)}</small>`;
  if (!run.review && user.role === Role.SUPERVISOR) review = `<form id="review-form" data-id="${id}"><label class="field"><span>复核结论</span><select name="decision"><option value="approved">签核通过</option><option value="returned">退回补充</option></select></label><label class="field"><span>复核依据</span><textarea name="note" required minlength="2" maxlength="1000" placeholder="${esc(spec.decision_prompt)}"></textarea></label>${formError}<button class="primary">保存复核</button></form>`;
  const parameters = spec.parameters.map(field => `<li><span>${esc(field.label)}</span><b>${esc(run.parameters[field.key])}</b></li>`).join('');
  const comparison = user.role === Role.VIEWER ? '' : await comparisonSection(spec, user, run);
  return `${head}<div class="split result-summary"><section class="conclusion"><h2>${esc(data.headline)}</h2><p>${esc(data.summary)}</p>${data.metrics.length ? `<dl class="metrics">${data.metrics.map(metric => `<div><dt>${esc(metric.label)}</dt><dd>${esc(formatMetric(metric.value))}${metric.unit ? `<small> ${esc(metric.unit)}</small>` : ''}</dd></div>`).join('')}</dl>` : ''}</section><aside class="method"><h2>本次条件</h2><ul class="parameters">${parameters}</ul>${bars(data.chart)}</aside></div>
    <section class="panel"><div class="panel-heading"><h2>计算明细</h2><button data-download="/runs/${id}/export">下载 CSV</button></div>${table(data.columns,data.rows,'当前参数下无明细，计算结论保留。')}</section>
    ${comparison}
    <section class="panel"><div class="panel-heading"><h2>业务签核</h2>${run.review?.decision === Decision.APPROVED ? '<a href="#reports">查看分发 →</a>' : ''}</div><div class="panel-body">${review}</div></section>`;
}

export function wireResult(root, refresh, spec) {
  bindDownloads(root, download);
  bindForm(root.querySelector('#review-form'), async form => {
    await post(`/runs/${form.dataset.id}/review`,Object.fromEntries(new FormData(form)));
    notify('复核记录已保存');
    refresh();
  });
  bindForm(root.querySelector('#compare-form'), async form => {
    const reply = await post('/comparisons', {
      baseline_run_id: Number(form.elements.baseline_run_id.value),
      candidate_run_id: Number(form.dataset.id),
      parameters: formValues(form, spec.comparison_parameters),
    });
    notify(reply.created ? '对照已生成' : '相同基准、观测和参数的对照已存在，直接沿用');
    refresh();
  });
  const pending = root.querySelector('[data-pending]');
  if (pending) setTimeout(() => {
    if (pending.isConnected && location.hash === `#result/${pending.dataset.pending}`) refresh();
  }, 800);
}
