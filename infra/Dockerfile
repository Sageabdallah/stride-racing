# Job image: Lambda python base so the same image serves container Lambdas
# (entrypoint overridden per function) and Fargate tasks.
FROM public.ecr.aws/lambda/python:3.11
COPY Race-Analytics/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt boto3
COPY Race-Analytics/server/python ${LAMBDA_TASK_ROOT}/server/python
COPY Race-Analytics/migrations ${LAMBDA_TASK_ROOT}/migrations
COPY infra/jobs ${LAMBDA_TASK_ROOT}/jobs
ENV PYTHONPATH=${LAMBDA_TASK_ROOT}/server/python:${LAMBDA_TASK_ROOT}
CMD ["jobs.handler.dispatch"]
