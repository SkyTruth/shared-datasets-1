# Usage retention provider fixture

`retention-plan.json` contains actual `terraform show -json` resource changes
from Terraform 1.8.5 and the repository's locked Google provider 7.31.0.

The offline producer copied the four complete production resource blocks for
the private raw bucket, usage sink, sink writer and default storage exclusion.
It supplied the reviewed collection configuration, fixed project/bucket
variables, an explicitly dummy access token, and no backend or remote state.
`terraform plan -refresh=false` created a local plan only. No apply or live
Google API operation was performed.

The fixture exercises legitimate provider defaults such as `with_state = ANY`,
empty lifecycle selectors and computed writer identities. The deployment test
passes this serialized provider output through both the resource allowlist and
saved-plan permission checker. It also proves that unknown retention fields
remain rejected.

Regenerate from the same resource blocks and locked provider when the collection
filter, retention contract or provider representation changes. Keep only
`format_version`, `terraform_version` and `resource_changes` in the fixture;
never include credentials or production state.
