# WireGuard QR Manager

A FastAPI web service for generating and managing WireGuard VPN access QR codes. Create, manage, and distribute WireGuard peer configurations through a mobile-friendly web interface.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-green.svg)

## Features

- **One-Click Peer Creation** - Generate new WireGuard peer configurations instantly
- **QR Code Generation** - Scan-ready QR codes for WireGuard mobile apps
- **Live Status Monitoring** - See which peers are currently connected
- **Data Transfer Stats** - Track upload/download for each peer
- **Enable/Disable Peers** - Toggle peer access without deleting
- **Mobile-Responsive UI** - Card-based design works great on phones
- **HTTPS Ready** - Nginx reverse proxy configuration included
- **Multi-User Auth** - Basic authentication for multiple users

## Screenshots

The interface shows:
- Stats bar: Total peers, Online count, Enabled count, Scan count
- Card grid with QR placeholder, name, IP, transfer stats, last seen
- Green/Red toggle button showing enabled/disabled state
- "DISABLED" overlay on inactive peer cards
- Modal with full QR code for scanning

## Requirements

- Ubuntu 20.04+ / Debian 11+ (or compatible Linux)
- Python 3.8+
- WireGuard installed and configured
- Root access (for WireGuard commands)
- Nginx (for HTTPS reverse proxy)

---

## Quick Installation

```bash
# 1. Extract the archive
unzip wireguard-qr-manager.zip
cd wireguard-qr-manager

# 2. Run setup script (as root)
sudo bash setup.sh
```

