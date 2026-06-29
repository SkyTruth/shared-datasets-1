---
name: invariant-first-engineering
description: Use when implementing, reviewing, refactoring, simplifying, or iterating on code where correctness depends on strong invariants, persisted data formats, schemas, state machines, infrastructure behavior, shared abstractions, or core domain logic. Trigger especially after test failures, bug reports, autonomous coding loops, defensive fallbacks, broad exception handling, duplicated guards, unclear state modeling, local handling for malformed internal states, or requests to reduce complexity. Guides Codex to make invalid states impossible, strengthen producers and boundaries, delete redundant defenses, remove obsolete branches, collapse duplicate representations, and fail fast internally instead of accumulating defensive complexity.
---

# Invariant-First Engineering

Use this skill to resist defensive, locally patched code. Prefer designs that make bad states unrepresentable, validate once at trust boundaries, and keep internal code simple because its invariants are strong.

## Required Pause

Before editing code, identify the invariant:

```text
Invariant:
Bad states that must be impossible:
Where the invariant is established:
Where the invariant is consumed:
Trust boundaries:
```

If the invariant cannot be stated clearly, inspect the surrounding design before adding code. Do not add fallbacks to compensate for an unclear model.

## Deletion Bias

Treat simplification as part of the fix. After identifying the invariant, look for code that exists only because the invariant was weak, implicit, duplicated, or unenforced.

Before adding code, ask:

```text
What code would be unnecessary if the invariant were enforced earlier?
What states, branches, flags, options, or fallback paths should stop existing?
Which validations are duplicated across consumers?
Which abstractions only hide uncertainty rather than encode a rule?
Can this fix be a net deletion?
```

Prefer changes that reduce the number of representable states and remove handling for states that should be impossible.

## Classify The Failure

For every observed failure, classify it before fixing it:

```text
Boundary validation needed
Producer bug
Invalid persisted data
Impossible internal state leaked
Consumer misuse
External dependency failure
Test expectation wrong
```

Boundary validation and external dependency failures may justify graceful handling. Producer bugs, impossible internal states, and consumer misuse usually call for stronger construction, schema validation, type modeling, assertions, migrations, or shared helper changes.

## Preferred Fix Order

Prefer fixes in this order:

1. Make the bad state impossible at construction, parsing, writing, or persistence time.
2. Encode the invariant in a type, schema, constructor, parser, migration, database constraint, or single shared helper.
3. Validate once at the trust boundary.
4. Fail fast internally with a narrow assertion or explicit error.
5. Add local defensive handling only when the bad state is genuinely external, recoverable, and expected.

Do not add consumer-side repair for data that a producer owns unless the data already exists in persisted form and needs a migration or compatibility bridge.

## Required Removal Pass

Before finishing a change, inspect the touched area for removable code.

Look for:

```text
guards made redundant by stronger validation
fallbacks for impossible internal states
duplicate null or shape checks
compatibility branches with no current caller or persisted-data need
parallel representations of the same state
boolean flags that encode invalid combinations
wrappers that no longer centralize a real rule
tests that assert graceful handling of impossible states
comments explaining branches that should not exist
normalization repeated after a single canonical normalization point
```

For each candidate, decide:

```text
Delete now
Keep as boundary handling
Keep temporarily for persisted-data compatibility
Keep because it is public API compatibility
Keep because evidence is insufficient
```

Do not delete public API behavior, persisted-format compatibility, migrations, or documented external behavior without confirming the compatibility contract.

## Fallback Gate

Before adding any fallback, guard, retry, broad catch, default value, optional branch, or best-effort path, answer:

```text
What invariant does this preserve?
Can the invalid state be made unrepresentable instead?
Is this handling external input or an internal impossible state?
Will this hide corrupted data, bad writes, or caller misuse?
Can this branch still be reached after the stronger fix?
What code becomes removable if the invariant is enforced earlier?
```

If the answer is only "to be safe," do not add the fallback.

## Loop Damage Check

After each test-fix iteration, ask:

```text
Did this add a guard, fallback, catch, retry, optional path, duplicate branch, flag, wrapper, or coercion?
Did it strengthen a producer or boundary?
Did it reduce the number of representable states?
Did it remove any now-impossible handling?
Is the diff net simpler, or did it only move complexity?
Could the same fix be achieved by deleting code instead?
```

If an iteration only adds local defenses, stop and re-evaluate the invariant. Prefer one stronger boundary plus deletion over multiple local patches.

## Smells To Challenge

Treat these as design smells unless there is a clear boundary or compatibility reason:

```text
broad try/catch returning defaults
silent fallback to empty list, empty object, empty string, zero, or null
duplicated null checks across internal consumers
best-effort parsing inside core logic
consumer-side cleanup of producer-owned data
optional fields that are required in practice
parallel code paths for states that should not coexist
tests asserting graceful handling of impossible internal states
comments explaining impossible combinations
local normalization repeated in multiple places
catching exceptions from code that should have been validated earlier
abstractions that only rename branches without reducing state space
feature flags or booleans that permit invalid combinations
```

When one of these appears, look for the producer, parser, constructor, schema, migration, or shared abstraction that should own the invariant.

## Safe Deletion Standard

Before deleting code, gather enough evidence for the risk level.

Use local search, tests, type checks, call sites, schemas, migrations, and documentation to distinguish dead code from compatibility code.

Safe deletion usually requires at least one of:

```text
the code is unreachable after a newly enforced invariant
all call sites already satisfy the stronger contract
the branch handles a state that construction now forbids
the fallback duplicates boundary validation
the abstraction no longer owns unique behavior
tests can be updated to assert the invariant instead of the fallback
```

When evidence is incomplete, prefer a small explicit internal error over silent fallback, but do not remove documented external behavior casually.

## Assertions

Use assertions or narrow internal errors for impossible states after boundaries have been validated.

Good internal failures are loud and specific. They should reveal a broken invariant quickly.

Do not convert impossible internal states into user-visible fallback behavior unless the system has a documented compatibility requirement.

## Persisted Data

For persisted formats, prefer explicit versioning, migrations, schema validation, and writer-side guarantees.

When malformed persisted data already exists, distinguish between:

```text
New writes must be prevented
Existing data must be migrated
Readers need a temporary compatibility bridge
Bad data should fail loudly
```

Temporary compatibility code must be named as temporary, scoped narrowly, and paired with a removal path when practical.

## Abstraction Discipline

Before adding an abstraction, identify the duplication or invariant it removes.

Good abstractions centralize a real rule. Bad abstractions hide uncertainty.

Prefer one clear owner for each invariant. Avoid spreading the same check across callers.

Remove abstractions that no longer own a distinct rule, collapse invalid states, or reduce call-site complexity.

## Final Response Requirement

When this skill affects the work, include:

```text
Invariant enforced:
Boundary changed:
Code removed:
Internal handling removed:
Fallbacks added:
Fallbacks rejected:
Deletion candidates left in place:
Remaining uncertainty:
```

If no code was changed, summarize the invariant-first simplification recommendation instead.
