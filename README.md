# Auto Confirm Pinterest (Flask + IMAP worker)

**Purpose:** Poll an IMAP mailbox and automatically follow Pinterest confirmation links (emails sent to accounts you control).

**Important:** Use only with mailboxes and accounts you own. Do not use for abuse or to bypass terms of service.

## Files
- `app.py` - Flask app and background worker poller.
- `requirements.txt` - Python dependencies.
- `Dockerfile` - Container image build file.
- `docker-compose.yml` - Example for running locally (includes IMAP_PASS set to the value you provided).
- `.env` - Environment variables (contains the IMAP password you provided). Remove or secure this for production.

## Quick start (VPS / Docker)
1. Copy the repo to your VPS or upload this project.
2. (Optional) Edit `.env` to secure credentials or change settings.
3. Build and run with Docker:
   ```bash
   docker build -t auto-confirm-pinterest .
   docker run -d --env-file .env -p 5000:5000 --name auto-confirm auto-confirm-pinterest
   ```
4. Open `http://your-vps-ip:5000/status` to see worker status.

## Coolify
- Coolify can deploy a Docker image from a Git repo or Dockerfile. Push this project to a git repo and configure a service in Coolify using the provided Dockerfile.

## Security notes
- Storing secrets in `.env` is convenient but not secure. Use a secret manager or Coolify's secret/env settings.
- Polling every 1 second is aggressive; consider increasing `POLL_INTERVAL` or using inbound webhooks if available.
