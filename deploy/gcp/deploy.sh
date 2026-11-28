#!/usr/bin/env bash
# Deploy the hub to one Compute Engine VM (Docker Compose: hub + Caddy for HTTPS).
# Idempotent: re-run it to ship new code. Data lives in a Docker volume on the VM's
# persistent boot disk, snapshotted daily. Works from macOS/Linux and Git Bash on Windows.
#
#   PROJECT=my-gcp-project ./deploy/gcp/deploy.sh --check   # preflight only, creates nothing
#   PROJECT=my-gcp-project ./deploy/gcp/deploy.sh           # create/update everything
#
# Optional: ZONE (asia-south1-a), NAME (hackathon-hub), MACHINE (e2-medium),
#   DOMAIN (default <static-ip>.sslip.io; for your own domain, point an A record at the IP first),
#   EVENT_NAME, EVENT_START, EVENT_END (ISO, e.g. 2026-12-05T09:00:00+05:30), UTC_OFFSET_MINUTES (330),
#   REGISTRATION_CODE (default: generated). Secrets are kept in deploy/gcp/.env.hub (git-ignored, 600).
set -euo pipefail

: "${PROJECT:?Set PROJECT to your GCP project id (deliberately not taken from gcloud config)}"
ZONE="${ZONE:-asia-south1-a}"
REGION="${ZONE%-*}"
NAME="${NAME:-hackathon-hub}"
MACHINE="${MACHINE:-e2-medium}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SECRETS="$HERE/.env.hub"
G=(--project "$PROJECT" --quiet)
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
# gcloud on Windows is a native program: give it Windows paths.
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
PY="$(command -v python3 || command -v python || true)"

# ---------------------------------------------------------------- preflight
say "Preflight"
command -v gcloud >/dev/null || die "gcloud not found. Install the Google Cloud CLI."
[ -n "$PY" ] || die "python not found (used to generate secrets and validate times)."
ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n1)"
[ -n "$ACCOUNT" ] || die "Not logged in. Run: gcloud auth login"
gcloud projects describe "$PROJECT" --format='value(projectId)' >/dev/null 2>&1 \
  || die "Project '$PROJECT' not found or no access for the active account."
BILLING="$(gcloud billing projects describe "$PROJECT" --format='value(billingEnabled)' 2>/dev/null || echo unknown)"
[ "$BILLING" = "False" ] && die "Billing is not enabled on '$PROJECT'."
for t in EVENT_START EVENT_END; do
  v="${!t:-}"
  [ -z "$v" ] || "$PY" -c "import sys,datetime; datetime.datetime.fromisoformat(sys.argv[1].replace('Z','+00:00'))" "$v" \
    || die "$t='$v' is not an ISO time (e.g. 2026-12-05T09:00:00+05:30)."
done
for f in server_7400.py Dockerfile hub/board.py static/index.html deploy/gcp/docker-compose.yml deploy/gcp/Caddyfile; do
  [ -f "$ROOT/$f" ] || die "missing $f"
done
ENABLED="$(gcloud services list --enabled --project "$PROJECT" --format='value(config.name)' 2>/dev/null || true)"
for api in compute.googleapis.com iap.googleapis.com; do
  grep -qx "$api" <<<"$ENABLED" && echo "  api $api: enabled" || echo "  api $api: will be enabled"
done
echo "  account: active   project: $PROJECT   billing: $BILLING   zone: $ZONE   machine: $MACHINE"
if gcloud compute instances describe "$NAME" --zone "$ZONE" --project "$PROJECT" >/dev/null 2>&1; then
  echo "  VM '$NAME' exists: this run will update it in place"
else
  echo "  VM '$NAME' does not exist yet: this run will create it"
fi
if [ "$CHECK_ONLY" = 1 ]; then
  say "Preflight passed. Nothing was created."
  exit 0
fi

# ------------------------------------------------------------------ infra
say "APIs"
gcloud services enable compute.googleapis.com iap.googleapis.com "${G[@]}"

say "Static IP"
if ! gcloud compute addresses describe "$NAME-ip" --region "$REGION" "${G[@]}" >/dev/null 2>&1; then
  gcloud compute addresses create "$NAME-ip" --region "$REGION" "${G[@]}"
fi
IP="$(gcloud compute addresses describe "$NAME-ip" --region "$REGION" --format='value(address)' "${G[@]}")"
DOMAIN="${DOMAIN:-$IP.sslip.io}"
echo "IP $IP -> https://$DOMAIN"

say "Firewall (80/443 public; SSH only through IAP)"
if ! gcloud compute firewall-rules describe "$NAME-web" "${G[@]}" >/dev/null 2>&1; then
  gcloud compute firewall-rules create "$NAME-web" --allow tcp:80,tcp:443 --target-tags "$NAME" "${G[@]}"
