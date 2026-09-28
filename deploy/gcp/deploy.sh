#!/usr/bin/env bash
# Deploy TrustMeBank to a single Compute Engine VM behind Caddy (automatic HTTPS).
#
# Run from anywhere inside the repository after `gcloud auth login` and
# `gcloud config set project <project>`. Safe to re-run: it creates what is missing and
# re-uploads the current commit to an existing VM.
#
#   PROJECT   Google Cloud project (default: gcloud config)
#   ZONE      default africa-south1-a (Johannesburg)
#   MACHINE   default e2-small
#   NAME      VM and address name, default trustmebank
#   DOMAIN    hostname for HTTPS. Default: trustmebank.<ip-with-dashes>.sslip.io, which needs
#             no DNS setup. For a class, point a real name at the static IP and pass DOMAIN.
set -euo pipefail

REPO_ROOT=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
PROJECT=${PROJECT:-$(gcloud config get-value project 2>/dev/null)}
ZONE=${ZONE:-africa-south1-a}
REGION=${ZONE%-*}
MACHINE=${MACHINE:-e2-small}
NAME=${NAME:-trustmebank}

if [ -z "$PROJECT" ]; then
  echo "No project. Run: gcloud config set project <project-id>" >&2
  exit 1
fi
G="gcloud --project=$PROJECT --quiet"

echo "==> Project $PROJECT, zone $ZONE, VM $NAME ($MACHINE)"
$G services enable compute.googleapis.com >/dev/null

if ! $G compute addresses describe "$NAME" --region="$REGION" >/dev/null 2>&1; then
  echo "==> Reserving static IP"
  $G compute addresses create "$NAME" --region="$REGION" >/dev/null
fi
IP=$($G compute addresses describe "$NAME" --region="$REGION" --format='value(address)')
DOMAIN=${DOMAIN:-trustmebank.${IP//./-}.sslip.io}
echo "==> IP $IP, domain $DOMAIN"

if ! $G compute firewall-rules describe "$NAME-web" >/dev/null 2>&1; then
  echo "==> Opening ports 80 and 443"
  $G compute firewall-rules create "$NAME-web" --allow=tcp:80,tcp:443 \
    --target-tags="$NAME" --source-ranges=0.0.0.0/0 >/dev/null
fi

if ! $G compute instances describe "$NAME" --zone="$ZONE" >/dev/null 2>&1; then
  echo "==> Creating VM"
  $G compute instances create "$NAME" --zone="$ZONE" --machine-type="$MACHINE" \
    --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=20GB \
    --address="$IP" --tags="$NAME" >/dev/null
fi

echo "==> Waiting for SSH"
for _ in $(seq 1 30); do
  if $G compute ssh "$NAME" --zone="$ZONE" --command=true >/dev/null 2>&1; then break; fi
  sleep 5
done

echo "==> Uploading commit $(git -C "$REPO_ROOT" rev-parse --short HEAD)"
BUNDLE=$(mktemp -t trustmebank-XXXX.tgz)
trap 'rm -f "$BUNDLE"' EXIT
git -C "$REPO_ROOT" archive --format=tar.gz -o "$BUNDLE" HEAD
$G compute scp "$BUNDLE" "$REPO_ROOT/deploy/gcp/install.sh" "$NAME:/tmp/" --zone="$ZONE" >/dev/null

echo "==> Installing on the VM (first run takes a few minutes)"
$G compute ssh "$NAME" --zone="$ZONE" --command="sudo DOMAIN='$DOMAIN' BUNDLE='/tmp/$(basename "$BUNDLE")' bash /tmp/install.sh"

ADMIN_PASSWORD=$($G compute ssh "$NAME" --zone="$ZONE" --command="sudo grep '^ADMIN_PASSWORD=' /opt/trustmebank/.env | cut -d= -f2-")

echo "==> Waiting for HTTPS at https://$DOMAIN (certificate issuance can take a minute)"
for _ in $(seq 1 24); do
  if curl -fsS -o /dev/null "https://$DOMAIN/health"; then break; fi
  sleep 5
done
curl -fsS "https://$DOMAIN/health" && echo

cat <<EOF

TrustMeBank is up.

  Customer UI   https://$DOMAIN            alice@example.com / alice123
  Admin         https://$DOMAIN/admin      password: $ADMIN_PASSWORD
  Swagger       https://$DOMAIN/docs
  Student guide https://$DOMAIN/guide

Redeploy after changes:  deploy/gcp/deploy.sh
Logs:                    gcloud compute ssh $NAME --zone=$ZONE -- sudo docker compose -f /opt/trustmebank/docker-compose.yml logs -f app
Shell on the VM:         gcloud compute ssh $NAME --zone=$ZONE
Delete everything:       gcloud compute instances delete $NAME --zone=$ZONE && gcloud compute addresses delete $NAME --region=$REGION
EOF
