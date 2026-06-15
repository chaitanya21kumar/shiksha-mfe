'use strict';

// Next.js rejects a rewrite whose `destination` doesn't start with `/`, `http://`
// or `https://`. When a destination is built from an unset env var — e.g.
// `${process.env.NEXT_PUBLIC_TELEMETRY_URL}/v1/telemetry` becomes
// `undefined/v1/telemetry` — the build and dev server crash with
// "Invalid rewrites found" on a fresh checkout.
//
// `isResolvedRewrite` keeps a rule only when its destination has no unresolved
// env var. It matches `undefined` as a whole segment (bounded by start/end or
// `/`, `.`, `:`), so a legitimate destination that merely contains the text
// "undefined" (e.g. `/api/undefined-handler`) is preserved. With the env vars
// set, every rewrite passes through unchanged.
const UNRESOLVED_ENV = /(^|[\/.:])undefined([\/.:]|$)/;

const isResolvedRewrite = (rule) =>
  !rule || !UNRESOLVED_ENV.test(String(rule.destination));

module.exports = { isResolvedRewrite };
