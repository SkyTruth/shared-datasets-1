variable "wdpa_monthly_image" {
  description = "Container image URI for the WDPA monthly Cloud Run Job."
  type        = string
}

variable "wdpa_source_url_template" {
  description = "Protected Planet monthly WDPA/WDOECM source URL template."
  type        = string
  default     = "https://d1gam3xoknrgr2.cloudfront.net/current/WDPA_WDOECM_{month_token}_Public_all_shp.zip"
}
