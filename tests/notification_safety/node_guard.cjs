"use strict";

// Trusted-test safety, inherited by Node children. This is not a sandbox for
// malicious programs; fake transports can still replace these module methods.
const childProcess = require("node:child_process");
const dns = require("node:dns");
const path = require("node:path");

function forbid(reason) {
  const message = `Notification test isolation forbids ${reason}`;
  // A best-effort production catch must not turn an escaped live send green.
  process.exitCode = 86;
  const error = new Error(message);
  error.name = "NotificationIsolationError";
  throw error;
}

function slackHost(value) {
  const host = String(value ?? "").toLowerCase().replace(/\.$/, "").split(":")[0];
  return host === "slack.com" || host === "slack-gov.com" || host.endsWith(".slack.com") || host.endsWith(".slack-gov.com");
}

function checkTarget(value) {
  if (value == null) return;
  if (typeof value === "object") {
    if (value.url) checkTarget(value.url);
    if (value.href) checkTarget(value.href);
    if (slackHost(value.hostname ?? value.host)) forbid("live Slack transport");
    return;
  }
  let parsed;
  try {
    parsed = new URL(String(value));
  } catch {
    if (slackHost(value)) forbid("live Slack transport");
    return;
  }
  if (slackHost(parsed.hostname)) forbid("live Slack transport");
  if (parsed.hostname === "secretmanager.googleapis.com" && parsed.pathname.includes("slack-webhook") && parsed.pathname.includes(":access")) {
    forbid("production webhook secret resolution");
  }
}

if (typeof globalThis.fetch === "function") {
  const fetch = globalThis.fetch;
  globalThis.fetch = function (input, ...args) {
    checkTarget(input);
    return fetch.call(this, input, ...args);
  };
}

for (const module of [require("node:http"), require("node:https")]) {
  for (const method of ["request", "get"]) {
    const original = module[method];
    module[method] = function (...args) {
      checkTarget(args[0]);
      if (typeof args[1] === "object") checkTarget(args[1]);
      return original.apply(this, args);
    };
  }
}

for (const module of [dns, dns.promises]) {
  for (const method of Object.keys(module)) {
    if ((method === "lookup" || method.startsWith("resolve")) && typeof module[method] === "function") {
      const original = module[method];
      module[method] = function (hostname, ...args) {
        if (slackHost(hostname)) forbid("live Slack transport");
        return original.call(this, hostname, ...args);
      };
    }
  }
}

for (const method of ["exec", "execSync", "execFile", "execFileSync", "spawn", "spawnSync", "fork"]) {
  const original = childProcess[method];
  childProcess[method] = function (command, ...args) {
    const text = [command, ...args.filter(Array.isArray).flat()].join(" ").toLowerCase();
    if ((text.includes("shared-datasets-slack-webhook-url") || text.includes("shared_datasets_slack_webhook_secret")) && (text.includes("access") || text.includes("secrets"))) {
      forbid("production webhook secret resolution");
    }
    if (["hooks.slack.com/", "slack.com/api/", "hooks.slack-gov.com/"].some(host => text.includes(host))) {
      forbid("live Slack subprocess transport");
    }
    // Explicit child environments must preserve the guard. A missing options
    // object inherits NODE_OPTIONS; no environment rewriting is needed.
    const options = args.find(value => value && typeof value === "object" && !Array.isArray(value));
    const node = method === "fork" || ["node", "nodejs", "node.exe"].includes(path.basename(String(command)));
    if (node && options?.env && !String(options.env.NODE_OPTIONS ?? "").includes(__filename)) {
      forbid("unguarded Node child environment");
    }
    return original.call(this, command, ...args);
  };
}

// Built-in ESM named imports must see the same guarded methods.
require("node:module").syncBuiltinESMExports();
