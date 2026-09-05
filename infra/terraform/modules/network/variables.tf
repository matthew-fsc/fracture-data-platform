variable "environment" { type = string }
variable "region" { type = string }
variable "cidr_block" {
  type    = string
  default = "10.40.0.0/16"
}
variable "availability_zones" {
  type    = list(string)
  default = []
}
variable "enable_nat" {
  type        = bool
  default     = false
  description = "Egress for source systems reachable only over the public internet. Off by default: the data path should not need it."
}
variable "enable_flow_logs" {
  type        = bool
  default     = true
  description = "Flow logs are cheap and the first thing a PE security questionnaire asks for."
}
variable "flow_log_bucket_arn" {
  type    = string
  default = null
}
variable "tags" {
  type    = map(string)
  default = {}
}
