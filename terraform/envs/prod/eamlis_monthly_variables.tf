variable "eamlis_monthly_image" {
  description = "Container image URI for the monthly e-AMLIS Cloud Run Job."
  type        = string
}

variable "eamlis_layer_url" {
  description = "Public OSMRE e-AMLIS ArcGIS hosted feature layer URL."
  type        = string
  default     = "https://services.arcgis.com/Vsy5ieu7PwNdunLd/arcgis/rest/services/eAMLISExternalView/FeatureServer/0"
}

variable "eamlis_where" {
  description = "ArcGIS SQL where clause for the canonical public e-AMLIS layer."
  type        = string
  default     = "LAT_DEG > 0"
}

variable "eamlis_page_size" {
  description = "ArcGIS query page size for e-AMLIS feature downloads."
  type        = number
  default     = 2000
}

variable "eamlis_monthly_schedule" {
  description = "Cloud Scheduler cron schedule for the monthly e-AMLIS job."
  type        = string
  default     = "0 10 2 * *"
}
