output "executor_role_arn" { value = aws_iam_role.executor.arn }
output "run_task_definition_arn" { value = aws_ecs_task_definition.run_executor.arn }
output "daemon_task_definition_arn" { value = aws_ecs_task_definition.dagster_daemon.arn }
