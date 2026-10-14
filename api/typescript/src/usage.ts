/** Opt-in daily observations. Transport belongs to the application backend. */
export type UsageActivity = 'use' | 'interest';
export type UsageReceipt = {schema_version: 1; asset_slug: string; activity: UsageActivity; observed_at: string};
export type UsageReporter = {recordUsage(assetSlug: string, activity?: UsageActivity): void; flush(): Promise<boolean>};
export type UsageReporterOptions = {
  producer: string;
  writeReceipt: (name: string, receipt: UsageReceipt, signal: AbortSignal) => Promise<void>;
  now?: () => Date;
  diagnostic?: (code: string) => void;
};
const slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export function createUsageReporter(options: UsageReporterOptions): UsageReporter {
  if (typeof options.producer !== 'string' || options.producer.length > 128 || !slugPattern.test(options.producer)) throw new Error('Usage producer requires a registered identifier');
  const now = options.now ?? (() => new Date());
  const diagnostic = options.diagnostic ?? ((code: string) => console.warn(`Dataset usage reporting failed: ${code}`));
  const pending = new Map<string, {name: string; receipt: UsageReceipt}>();
  const sent = new Set<string>();
  const blocked = new Map<string, number>();
  let sentDay = '';
  return {
    recordUsage(assetSlug, activity = 'use') {
      if (typeof assetSlug !== 'string' || assetSlug.length > 128 || !slugPattern.test(assetSlug) || !['use', 'interest'].includes(activity)) throw new Error('Usage requires a dataset slug and use/interest activity');
      const observed = now().toISOString(), day = observed.slice(0, 10);
      if (sentDay !== day) { sent.clear(); sentDay = day; }
      const key = `${day}/${assetSlug}/${activity}`;
      for (const [entry, until] of blocked) if (until <= Date.now()) blocked.delete(entry);
      if (sent.has(key) || pending.has(key) || blocked.has(key)) return;
      if (pending.size >= 1024) { diagnostic('buffer_full'); return; }
      pending.set(key, {name: `app-usage/${options.producer}/v1/${day}/${globalThis.crypto.randomUUID().replaceAll('-', '')}.json`,
        receipt: {schema_version: 1, asset_slug: assetSlug, activity, observed_at: observed}});
    },
    async flush() {
      const batch = [...pending.entries()]; pending.clear();
      let success = true;
      const deadline = Date.now() + 3000;
      for (const [key, item] of batch) {
        if (Date.now() >= deadline) { success = false; blocked.set(key, Date.now() + 300000); diagnostic('flush_deadline'); continue; }
        for (let attempt = 0; attempt < 2; attempt++) {
          const controller = new AbortController();
          let timer: ReturnType<typeof setTimeout> | undefined;
          try {
            await Promise.race([options.writeReceipt(item.name, item.receipt, controller.signal), new Promise<never>((_resolve, reject) => {
              timer = setTimeout(() => { controller.abort(); reject(Object.assign(new Error('upload_timeout'), {name: 'UploadTimeoutError'})); }, Math.max(1, Math.min(1000, deadline - Date.now())));
            })]);
            sent.add(key); break;
          }
          catch (error) {
            if (attempt === 1) { success = false; blocked.set(key, Date.now() + 300000); diagnostic(error instanceof Error ? error.name : 'upload_failed'); }
          } finally { if (timer !== undefined) clearTimeout(timer); }
        }
      }
      return success;
    },
  };
}
