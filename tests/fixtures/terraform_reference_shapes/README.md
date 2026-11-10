# Terraform configuration reference shapes

`plan.json` was produced by Terraform **1.8.5** with Google provider **7.31.0**
from the adjacent `main.tf` and `child/main.tf`, without applying or refreshing:

```sh
terraform init -backend=false -input=false
terraform plan -refresh=false -lock=false -input=false -out=reference.tfplan
terraform show -json reference.tfplan > plan.full.json
```

The fixture retains `format_version`, `terraform_version`, `resource_changes`
and `configuration.root_module` verbatim from that JSON. Other fields are omitted.
The provider uses a dummy access token; this plan makes no authenticated API calls.
The source contains only disposable fixture identities and must never be applied.

Dynamic resource keys produce the bare resource reference and `each.key`.
Literal string/numeric keys also produce indexed and attribute references.
Dynamic module keys produce only the bare module call; literal keys produce
indexed module/output traversals. References inside a module remain local.
