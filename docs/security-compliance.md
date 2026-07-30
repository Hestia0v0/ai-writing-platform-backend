# Security and Compliance Operations

This repository includes an auditable security/compliance pipeline in
`.github/workflows/security-compliance.yml`.

## Coverage

- **SAST rescan**: Bandit + Semgrep (`artifacts/sast`)
- **DAST rescan**: OWASP ZAP baseline (`artifacts/dast`)
- **IaC rescan**: Checkov for Dockerfile/GitHub Actions/secrets (`artifacts/iac`)
- **Git audit traceability**: structured commit history artifact (`artifacts/audit`)
- **Compliance evidence**: generated report mapped to SOC2/HIPAA/GDPR controls
  (`artifacts/compliance`)

## What "successful" looks like

All jobs in the `Security Compliance` workflow must be green:

1. `SAST Rescan (Bandit + Semgrep)` has 0 findings.
2. `IaC Rescan (Checkov)` has 0 failed checks.
3. `DAST Rescan (OWASP ZAP Baseline)` has 0 alerts.
4. `Git Audit Trail` generates both JSON and Markdown artifacts.
5. `Compliance Evidence (SOC2/HIPAA/GDPR)` status is `pass`.

### SAST gate policy (implemented)

- Bandit blocks only `MEDIUM/HIGH` severity findings with `MEDIUM/HIGH` confidence.
- Semgrep blocks only findings with severity `ERROR`.
- Warning/audit findings are still preserved in artifacts for manual triage.

## Manual local checks

### SAST

```bash
pip install bandit semgrep
bandit -r backend
semgrep scan --config auto backend tests
```

### IaC

```bash
pip install checkov
checkov -d . --framework dockerfile,github_actions,secrets
```

### DAST

```bash
cd infrastructure
cp .env.example .env
docker compose up -d --wait
docker run --rm --add-host=host.docker.internal:host-gateway \
  -v "${PWD}/..:/zap/wrk" ghcr.io/zaproxy/zaproxy:stable \
  zap-baseline.py -t http://host.docker.internal:8000 -m 3
docker compose down -v
```

### Git audit evidence

```bash
python scripts/git_audit_report.py \
  --json-out artifacts/audit/git-audit-report.json \
  --md-out artifacts/audit/git-audit-report.md
```

## Compliance evidence generation

After scan artifacts are available under `artifacts/`, run:

```bash
python scripts/compliance_evidence.py \
  --artifacts-dir artifacts \
  --json-out artifacts/compliance/compliance-evidence.json \
  --md-out artifacts/compliance/compliance-evidence.md
```

The script exits non-zero if unresolved findings remain.
