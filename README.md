# Cloudhand Control Plane (API + Plan/Apply)

This repo is a **control-plane** you deploy onto a single Hetzner (or any Linux) server.
It exposes an HTTP API that:
- stores Cloudhand plans on disk (`./cloudhand/plan-*.json`)
- lets you **patch/upsert a workload** inside a plan by workload name
- runs **`cloudhand` (Terraform + SSH deploy)** to provision servers and deploy workloads
- configures **nginx + Let's Encrypt certs** on the workload servers (one domain per workload)

## What’s included

- `cloudhand-api/` – FastAPI backend
- `src/cloudhand/` – Cloudhand engine (Terraform generator + SSH deployer + CLI)
- `deploy/` – docker-compose, systemd, nginx templates, examples, setup script

## Security note

Do **not** commit real secrets into git.
This repo ships only `.env.example`.

---

# Quickstart on a Hetzner “control-plane” server

## 1) Create the control-plane server
- Ubuntu 22.04 is a good default.
- Open inbound:
  - `22/tcp` (SSH)
  - `80/tcp`, `443/tcp` (optional: if you reverse-proxy the API via nginx)
  - `8000/tcp` (only if you expose uvicorn directly; not recommended)

## 2) Clone this repo onto the server
Recommended path:

```bash
sudo mkdir -p /opt/cloudhand-control-plane
sudo chown -R $USER:$USER /opt/cloudhand-control-plane
cd /opt/cloudhand-control-plane

# clone your git repo here (contents of this zip)
```

## 3) Run the bootstrap script (installs deps, starts Postgres, creates systemd service)
Run as root:

```bash
sudo bash deploy/scripts/setup_control_server.sh /opt/cloudhand-control-plane
```

The first run creates:

- `/opt/cloudhand-control-plane/cloudhand-api/.env` (copied from `.env.example`)
- Postgres container (docker compose)

Then it **stops** and tells you to edit `.env`.

## 4) Edit `.env`
Edit:

```bash
sudo nano /opt/cloudhand-control-plane/cloudhand-api/.env
```

Minimum you should set:
- `CLOUDHAND_API_KEY` (so you can call the API from a CLI without cookies)
- `CERTBOT_EMAIL` (Let’s Encrypt registration)
- `OPENAI_API_KEY` (only needed if you want LLM plan generation via `ch plan`)
- `DATABASE_URL` (defaults match the docker-compose Postgres)

## 5) Re-run the bootstrap script
```bash
sudo bash deploy/scripts/setup_control_server.sh /opt/cloudhand-control-plane
```

This time it will:
- create the Python venv
- install dependencies
- run Alembic migrations
- install + start `cloudhand-api` systemd service on `127.0.0.1:8000`

Check status:

```bash
sudo systemctl status cloudhand-api --no-pager
```

---

# API authentication (recommended)

Set in `.env`:

```
CLOUDHAND_API_KEY=some-long-random-token
```

Then send:

- `X-API-Key: some-long-random-token`

This avoids GitHub OAuth during headless usage.

---

# Workflow: deploy initial workload, then add/patch workload, then redeploy

## 0) Configure provider (Hetzner token)
```bash
curl -X POST "http://127.0.0.1:8000/api/onboarding/provider" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $CLOUDHAND_API_KEY" \
  -d '{
    "provider": "hetzner",
    "token": "HCLOUD_xxx",
    "project": "landing-pages"
  }'
```

This writes a local `ch.yaml` + `cloudhand/secrets.json` and validates the token via a scan.

## 1) Save your initial plan
Use the example:

- `deploy/examples/plan-single-workload.json`

Save it:

```bash
PLAN_JSON="$(cat deploy/examples/plan-single-workload.json)"
curl -X POST "http://127.0.0.1:8000/api/applications/plans" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $CLOUDHAND_API_KEY" \
  -d "$(jq -n --arg content "$PLAN_JSON" '{content: $content}')"
```

Response includes a `path` like `.../cloudhand/plan-2026-...json`.

## 2) Apply that plan (CLI-like)
```bash
curl -X POST "http://127.0.0.1:8000/api/applications/plans/apply" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $CLOUDHAND_API_KEY" \
  -d '{}'
```

It returns:
- `logs`
- `server_ips` (terraform output)
- `live_url`

### DNS + HTTPS best practice (important)
Let’s Encrypt will only issue certs if:
- DNS A record points at the server IP
- port 80 is reachable from the internet

So do this:
1) First apply with `https: false`
2) Set DNS
3) Patch workload to `https: true`
4) Apply again

## 3) Patch an existing workload (by name)
Post only the workload object (patch). Example:

```bash
curl -X POST "http://127.0.0.1:8000/api/applications/plans/workloads?in_place=false" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $CLOUDHAND_API_KEY" \
  -d '{
    "name": "honest-herbalist",
    "service_config": {
      "server_names": ["thehonestherbalist.com"],
      "https": true
    }
  }'
```

This:
- finds the workload by `name`
- deep-merges your patch into it
- validates the final workload against `ApplicationSpec`
- writes a new plan file (unless `in_place=true`)

Then apply again:

```bash
curl -X POST "http://127.0.0.1:8000/api/applications/plans/apply" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $CLOUDHAND_API_KEY" \
  -d '{"plan_path": "/opt/cloudhand-control-plane/cloudhand/plan-....json"}'
```

## 4) Add a new workload (same server) and redeploy
Key rule: **ports must not collide**.

To add a workload if it doesn’t exist yet:

```bash
curl -X POST "http://127.0.0.1:8000/api/applications/plans/workloads?create_if_missing=true&instance_name=ubuntu-4gb-nbg1-1" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $CLOUDHAND_API_KEY" \
  -d '{
    "name": "second-site",
    "repo_url": "https://github.com/you/second-repo",
    "branch": "main",
    "runtime": "nodejs",
    "build_config": {
      "install_command": "npm install",
      "build_command": "npm run build",
      "system_packages": []
    },
    "service_config": {
      "command": "npm run start",
      "environment": {"PORT": "3001"},
      "ports": [3001],
      "server_names": ["second.example.com"],
      "https": false
    },
    "destination_path": "/opt/apps"
  }'
```

Then:
1) apply
2) set DNS A record for `second.example.com` to the **same server IP**
3) patch that workload to `https: true`
4) apply again

---

# Nginx + certificates on workload servers

Default behavior is **one nginx site per workload**:
- config: `/etc/nginx/sites-available/<workload-name>`
- enabled via symlink in `sites-enabled/`
- certbot runs automatically when `service_config.https=true`

You can switch to the old “combined” nginx routing mode (path-based) by setting on the control-plane:

```
CLOUDHAND_NGINX_MODE=combined
```

---

# “Project label” / folder question

You do **not** need to manually create a `landing-pages/` folder for the API to work.

The `project` value you pass to `/api/onboarding/provider` is written into `ch.yaml` and used by the Cloudhand CLI to scope **SSH keys** (each project gets its own keypair). Plans + terraform state are still stored under `./cloudhand/` unless you isolate projects (see below).

If you want strict isolation between multiple projects, the easiest approach is:
- run one control-plane per project (separate clone + separate systemd service), or
- extend the code to store terraform state + plans per workspace (not included here).

---

# Example files

- `deploy/examples/plan-single-workload.json`
- `deploy/examples/plan-two-workloads-same-server.json`
- `deploy/examples/workload-patch.json`