fi
if ! gcloud compute firewall-rules describe "$NAME-iap-ssh" "${G[@]}" >/dev/null 2>&1; then
  gcloud compute firewall-rules create "$NAME-iap-ssh" --allow tcp:22 --source-ranges 35.235.240.0/20 \
    --target-tags "$NAME" "${G[@]}"
fi

say "VM"
if ! gcloud compute instances describe "$NAME" --zone "$ZONE" "${G[@]}" >/dev/null 2>&1; then
  gcloud compute instances create "$NAME" --zone "$ZONE" --machine-type "$MACHINE" \
    --image-family debian-12 --image-project debian-cloud --boot-disk-size 20GB --boot-disk-type pd-balanced \
    --address "$IP" --tags "$NAME" --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --metadata-from-file startup-script="$(native "$HERE/startup.sh")" "${G[@]}"
fi

say "Daily disk snapshots (7 days kept)"
if ! gcloud compute resource-policies describe "$NAME-daily" --region "$REGION" "${G[@]}" >/dev/null 2>&1; then
  gcloud compute resource-policies create snapshot-schedule "$NAME-daily" --region "$REGION" \
    --daily-schedule --start-time 20:00 --max-retention-days 7 "${G[@]}"
  gcloud compute disks add-resource-policies "$NAME" --zone "$ZONE" --resource-policies "$NAME-daily" "${G[@]}"
fi

# ---------------------------------------------------------------- release
say "Secrets ($SECRETS)"
if [ ! -f "$SECRETS" ]; then
  ( umask 077
    printf 'HUB_ADMIN_TOKEN=%s\nHUB_REGISTRATION_CODE=%s\n' \
      "$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')" \
      "${REGISTRATION_CODE:-$("$PY" -c 'import secrets; print(secrets.token_hex(3).upper())')}" > "$SECRETS" )
  echo "Created. Share HUB_REGISTRATION_CODE with participants; keep HUB_ADMIN_TOKEN with organisers."
else
  echo "Reusing existing secrets."
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
( umask 077
  cat "$SECRETS"
  printf 'HUB_DOMAIN=%s\nHUB_PUBLIC_URL=https://%s\n' "$DOMAIN" "$DOMAIN"
  printf 'HUB_EVENT_NAME=%s\nHUB_EVENT_START=%s\nHUB_EVENT_END=%s\nHUB_UTC_OFFSET_MINUTES=%s\n' \
    "${EVENT_NAME:-Catalyst}" "${EVENT_START:-}" "${EVENT_END:-}" "${UTC_OFFSET_MINUTES:-330}"
) > "$STAGE/env"
tar -czf "$STAGE/app.tgz" --exclude=__pycache__ -C "$ROOT" server_7400.py hub static Dockerfile .dockerignore \
  deploy/gcp/docker-compose.yml deploy/gcp/Caddyfile

say "Waiting for Docker on the VM (first boot takes 2-3 minutes)"
SSH=(gcloud compute ssh "$NAME" --zone "$ZONE" --tunnel-through-iap "${G[@]}")
ready=0
for _ in $(seq 40); do
  if "${SSH[@]}" --command "docker compose version" >/dev/null 2>&1; then ready=1; break; fi
  sleep 10
done
[ "$ready" = 1 ] || die "Docker did not come up on the VM. Check: gcloud compute instances get-serial-port-output $NAME --zone $ZONE --project $PROJECT"

say "Shipping code"
gcloud compute scp "$(native "$STAGE/app.tgz")" "$(native "$STAGE/env")" "$NAME:/tmp/" \
  --zone "$ZONE" --tunnel-through-iap "${G[@]}"
"${SSH[@]}" --command "set -e; sudo mkdir -p /opt/hub && sudo tar -xzf /tmp/app.tgz -C /opt/hub && rm /tmp/app.tgz; sudo install -m 600 /tmp/env /opt/hub/deploy/gcp/.env && rm /tmp/env; cd /opt/hub/deploy/gcp && sudo docker compose up -d --build --remove-orphans && sudo docker image prune -f >/dev/null"

say "Health check over HTTPS (certificate issuance can take ~30 s)"
ok=0
for _ in $(seq 24); do
  if curl -fsS --max-time 10 "https://$DOMAIN/healthz" >/dev/null 2>&1; then ok=1; break; fi
  sleep 5
done
[ "$ok" = 1 ] && echo "https://$DOMAIN/healthz OK" || echo "WARNING: https://$DOMAIN not answering yet; check 'docker compose logs caddy' on the VM."

say "Done"
echo "Leaderboard: https://$DOMAIN    Stage: https://$DOMAIN/stage    Join: https://$DOMAIN/join"
