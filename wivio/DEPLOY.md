# Wivio Server Deploy

This guide deploys the bot on a Linux server with Docker Compose in polling mode.

## 1. Connect To The Server

```bash
ssh root@SERVER_IP
```

Use your real server IP instead of `SERVER_IP`.

## 2. Install Docker

Ubuntu 24.04:

```bash
apt update
apt install -y ca-certificates curl git

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list

apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
```

Check:

```bash
docker --version
docker compose version
```

## 3. Put The Project On The Server

Recommended location:

```bash
mkdir -p /opt/wivio
cd /opt/wivio
```

Clone the repository:

```bash
git clone REPOSITORY_URL .
```

If the repository is already cloned:

```bash
cd /opt/wivio
git pull origin main
```

## 4. Create `.env`

```bash
cp .env.example .env
nano .env
```

Minimum required values:

```env
BOT_TOKEN=123456:replace-me
BOT_USERNAME=wivio_bot
UPLOAD_CHAT_ID=-1001234567890
BOT_MODE=polling
ADMIN_USER_IDS=986436438
```

For Telegram alerts:

```env
ALERTS_ENABLED=true
ALERT_CHAT_ID=-1001234567890
ALERT_MESSAGE_THREAD_ID=123
ALERT_LEVEL=ERROR
```

`ALERT_BOT_TOKEN` can be empty if alerts should use `BOT_TOKEN`.

## 5. Start The Bot

```bash
docker compose up -d --build
```

Check logs:

```bash
docker compose logs -f
```

Healthcheck:

```bash
curl http://127.0.0.1:8080/healthz
```

## 6. Stop Local Copies

Only one polling instance can run per Telegram bot token. Before checking production, stop the bot
on your laptop or in PyCharm. Otherwise Telegram will return:

```text
Conflict: terminated by other getUpdates request
```

## 7. Update Later

```bash
cd /opt/wivio
git pull origin main
docker compose up -d --build
docker compose logs -f
```

## 8. Back Up The Database

The SQLite database is stored in:

```text
/opt/wivio/data/bot.sqlite3
```

Create a backup:

```bash
mkdir -p /opt/wivio/backups
cp data/bot.sqlite3 /opt/wivio/backups/bot-$(date +%F-%H%M%S).sqlite3
```

## Useful Commands

```bash
docker compose ps
docker compose logs -f --tail=200
docker compose restart
docker compose down
docker compose up -d --build
```

Runtime folders are mounted on the host:

```text
data/
downloads/
logs/
```

Do not commit `.env`, `data/`, `downloads/`, or `logs/`.
