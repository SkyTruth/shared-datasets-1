variable "sea_ice_daily_image" {
  description = "Container image URI for the daily IMS sea-ice Cloud Run Job."
  type        = string
}

variable "sea_ice_source_url_template" {
  description = "NOAA/NSIDC IMS 4 km GeoTIFF source URL template."
  type        = string
  default     = "https://noaadata.apps.nsidc.org/NOAA/G02156/GIS/4km/{yyyy}/{file_name}"
}

variable "sea_ice_max_lookback_days" {
  description = "Maximum number of source days to probe from RUN_DATE or UTC today."
  type        = number
  default     = 14
}

variable "sea_ice_daily_schedule" {
  description = "Cloud Scheduler cron schedule for the daily IMS sea-ice job."
  type        = string
  default     = "0 15 * * *"
}
