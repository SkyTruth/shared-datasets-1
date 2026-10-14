import {createUsageReporter, type UsageReporterOptions, type UsageReporter} from './usage.js';
export type UsageStorageClient = {
  bucket(name: string): {file(name: string): {save(data: string, options: {
    resumable: false; contentType: string; timeout: number;
    preconditionOpts: {ifGenerationMatch: 0};
  }): Promise<unknown>}};
};
/** Supply an ADC-backed Storage client configured with autoRetry: false. */
export function createGcsUsageReporter(options: Omit<UsageReporterOptions, 'writeReceipt'> & {
  storage: UsageStorageClient; bucket?: string;
}): UsageReporter {
  return createUsageReporter({...options, async writeReceipt(name, receipt) {
    try {
      await options.storage.bucket(options.bucket ?? 'skytruth-shared-datasets-1-usage-raw').file(name)
        .save(JSON.stringify(receipt), {resumable: false, contentType: 'application/json',
          timeout: 1000, preconditionOpts: {ifGenerationMatch: 0}});
    } catch (error) {
      if (!(typeof error === 'object' && error !== null && 'code' in error && error.code === 412)) throw error;
    }
  }});
}
