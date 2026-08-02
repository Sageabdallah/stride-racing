#!/bin/sh
# Lambda sets AWS_LAMBDA_RUNTIME_API; Fargate does not. Same image, two
# invocation modes — the original assumption that the Lambda base's own
# entrypoint would serve Fargate was never true: on ECS there is no
# runtime API and no RIE, so the bootstrap could only hang.
set -e
if [ -n "${AWS_LAMBDA_RUNTIME_API:-}" ]; then
  exec python -m awslambdaric "$@"
fi
exec python "${LAMBDA_TASK_ROOT}/jobs/handler.py"
