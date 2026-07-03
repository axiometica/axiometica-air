UPDATE approved_actions SET command_variants = (
  COALESCE(command_variants::jsonb,'{}') || jsonb_build_object(
    'kubernetes', 'kubectl exec {pod} -n {namespace} -- psql -U {db_user} -d {db_name} -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state=''idle'' AND state_change < NOW() - INTERVAL ''{idle_seconds} seconds'';"',
    'vcenter',    'psql -U {db_user} -d {db_name} -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state=''idle'' AND state_change < NOW() - INTERVAL ''{idle_seconds} seconds'';"',
    'aws_ssm',    'psql -U {db_user} -d {db_name} -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state=''idle'' AND state_change < NOW() - INTERVAL ''{idle_seconds} seconds'';"',
    'azure',      'psql -U {db_user} -d {db_name} -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state=''idle'' AND state_change < NOW() - INTERVAL ''{idle_seconds} seconds'';"'
  )
)::json WHERE tool_name = 'db_command';
