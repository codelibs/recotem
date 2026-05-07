# Tutorial example: purchase_log

Self-contained Recotem 2.0 tutorial recipe. Fetches a small public CSV
(≈37 KiB, ≈4 988 interactions) over HTTPS and trains an IALS + TopPop
recommender against it.

- Walkthrough: [docs/getting-started.md](../../docs/getting-started.md)
- Source data: `https://raw.githubusercontent.com/codelibs/recotem/refs/tags/v1.0.0/frontend/e2e/test_data/purchase_log.csv`
- sha256: `945fc769205a5976d38c5783500ae473afbb04608043b703951a699993c8f8be`

Run from the repository root:

```bash
mkdir -p artifacts
uv run recotem train examples/tutorial-purchase-log/recipe.yaml
```

The artifact is written to `./artifacts/purchase_log-<sha>.recotem` (the
`-<sha>` suffix is added by `versioning: append_sha`).
