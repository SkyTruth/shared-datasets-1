# Retirement preserves historical Firestore data. The protected retirement
# workflow removes serving infrastructure separately; no database is deleted.
removed {
  from = google_firestore_database.feature_metadata

  lifecycle {
    destroy = false
  }
}
