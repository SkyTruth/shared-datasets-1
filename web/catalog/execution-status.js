const states = new Set(["pending", "running", "succeeded", "failed", "cancelled", "unknown"]);

export function executionStatusText(document, now = Date.now()) {
  if (document?.schema_version !== 1 || !String(document.job_name || "").endsWith("/jobs/wdpa-monthly")) {
    throw new Error("Unsupported WDPA execution observation");
  }
  const observed = Date.parse(document.observed_at);
  if (!Number.isFinite(observed)) throw new Error("Invalid execution observation timestamp");
  const describe = (entry) => {
    if (!entry) return "No execution observed";
    if (!states.has(entry.state)) throw new Error("Invalid execution state");
    return `${entry.state} · ${entry.id} · ${entry.completed_at || entry.started_at || entry.created_at || "time unknown"}${entry.reason_code && entry.reason_code !== "UNSPECIFIED" ? ` · ${entry.reason_code}` : ""}`;
  };
  const latest = document.latest_execution;
  const completed = document.latest_completed_execution;
  const parts = [describe(latest)];
  if (completed && completed.id !== latest?.id) parts.push(`Last completed: ${describe(completed)}`);
  parts.push(now - observed > 15 * 60_000 ? `Stale observation · ${document.observed_at}` : `Observed ${document.observed_at}`);
  return parts.join(". ");
}
