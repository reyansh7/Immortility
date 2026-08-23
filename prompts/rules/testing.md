# Verification

- Success criteria must be explicit before coding starts.
- Prefer targeted pytest paths named in the request or plan.
- If no test path is known, syntax-check touched Python files.
- A failing check is a retry signal, not a silent success.
- Cancellation and confirmation beat retries: stop the loop.
