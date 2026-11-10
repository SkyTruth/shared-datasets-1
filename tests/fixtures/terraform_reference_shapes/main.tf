terraform {
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.31" }
  }
}
provider "google" {
  project      = "shared-datasets-1"
  access_token = "unused-offline-fixture"
}
resource "google_service_account" "github_job" {
  for_each = toset(["observer", "runtime"])
  project = "shared-datasets-1"
  account_id = "fixture-${each.key}"
}
resource "google_service_account" "counted" {
  count = 1
  project = "shared-datasets-1"
  account_id = "fixture-counted"
}
resource "google_service_account" "single" {
  project = "shared-datasets-1"
  account_id = "fixture-single"
}
resource "google_service_account_iam_member" "dynamic" {
  for_each = toset(["observer", "runtime"])
  service_account_id = google_service_account.github_job[each.key].name
  role = "roles/iam.serviceAccountUser"
  member = "user:fixture@example.org"
}
resource "google_service_account_iam_member" "literal" {
  service_account_id = google_service_account.github_job["observer"].name
  role = "roles/iam.serviceAccountUser"
  member = "user:fixture@example.org"
}
resource "google_service_account_iam_member" "numeric" {
  service_account_id = google_service_account.counted[0].name
  role = "roles/iam.serviceAccountUser"
  member = "user:fixture@example.org"
}
resource "google_service_account_iam_member" "attribute" {
  service_account_id = google_service_account.single.name
  role = "roles/iam.serviceAccountUser"
  member = "user:fixture@example.org"
}
module "child" {
  source = "./child"
  for_each = toset(["observer"])
}
resource "google_service_account_iam_member" "module_dynamic" {
  for_each = toset(["observer"])
  service_account_id = module.child[each.key].name
  role = "roles/iam.serviceAccountUser"
  member = "user:fixture@example.org"
}
resource "google_service_account_iam_member" "module_literal" {
  service_account_id = module.child["observer"].name
  role = "roles/iam.serviceAccountUser"
  member = "user:fixture@example.org"
}
