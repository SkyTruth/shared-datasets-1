> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# G packaging addendum — plan only

Approval requested before changing these additional files. This is dependency closure for the approved new local helper, not deployment authorization. Read deploy-scheduled-ingestion for the packaging/trigger boundary; preserve job/IAM/schema/runtime interfaces.

## Evidence and minimal closure

`ingestion/wdpa_monthly/run.py:42` imports localization and calls its materializer at line 696. Its Dockerfile explicitly copies localization at line 43 but omits new `scripts/translation_local_io.py`. The image's existing job-import smoke would fail. Other ingestion entries import release-feature-model/vector helpers, not localization; none needs this helper copied for its current entrypoint. WDPA's source-provided translation producer remains valid under the new failure semantics (its notes do not match legacy failure notes).

Exact additional edits proposed:

1. `ingestion/wdpa_monthly/Dockerfile`: add the one explicit helper COPY next to localization.
2. `.github/workflows/wdpa-monthly-deploy.yml`: add helper to push path dependencies and name it in the existing image import smoke. No deployment behavior/settings/credentials changes.
3. `.github/workflows/ci.yml`: include `translation_local_io` in the existing geospatial change detector's scripts and tests alternatives; include its focused test in the existing geospatial pytest invocation. Keep all existing gate/queue/deploy behavior unchanged.
4. `tests/test_wdpa_monthly_deploy_workflow.py`: extend required copies/triggers; add a behavioral import-closure test that copies only Dockerfile-declared `scripts/*.py` sources into a disposable directory and runs an isolated subprocess import of localization (and the helper), with repository imports excluded. This fails now and passes with the COPY. Exercise the actual workflow filter regex against the helper/test paths, not only presence of a string. Existing workflow tests continue covering the image smoke.

The runtime is unchanged and no container build/download/install or deployment is planned. Local isolated import with stdlib dependencies proves this added module closure; an actual Linux image build/native smoke remains CI's responsibility. Run the WDPA ingestion/deploy suites and existing focused translation tests, lint and diff check.

F3a owns a separately approved HOLD gate in WDPA deploy. These edits only affect path filters and the existing import-smoke string; coordinate the shared workflow path and combine with its gate in supervisor integration. F3's future Docker/API changes do not substitute for this independent fix. No edits to any of these files until root says PLAN APPROVED.
