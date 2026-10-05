# Job image for every container-based Lambda (collectors, build jobs). One image, one handler per
# function via image_config.command in modules/jobs. Built and pushed by CI to the ECR repo from
# module data_lake (immutable tags); Inspector scans it continuously.
FROM public.ecr.aws/lambda/python:3.12-arm64

COPY requirements-jobs.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements-jobs.txt

# Flat layout: shared helpers and every handler module sit side by side, as in tests/conftest.py.
COPY app/ ${LAMBDA_TASK_ROOT}/
COPY functions/trend_metrics/ functions/q1_fundamentals/ functions/short_interest/ ${LAMBDA_TASK_ROOT}/
COPY functions/options_daily/ functions/backfill/ functions/status_feed/ ${LAMBDA_TASK_ROOT}/

# Lambda always runs the code as its own unprivileged user and checks health itself.
# checkov:skip=CKV_DOCKER_2: Lambda manages function health; HEALTHCHECK is ignored by the runtime
# checkov:skip=CKV_DOCKER_3: Lambda runs the image as a non-root sandbox user regardless of USER

CMD ["status_feed_job.handler"]
