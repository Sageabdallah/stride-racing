# Job image: Lambda python base so the same image serves container Lambdas
# (entrypoint overridden per function) and Fargate tasks.
#
# Build context is the REPO ROOT (04_ecr_image.sh passes infra/..). The
# original paths said Race-Analytics/… — the Mac export-folder layout where
# the repo's parent holds a Race-Analytics/ dir — and failed on any CI
# checkout; never caught because the image had never been built anywhere.
FROM public.ecr.aws/lambda/python:3.11
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt boto3
COPY server/python ${LAMBDA_TASK_ROOT}/server/python
COPY migrations ${LAMBDA_TASK_ROOT}/migrations
COPY infra/jobs ${LAMBDA_TASK_ROOT}/jobs
ENV PYTHONPATH=${LAMBDA_TASK_ROOT}/server/python:${LAMBDA_TASK_ROOT}
CMD ["jobs.handler.dispatch"]
