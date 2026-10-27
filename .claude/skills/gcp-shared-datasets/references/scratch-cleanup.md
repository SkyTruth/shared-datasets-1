# Standing scratch cleanup

The scheduled `Scratch cleanup audit` workflow is the only standing automated
prefix cleanup. Its age-based abandonment policy writes a warning when the
newest object in a `_scratch/pending-publishes/{asset-slug}/{proposal-id}/` prefix
is 60 days old. At 90 days, the prefix becomes a deletion candidate only if that
object's name, generation, and update time still match the warning. A first
warning after day 90 can become eligible on the next audit. Historical content
matches are not proof of completed publication, and cleanup does not inspect PR
status. Deletes use each listed object's exact generation; the prefix operation
is not atomic. Dry-run reports list candidates without writing markers or
deleting objects.

EAMLIS `source-loss-{64-character SHA-256}` proposals are held review evidence,
not abandoned uploads, and are exempt from this age-based policy. Their raw
source/candidate records remain until explicit reviewed cleanup; successful
publication retains the existing exact-promoted-source cleanup behavior.
