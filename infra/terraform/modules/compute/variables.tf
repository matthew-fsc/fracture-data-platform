variable "environment" { type = string }
variable "region" { type = string }
variable "account_id" { type = string }
variable "image" { type = string }
variable "artifact_bucket" { type = string }
variable "log_group" { type = string }
variable "task_execution_role_arn" { type = string }
variable "daemon_cpu" { default = "512" }
variable "daemon_memory" { default = "1024" }
variable "run_cpu" { default = "1024" }
variable "run_memory" { default = "4096" }
variable "tags" {
  type    = map(string)
  default = {}
}
