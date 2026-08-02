# One image for container Lambdas and Fargate tasks.
#
# Base is Debian slim, NOT the Lambda python base: the Lambda 3.11 base is
# AL2 (glibc 2.26) and rejects the ML stack's manylinux_2_28 wheels — even
# correctly-pinned sklearn/pandas fell back to sdists and a compiler that
# isn't there. Debian bookworm (glibc 2.36) takes every wheel; awslambdaric
# restores Lambda compatibility, and entrypoint.sh picks Lambda vs Fargate
# mode from AWS_LAMBDA_RUNTIME_API. libgomp1 is the OpenMP runtime
# lightgbm/xgboost link against.
FROM python:3.11-slim
ENV LAMBDA_TASK_ROOT=/var/task
WORKDIR /var/task
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt boto3 awslambdaric
COPY server/python ${LAMBDA_TASK_ROOT}/server/python
COPY migrations ${LAMBDA_TASK_ROOT}/migrations
COPY infra/jobs ${LAMBDA_TASK_ROOT}/jobs
COPY infra/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENV PYTHONPATH=${LAMBDA_TASK_ROOT}/server/python:${LAMBDA_TASK_ROOT}
ENTRYPOINT ["/entrypoint.sh"]
CMD ["jobs.handler.dispatch"]
