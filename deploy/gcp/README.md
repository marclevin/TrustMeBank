# Deploying to Google Cloud

One Compute Engine VM runs PostgreSQL and the app under Docker Compose, with Caddy in front
for automatic HTTPS. This matches the app's single-process design (in-memory rate limiter,
in-process webhook worker), so do not run more than one instance.

## First deployment

```bash
gcloud auth login
gcloud config set project <your-project-id>     # billing must be enabled
deploy/gcp/deploy.sh
```

The script reserves a static IP, opens ports 80 and 443, creates an `e2-small` Debian 12 VM in
`africa-south1-a`, uploads the current commit, installs Docker and Caddy, generates secrets
into `/opt/trustmebank/.env`, starts the stack and prints the URLs and admin password.

Without a domain it serves on `trustmebank.<ip-with-dashes>.sslip.io`. For a class, point a
real DNS name at the printed IP and run `DOMAIN=bank.example.ac.za deploy/gcp/deploy.sh`.

## Redeploying

Commit your changes, then run `deploy/gcp/deploy.sh` again. It keeps `.env` and the database
volume; migrations run on start.

## Operating

```bash
gcloud compute ssh trustmebank --zone=africa-south1-a
cd /opt/trustmebank
sudo docker compose logs -f app
sudo docker compose exec app tmb check-ledger
sudo docker compose exec app tmb reset-activity
sudo docker compose exec db pg_dump -U trustmebank trustmebank > backup-$(date +%F).sql
```

Webhooks from the VM cannot reach a student's laptop. Teams should poll payment status,
expose their app with a tunnel such as cloudflared or ngrok, or deploy it.

## Cost

An `e2-small` with a 20 GB disk and a static IP is roughly USD 15 to 20 per month. Delete
the VM and the address when the course ends:

```bash
gcloud compute instances delete trustmebank --zone=africa-south1-a
gcloud compute addresses delete trustmebank --region=africa-south1
```
