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

# Which commit these bytes were built from. Stamped twice on purpose:
#
#   LABEL  readable with `aws ecr describe-images` / `docker inspect`, i.e.
#          from outside, without running anything.
#   ENV    readable from *inside* the running container, which is where the
#          04:00 preflight freshness check runs. A label alone cannot be read
#          there, which is the whole reason both exist.
#
# Declared after the COPY lines so a new SHA does not invalidate the pip layer:
# ARG before them would rebuild the dependency install on every commit.
ARG GIT_SHA=""
LABEL org.opencontainers.image.revision=$GIT_SHA
ENV STRIDE_IMAGE_SHA=$GIT_SHA
RUN chmod +x /entrypoint.sh
ENV PYTHONPATH=${LAMBDA_TASK_ROOT}/server/python:${LAMBDA_TASK_ROOT}
ENTRYPOINT ["/entrypoint.sh"]
CMD ["jobs.handler.dispatch"]
