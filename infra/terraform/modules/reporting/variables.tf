variable "environment" { type = string }
variable "pack_bucket" { type = string }
variable "kms_key_arn" { type = string }
variable "signed_url_ttl_seconds" {
  type        = number
  default     = 3600
  description = "How long a delivery link lives. An hour is enough to open a pack and short enough that a forwarded link expires."
}
variable "log_retention_days" {
  type    = number
  default = 365
}
variable "tags" {
  type    = map(string)
  default = {}
}
