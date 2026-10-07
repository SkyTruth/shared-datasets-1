const $ = id => document.getElementById(id);
let report;
const text = (tag, value) => { const node = document.createElement(tag); node.textContent = value; return node; };
const last = asset => [asset.last_read, asset.last_interest].filter(Boolean).sort().at(-1) || '';
function render() {
  const days = $('window').value;
  const search = $('search').value.toLowerCase();
  const application = $('application').value;
  const rows = report.assets.filter(asset => `${asset.title} ${asset.slug}`.toLowerCase().includes(search)
    && (!$('candidate').value || asset.state === $('candidate').value)
    && (!application || asset.windows[days].some(group => group.application === application)));
  rows.sort((a, b) => $('sort').value === 'name' ? a.title.localeCompare(b.title) : last(a).localeCompare(last(b)));
  $('rows').replaceChildren();
  for (const asset of rows) {
    const row = document.createElement('tr');
    const name = text('td', asset.title); name.append(text('small', asset.slug), text('small', `Observation from ${asset.observed_from || 'not verified'}`)); row.append(name);
    row.append(text('td', last(asset) || 'None observed'), text('td', asset.last_catalog || 'None observed'));
    const state = text('td', asset.state.replaceAll('_', ' ')); state.append(text('small', asset.reason)); row.append(state);
    const counts = document.createElement('td');
    for (const group of asset.windows[days]) counts.append(text('div', `${group.source} · ${group.activity} · ${group.application} (${group.confidence}): ${group.requests} requests, ${group.active_days} active days${group.bytes == null ? '' : `, ${group.bytes} reported bytes`}`));
    if (!counts.childNodes.length) counts.textContent = 'No activity recorded in this window';
    row.append(counts); $('rows').append(row);
  }
  if (!rows.length) { const cell = text('td', 'No datasets match these filters.'); cell.colSpan = 5; const row = document.createElement('tr'); row.append(cell); $('rows').append(row); }
}
try {
  const response = await fetch('/api/usage', {cache: 'no-store'});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Usage report unavailable');
  report = result;
  const generated = Date.parse(report.generated_at);
  const stale = report.stale || !Number.isFinite(generated) || generated > Date.now() + 5 * 60 * 1000 || Date.now() - generated > 48 * 3600 * 1000;
  if (stale) { report.assets.forEach(asset => {asset.state = 'stale'; asset.reason = 'Report is more than 48 hours old';}); $('status').classList.add('error'); }
  $('status').textContent = `${stale ? 'Stale report — activity is unknown. ' : ''}Generated ${report.generated_at}. Observation through ${report.observed_through}. Counts from different sources overlap.`;
  $('disclaimer').textContent = report.disclaimer;
  $('cost').textContent = `Monthly cost target: $${report.cost.target_monthly_usd}. Estimate: ${report.cost.estimated_monthly_usd == null ? 'not verified' : '$' + report.cost.estimated_monthly_usd} (${report.cost.state}).`;
  const apps = new Set(report.assets.flatMap(asset => Object.values(asset.windows).flatMap(groups => groups.map(group => group.application))));
  for (const app of [...apps].sort()) { const option = text('option', app); option.value = app; $('application').append(option); }
  for (const source of report.source_health) $('source-health').append(text('li', `${source.source} · ${source.window_days} elapsed days: ${source.healthy_days} healthy, ${source.gap_days} gaps, ${source.pending_days} pending, ${source.unknown_days} unknown`));
  for (const entry of report.coverage) $('coverage').append(text('li', `${entry.day} · ${entry.source}: ${entry.state} — ${entry.reason}`));
  $('filters').addEventListener('submit', event => event.preventDefault());
  $('filters').addEventListener('input', render); render();
} catch (error) { $('status').classList.add('error'); $('status').textContent = `${error.message}. Missing information never means zero use.`; }
