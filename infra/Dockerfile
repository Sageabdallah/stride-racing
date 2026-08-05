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

# The MC engine itself. mc_api.py resolves this against PROJECT_ROOT — the
# repo root, /var/task here — and exec_module()s it at import time, taking
# RacingModel, simulate_race_monte_carlo and TARGET_TRACKS from it. Copying
# server/python alone left it out, so in the cloud the import raised
# FileNotFoundError, run_mc_simulation caught it and returned None, and the
# race loop turned that into a bare `continue`: the 10:00 job exited 0 having
# scored no race at all (2026-08-05, 31 races in, 0 selections out; the same
# on 2026-08-02). It failed per-race rather than once, which is why the whole
# card cost 0.9s of "MC" — 31 retries of a module load that could not succeed.
#
# It is the only root-level module the runtime loads by path;
# tests/test_image_contents.py holds that to one so the next one cannot be
# missed the same way.
COPY racing_system_v8.3_mc.py ${LAMBDA_TASK_ROOT}/racing_system_v8.3_mc.py

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
