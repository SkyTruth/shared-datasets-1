cron_alert_notification_channels = [
  "projects/shared-datasets-1/notificationChannels/6831586092945135667",
]

shared_bucket_public_object_viewer_enabled = false

shared_dataset_consumer_projects = {
  cerulean = {
    project_id   = "cerulean-338116"
    display_name = "Shared datasets reader for Cerulean"
  }
  thirty_by_thirty = {
    project_id   = "x30-399415"
    display_name = "Shared datasets reader for 30x30"
  }
  skytruth_tech = {
    project_id   = "skytruth-tech"
    display_name = "Shared datasets reader for SkyTruthTech"
  }
}

pmtiles_serving_mode     = "cdn"
pmtiles_redirector_image = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/pmtiles-redirector:20260505144357"
catalog_viewer_image     = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/catalog-viewer:20260509032023"
metadata_service_image   = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/metadata-service:bootstrap-required"

cerulean_pmtiles_cookie_signer_service_accounts = [
  "serviceAccount:734798842681-compute@developer.gserviceaccount.com",
]
