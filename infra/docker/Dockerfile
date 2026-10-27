# Job image for every container-based Lambda (collectors, build jobs). One image, one handler per
# function via image_config.command in modules/jobs. Built and pushed by CI to the ECR repo from
# module data_lake (immutable tags); Inspector scans it continuously.
# --- aws-lambda-rie rebuilt with a patched Go toolchain -------------------------------------
# The AWS base image ships /usr/local/bin/aws-lambda-rie (local-test emulator, unused in Lambda)
# built with Go 1.26.7 and go-chi/chi/v5 v5.2.4; Inspector flags its Go stdlib and chi CVEs and
# upstream v1.37 has no fixed build. Rebuild it from the pinned tag with Go 1.27.2,
# chi/v5 v5.4.0 (first release that fixes CVE-2026-114437 / GHSA-j6qf-3m9c-vwj3; requires Go 1.24)
# and x/sys v0.44.0, cross-compiled for the target arch.
# GHSA-mqqf-5wvp-8fh8: accepted risk. aws-lambda-rie is the local-test emulator and is never
# invoked in Lambda. The Go vulnerability database still lists the unversioned github.com/go-chi/chi
# module as having no fix. Inspector suppression rules are not managed in this stack; if the
# finding remains on invdash/jobs, suppress that vulnerability id by hand. v5.4.0 does include
# the v5.2.4 RedirectSlashes hardening.
FROM --platform=$BUILDPLATFORM docker.io/library/golang:1.27.2-bookworm@sha256:5cf287a799e6b94384bad13d16b14904c531f51ba65792237e122ce42b392f61 AS rie
ARG TARGETARCH=arm64
ARG RIE_REF=c622c06822a4e5390cc71f29df37594646b10700
WORKDIR /src
RUN git init -q . \
 && git fetch -q --depth 1 https://github.com/aws/aws-lambda-runtime-interface-emulator.git "${RIE_REF}" \
 && git checkout -q FETCH_HEAD \
 && go mod edit -go=1.27 -toolchain=none \
 && go get github.com/go-chi/chi/v5@v5.4.0 golang.org/x/sys@v0.44.0 \
 && go mod tidy \
 && CGO_ENABLED=0 GOOS=linux GOARCH="${TARGETARCH}" go build -trimpath -ldflags "-s -w" -o /out/aws-lambda-rie ./cmd/aws-lambda-rie \
 && go version -m /out/aws-lambda-rie | grep -E 'github.com/go-chi/chi/v5[[:space:]]+v5.4.0'

# --- job image --------------------------------------------------------------------------------
# Base pinned by digest (refreshed 2026-10-27) so the image picks up the AL2023 builds that fix
# pcre2, libcap, curl and ca-certificates. OS packages are upgraded on top of that pin.
FROM public.ecr.aws/lambda/python:3.12-arm64@sha256:43c5c3f9ebcfa6d849976719bfe0b8e041a5ead686a948a728262da7d868b051

# AL2023 locks dnf to the image's release; --releasever=latest pulls the fixed package builds.
# The second upgrade names pcre2, libcap, curl and ca-certificates so a missing package fails
# the build instead of leaving those CVEs in place.
RUN dnf -y --releasever=latest upgrade --refresh \
 && dnf -y --releasever=latest upgrade pcre2 libcap curl ca-certificates \
 && dnf clean all \
 && rm -rf /var/cache/dnf \
 && python -m pip install --no-cache-dir --upgrade "pip>=26.2" "setuptools>=78.1.1"
COPY --from=rie /out/aws-lambda-rie /usr/local/bin/aws-lambda-rie

COPY services/data-jobs/requirements-jobs.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements-jobs.txt

# Flat layout: shared helpers and every handler module sit side by side, as in tests/conftest.py.
COPY services/data-jobs/src/app/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/trend_metrics/ services/data-jobs/src/functions/q1_fundamentals/ services/data-jobs/src/functions/short_interest/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/options_daily/ services/data-jobs/src/functions/backfill/ services/data-jobs/src/functions/status_feed/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/daily_prices/ services/data-jobs/src/functions/dashboard_build/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/macro_daily/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/news_sentiment/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/hourly_prices/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/regulatory_feeds/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/gov_contracts/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/price_reconcile/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/release_day/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/company_ir/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/deep_dive/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/sec_filings/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/kalshi_fomc/ ${LAMBDA_TASK_ROOT}/
COPY services/data-jobs/src/functions/fund_filings/ services/data-jobs/src/functions/fund_profile/ ${LAMBDA_TASK_ROOT}/

# Lambda always runs the code as its own unprivileged user and checks health itself.
# checkov:skip=CKV_DOCKER_2: Lambda manages function health; HEALTHCHECK is ignored by the runtime
# checkov:skip=CKV_DOCKER_3: Lambda runs the image as a non-root sandbox user regardless of USER

CMD ["status_feed_job.handler"]
