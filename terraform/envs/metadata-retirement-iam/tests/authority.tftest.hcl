mock_provider "google" {}

run "no_authority_by_default" {
  command = plan
  assert {
    condition     = length(google_service_account_iam_member.retirement_deleter) == 0
    error_message = "The default configuration must grant no deletion authority."
  }
}

run "reviewed_account_only" {
  command = plan
  variables {
    retirement_account_ids = ["117696104962177306505"]
  }
  override_data {
    target = data.google_service_account.retirement_target["117696104962177306505"]
    values = {
      unique_id = "117696104962177306505"
      name      = "projects/shared-datasets-1/serviceAccounts/metadata-index-loader@shared-datasets-1.iam.gserviceaccount.com"
    }
  }
  assert {
    condition     = length(google_service_account_iam_member.retirement_deleter) == 1
    error_message = "Authority must be granted only on the reviewed account present in the retirement plan."
  }
}

run "recreated_account_is_rejected" {
  command = plan
  variables {
    retirement_account_ids = ["117696104962177306505"]
  }
  override_data {
    target = data.google_service_account.retirement_target["117696104962177306505"]
    values = {
      unique_id = "999999999999999999999"
      name      = "projects/shared-datasets-1/serviceAccounts/metadata-index-loader@shared-datasets-1.iam.gserviceaccount.com"
    }
  }
  expect_failures = [data.google_service_account.retirement_target["117696104962177306505"]]
}

run "active_account_is_rejected" {
  command = plan
  variables {
    retirement_account_ids = ["100846506355649701710"]
  }
  expect_failures = [var.retirement_account_ids]
}
