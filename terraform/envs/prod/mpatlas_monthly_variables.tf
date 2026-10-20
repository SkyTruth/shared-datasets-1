variable "mpatlas_monthly_image" {
  description = "Immutable MPAtlas image digest supplied by the protected deployment workflow."
  type        = string
  default     = "unused-by-other-targeted-deployments"
}

variable "mpatlas_monthly_schedule" {
  description = "Monthly MPAtlas source check, at 11:00 UTC on the second day."
  type        = string
  default     = "0 11 2 * *"
}
