resource "google_service_account" "this" {
  project = "shared-datasets-1"
  account_id = "fixture-child"
}
resource "google_service_account_iam_member" "local" {
  service_account_id = google_service_account.this.name
  role = "roles/iam.serviceAccountUser"
  member = "user:fixture@example.org"
}
output "name" { value = google_service_account.this.name }