The setup script will:
1. Install dependencies (Python, WireGuard tools, qrencode, Nginx)
2. Create Python virtual environment
3. Auto-detect WireGuard configuration
4. Setup systemd service
5. Configure Nginx with SSL (Let's Encrypt)

---

## Manual Installation

### Step 1: Install System Dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv wireguard-tools qrencode nginx certbot python3-certbot-nginx apache2-utils
```

### Step 2: Setup Application

```bash
# Create installation directory
sudo mkdir -p /opt/wireguard-qr-manager
sudo cp -r ./* /opt/wireguard-qr-manager/
cd /opt/wireguard-qr-manager

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt
```

### Step 3: Configure WireGuard Server

If WireGuard is not yet configured:

```bash
# Generate server keys
sudo mkdir -p /etc/wireguard
wg genkey | sudo tee /etc/wireguard/privatekey | wg pubkey | sudo tee /etc/wireguard/publickey
sudo chmod 600 /etc/wireguard/privatekey

# Get your main network interface
ip route | grep default | awk '{print $5}'  # e.g., eth0

# Create server config
sudo nano /etc/wireguard/wg0.conf
```

Paste this (replace `eth0` with your interface and add your private key):

```ini
[Interface]
Address = 10.10.0.1/24
ListenPort = 51820
PrivateKey = YOUR_PRIVATE_KEY_HERE

PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
```

Enable IP forwarding and start WireGuard:

```bash
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
sudo systemctl enable wg-quick@wg0
sudo systemctl start wg-quick@wg0
```

### Step 4: Configure Environment

```bash
cp .env.example .env
nano .env
```

**Required settings:**

```env
WG_SERVER_PUBLIC_KEY=your_wireguard_public_key
WG_SERVER_ENDPOINT=your.domain.com:51820
```

Get your public key:

```bash
sudo cat /etc/wireguard/publickey
```

### Step 5: Setup Systemd Service

```bash
sudo cp wireguard-qr-manager.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable wireguard-qr-manager
sudo systemctl start wireguard-qr-manager
```

### Step 6: Configure Nginx (HTTPS)

```bash
# Create HTTP config first
sudo tee /etc/nginx/sites-available/wireguard-qr-manager << 'EOF'
server {
    listen 80;
    server_name your.domain.com;
    
    auth_basic "WireGuard Manager";
    auth_basic_user_file /etc/nginx/.htpasswd;
    
    location / {
        proxy_pass http://127.0.0.1:6000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    location /health {
        auth_basic off;
        proxy_pass http://127.0.0.1:6000/health;
    }
}
EOF

# Enable site
sudo ln -sf /etc/nginx/sites-available/wireguard-qr-manager /etc/nginx/sites-enabled/

# Create admin user
sudo htpasswd -c /etc/nginx/.htpasswd admin

# Test and reload
sudo nginx -t && sudo systemctl reload nginx

# Setup SSL
sudo certbot --nginx -d your.domain.com
```

---

## User Management

### Add Users

```bash
# Add additional users (don't use -c flag)
sudo htpasswd /etc/nginx/.htpasswd username
```

### List Users

```bash
cat /etc/nginx/.htpasswd
```

### Delete User

```bash
sudo htpasswd -D /etc/nginx/.htpasswd username
```

### Change Password

```bash
sudo htpasswd /etc/nginx/.htpasswd existinguser
```

---

## Configuration Reference

| Variable | Description | Default |
|----------|-------------|---------|
| `WG_INTERFACE` | WireGuard interface name | `wg0` |
| `WG_SERVER_PUBLIC_KEY` | Server's public key | **Required** |
| `WG_SERVER_ENDPOINT` | Server hostname:port | **Required** |
| `WG_DNS` | DNS servers for clients | `1.1.1.1, 1.0.0.1` |
| `WG_ALLOWED_IPS` | Routed IPs for clients | `0.0.0.0/0, ::/0` |
| `WG_SUBNET` | First 3 octets of client range | `10.10.0` |
| `WG_START_IP` | Starting client IP (.X) | `10` |
| `DATABASE_URL` | SQLite database path | `sqlite:///./wireguard_peers.db` |
| `APP_PORT` | API listen port | `6000` |

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Web UI |
| `GET` | `/api/peers` | List all peers with stats |
| `POST` | `/api/peers` | Create new peer |
| `GET` | `/api/peers/{id}` | Get peer details + QR code |
| `DELETE` | `/api/peers/{id}` | Delete peer |
| `POST` | `/api/peers/{id}/toggle` | Enable/disable peer |
| `GET` | `/api/stats` | Live connection stats |
| `GET` | `/health` | Health check |

---

## Service Commands

```bash
# Start service
sudo systemctl start wireguard-qr-manager

# Stop service
sudo systemctl stop wireguard-qr-manager

# Restart service
sudo systemctl restart wireguard-qr-manager

# View status
sudo systemctl status wireguard-qr-manager

# View logs
sudo journalctl -u wireguard-qr-manager -f

# View last 50 log lines
sudo journalctl -u wireguard-qr-manager -n 50 --no-pager
```

---

## Troubleshooting

### Database Migration Error

If you see "no such column" errors after updating:

```bash
python3 << 'EOF'
import sqlite3
conn = sqlite3.connect('/opt/wireguard-qr-manager/wireguard_peers.db')
cursor = conn.cursor()
try: cursor.execute("ALTER TABLE peers ADD COLUMN total_rx INTEGER DEFAULT 0")
except: pass
try: cursor.execute("ALTER TABLE peers ADD COLUMN total_tx INTEGER DEFAULT 0")
except: pass
try: cursor.execute("ALTER TABLE peers ADD COLUMN last_handshake DATETIME")
except: pass
conn.commit()
conn.close()
print("Done!")
EOF
sudo systemctl restart wireguard-qr-manager
```

### Peers Not Connecting

```bash
# Check WireGuard status
sudo wg show

# Check if peer was added
sudo wg show wg0 peers

# Check firewall
sudo ufw status
sudo ufw allow 51820/udp
```

### Service Won't Start

```bash
# Check logs
sudo journalctl -u wireguard-qr-manager -n 50

# Common issues:
# - Missing WG_SERVER_PUBLIC_KEY in .env
# - WireGuard not running
# - Port 6000 already in use
```

### Online Status Not Updating

- WireGuard shows a peer as "online" if the last handshake was within ~2 minutes
- When a peer disconnects, it takes up to 2 minutes for status to change
- Use the refresh button or wait for auto-refresh (every 15 seconds)

---

## Firewall Setup

```bash
sudo ufw allow 51820/udp  # WireGuard
sudo ufw allow 443/tcp    # HTTPS
sudo ufw allow 80/tcp     # HTTP (for Let's Encrypt)
sudo ufw enable
```

---

## Security Notes

1. **Always use HTTPS** - QR codes contain private keys!
2. **Enable basic auth** - Protect the web interface
3. **Limit access** - Consider IP whitelisting in Nginx
4. **Regular backups** - Backup the SQLite database
5. **Keep updated** - Update system packages regularly

---

## License

MIT License - feel free to use and modify.

---

## Files Structure

```
wireguard-qr-manager/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application
│   ├── templates/
│   │   └── index.html       # Web UI template
│   └── static/
├── nginx/
│   └── wireguard-qr-manager.conf
├── requirements.txt
├── setup.sh                 # Automated setup script
├── setup-wireguard-server.sh # WireGuard server setup
├── wireguard-qr-manager.service
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── README.md
```
