# Deployment on a single VPS

Target: one Linux VM (4 vCPU, 8 GB RAM, 100 GB SSD is a good start), Docker Engine with the compose plugin,
a DNS name pointing to the VM. Only ports 80 and 443 are exposed; PostgreSQL and Redis stay inside the
compose network.

## 1. Prepare the host

```bash
# Ubuntu LTS
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # re-login
sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
```

## 2. Configure

```bash
mkdir -p ~/pcbis && cd ~/pcbis
curl -fsSLO https://raw.githubusercontent.com/Cheburek28/PCBInspectionService/main/compose.prod.yaml
mkdir -p docker && curl -fsSL -o docker/Caddyfile \
  https://raw.githubusercontent.com/Cheburek28/PCBInspectionService/main/docker/Caddyfile
curl -fsSL -o .env https://raw.githubusercontent.com/Cheburek28/PCBInspectionService/main/.env.example
$EDITOR .env      # PCBIS_DOMAIN, POSTGRES_PASSWORD (long random), PCBIS_VERSION, thresholds
chmod 600 .env
```

## 3. Start

```bash
docker compose -f compose.prod.yaml pull
docker compose -f compose.prod.yaml up -d --wait
curl https://$PCBIS_DOMAIN/health/ready
```

`migrate` runs automatically before `api` and `worker` start.

## 4. Create client keys

```bash
docker compose -f compose.prod.yaml exec api pcbis apikey create --station line-1
docker compose -f compose.prod.yaml exec api pcbis apikey list
docker compose -f compose.prod.yaml exec api pcbis apikey revoke <prefix>
```

## 5. Upgrade

```bash
sed -i 's/^PCBIS_VERSION=.*/PCBIS_VERSION=0.2.0/' .env
docker compose -f compose.prod.yaml pull && docker compose -f compose.prod.yaml up -d --wait
```

Migrations are forward-compatible within a minor version. Read the CHANGELOG before upgrading across minors.

## 6. Backups

- Database: the `backup` service writes `./backups/pcbis-<timestamp>.sql.gz` daily and keeps
  `BACKUP_KEEP_DAYS`. Copy `./backups` off the host (restic, rclone, provider snapshots).
- Files: the `blobdata` volume (`docker volume inspect pcbis_blobdata`) holds all photos — back it up too,
  or use `PCBIS_STORAGE_BACKEND=s3` with a bucket that has versioning.

Restore:

```bash
gunzip -c backups/pcbis-YYYYMMDD-HHMMSS.sql.gz | \
  docker compose -f compose.prod.yaml exec -T postgres psql -U pcbis pcbis
```

## 7. Sizing

| Load | Suggestion |
|---|---|
| ≤ 1 board side every 10 s | 2 worker processes (default) on 4 vCPU |
| more | raise `PCBIS_WORKER_CONCURRENCY` (≈ 1 per 2 vCPU, ~1 GB RAM each) or run more `worker` replicas |

Disk: ≈ 4 MB per inspected side at 3000 px (photo + aligned image + crops).
