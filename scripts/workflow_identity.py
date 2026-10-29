"""Current display names and explicit aliases for historical GitHub runs.

Filenames are stable workflow identities. Keep aliases while stored incidents,
deployment evidence or reruns can refer to the original names. Job names and
Terraform sync labels are separate persisted contracts and are not renamed.
"""

WORKFLOW_NAMES = {
    'artifact-registry-iam-sync.yml': ('Infrastructure / Artifact Registry IAM sync', 'Artifact Registry IAM sync'),
    'bucket-hygiene-audit.yml': ('Bucket hygiene audit',),
    'catalog-drift-guard.yml': ('Catalog drift guard',),
    'catalog-viewer-deploy.yml': ('Catalog / Viewer deploy', 'Catalog viewer deploy'),
    'catalog-web-deploy.yml': ('Catalog / Web deploy', 'Catalog web deploy'),
    'ci.yml': ('CI / Validate and deliver', 'CI'),
    'cron-alert-policy-sync.yml': ('Infrastructure / Cron alert policy sync', 'Cron alert policy sync'),
    'dataset-breaking-change-alert.yml': ('Dataset breaking change alert',),
    'dataset-usage-deploy.yml': ('Data / Usage collector deploy', 'Dataset usage deploy'),
    'deployment-recovery.yml': ('Deployments / Read-only reconciliation', 'Deployment read-only reconciliation'),
    'deployment-verification.yml': ('Deployments / Completion check', 'Deployment completion check', 'Deployment terminal verification'),
    'eamlis-held-candidate-preview.yml': ('Inspect held EAMLIS candidate',),
    'eamlis-monthly-deploy.yml': ('Ingestion / EAMLIS monthly deploy', 'EAMLIS monthly deploy'),
    'feature-preview-deploy.yml': ('Preview / Deploy feature branch', 'Deploy Feature Branch to Preview'),
    'feature-preview-destroy.yml': ('Preview / Destroy environment', 'Destroy Preview Environment'),
    'ingestion-schedule-control.yml': ('Ingestion / Schedule control', 'Ingestion schedule control'),
    'mpatlas-monthly-deploy.yml': ('Ingestion / MPAtlas monthly deploy', 'MPAtlas monthly deploy'),
    'pmtiles-cdn-sync.yml': ('Catalog / PMTiles CDN sync', 'PMTiles CDN sync'),
    'preview-terraform-iam-sync.yml': ('Infrastructure / Preview Terraform IAM sync', 'Preview Terraform IAM sync'),
    'prod-terraform-state-reconcile.yml': ('Prod Terraform state reconciliation',),
    'prod-terraform-target-apply.yml': ('Infrastructure / Production Terraform target apply', 'Prod Terraform target apply'),
    'publish-dataset.yml': ('Data / Approved mutation', 'Approved dataset mutation'),
    'publish-typescript-sdk.yml': ('SDK / Publish TypeScript', 'Publish TypeScript SDK'),
    'release-index-rebuild.yml': ('Catalog / Release index rebuild', 'Release index rebuild'),
    'repo-functionality-alert.yml': ('Alerts / Repository functionality', 'Repo functionality alert'),
    'resolve-workflow-alert.yml': ('Alerts / Legacy workflow resolution', 'Resolve legacy workflow alert'),
    'scheduled-ingestion-deploy-iam-sync.yml': ('Infrastructure / Ingestion deploy IAM sync', 'Scheduled ingestion deploy IAM sync'),
    'scratch-cleanup-audit.yml': ('Data / Scratch cleanup audit', 'Scratch cleanup audit'),
    'scratch-cleanup-iam-sync.yml': ('Infrastructure / Scratch cleanup IAM sync', 'Scratch cleanup IAM sync'),
    'sea-ice-daily-deploy.yml': ('Ingestion / Sea ice daily deploy', 'Sea ice daily deploy'),
    'terraform-state-read-iam-sync.yml': ('Terraform state read IAM sync',),
    'unattended-workflow-alert.yml': ('Alerts / Workflow health and Slack reconciliation', 'Workflow health and Slack reconciliation'),
    'wdpa-monthly-deploy.yml': ('Ingestion / WDPA monthly deploy', 'WDPA monthly deploy'),
    'wdpa-monthly-build.yml': ('Ingestion / WDPA monthly build',),
}
