# Hosted deployment runbook

1. Create one Serverspace vStack server in New Jersey (Standard): Ubuntu 24.04.2 x64,
   2 vCPU, 4 GB RAM, 80 GB SSD, 200 Mbps, one IPv4, Ed25519 SSH only. Name it
   `juma-prod-nj-01` and tag it `juma`, `production`.
2. Allow TCP 80/443 publicly and TCP 22 only from `JUMA_ADMIN_SSH_CIDR`; keep 4180,
   8000, and 8501 private. Apply unattended security updates and configure a host firewall.
3. Install pinned Docker Engine/Compose, clone this repository to `/opt/juma`, and create
   the six files under `secrets/` with mode 0600. The OAuth client ID is also set as the
   non-secret `OAUTH2_PROXY_CLIENT_ID` value in `.env`; the client secret file must contain
   only the secret (without a trailing newline), and the cookie secret file must contain
   exactly 32 random bytes. Register the GitHub OAuth app with callback
   `https://<JUMA_FQDN>/oauth2/callback` and allow only `JUMA_GITHUB_USERNAME`.
4. Copy `.env.example` to `.env`, set the FQDN and OAuth values, then run
   `docker compose build --pull` and `docker compose up -d`. Never mount the Docker socket
   into Juma containers.
5. Verify `curl -fsS https://<JUMA_FQDN>/health` and `/ready`, then smoke-test ask → approval →
   disposable check → apply. Confirm unauthenticated and non-allowlisted requests fail.
6. Run migrations with the API stopped, health-gate the new stack, and restore the previous
   image/config if readiness fails. Schedule `ops/backup.sh` every six hours; verify a restore
   monthly. Target RPO is six hours and RTO is two hours.

Container images must be digest-pinned in production. Keep automatic repair, commit, push,
deployment, arbitrary shell execution, and destructive cleanup disabled.
