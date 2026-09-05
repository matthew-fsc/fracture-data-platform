variable "environment" { type = string }
variable "admin_role" {
  type    = string
  default = "fracture_admin"
}
variable "artifact_bucket" { type = string }
variable "audit_bucket" { type = string }
variable "audit_retention_days" {
  type        = number
  default     = 2557
  description = "Seven years. Spec 17.4 proposes this as the default with a per-tenant override."
}
variable "log_retention_days" {
  type    = number
  default = 365
}
variable "tags" {
  type    = map(string)
  default = {}
}
