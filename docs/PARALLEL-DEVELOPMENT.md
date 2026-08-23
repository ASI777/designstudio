# Parallel proposal and integration system

Parallel work never writes to the released baseline. Each `proposal-branch/1`
records its immutable baseline and current child-configuration head, source
revisions, consumed contract versions, proposed contract changes, artifacts,
evidence, assumptions, approvals requested and author identity.

An `integration-sandbox/1` snapshots selected branch heads. Evaluation rejects
advanced/stale heads, ancestry outside the shared baseline, missing evidence and
different proposed digests or versions for the same contract. Passing checks
move the sandbox only to `ready_for_approval`. Every requested role must approve,
and an author of any included branch is forbidden from approving the assembled
result. Evaluation and approval leave every branch and baseline unchanged.

Agents receive typed tasks tied to one branch. Step, elapsed-time and cost
budgets are monotonic hard ceilings; progress above a ceiling is rejected.
Tasks are explicitly cancellable, and an interrupted running task can be
returned to its persisted queue at most three times. `product/context` retrieves
a deterministic, bounded semantic neighborhood so agents do not load the full
project by default.
