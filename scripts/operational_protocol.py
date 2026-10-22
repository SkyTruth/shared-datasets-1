"""Installed policy protocol shared by activation and deployment selection.

Every change to these files requires a fresh protected policy receipt before a
worker can establish cloud coverage or emit its heartbeat. Keep this module free
of runtime imports: CI selects deployments before installing dependencies.
"""

PROTOCOL_FILES = (
    "scripts/operational_protocol.py",
    "scripts/operational_cutover.py",
    "scripts/operational_delivery_activation.py",
    "scripts/monitoring_routing_audit.py",
    ".github/workflows/prod-terraform-target-apply.yml",
    ".github/workflows/unattended-workflow-alert.yml",
    "scripts/operational_reconcile.py",
    "scripts/cloud_observation_ledger.py",
    "scripts/cloud_operational_observations.py",
    "scripts/cloud_incident_adapter.py",
    "scripts/deployment_revision.py",
    "scripts/deployment_emission.py",
    "scripts/github_api_read.py",
    "terraform/envs/prod/operational_observer.tf",
    "catalog/operational-log-views.json",
    "scripts/operational_log_views.py",
)
