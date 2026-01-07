#!/bin/bash
# WireGuard QR Manager - Installation Script
# Run as root: sudo bash setup.sh

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() { echo -e "${BLUE}[*]${NC} $1"; }
print_success() { echo -e "${GREEN}[✓]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }

# Check root
if [[ $EUID -ne 0 ]]; then
   print_error "This script must be run as root (sudo bash setup.sh)"
   exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         WireGuard QR Manager - Installation Script           ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Installation directory
INSTALL_DIR="/opt/wireguard-qr-manager"

# Step 1: Install system dependencies
print_status "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv wireguard-tools qrencode nginx certbot python3-certbot-nginx apache2-utils

print_success "System dependencies installed"

# Step 2: Create installation directory
print_status "Setting up installation directory..."
mkdir -p $INSTALL_DIR
cp -r ./* $INSTALL_DIR/ 2>/dev/null || true

print_success "Files copied to $INSTALL_DIR"

# Step 3: Create Python virtual environment
print_status "Creating Python virtual environment..."
cd $INSTALL_DIR
python3 -m venv venv
source venv/bin/activate

print_success "Virtual environment created"

# Step 4: Install Python dependencies
print_status "Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

print_success "Python dependencies installed"

# Step 5: Create directories
print_status "Creating required directories..."
mkdir -p $INSTALL_DIR/app/static
mkdir -p $INSTALL_DIR/app/templates

print_success "Directories created"

# Step 6: Configure environment
if [ ! -f "$INSTALL_DIR/.env" ]; then
    print_status "Creating configuration file..."
    
    # Try to auto-detect WireGuard settings
    WG_INTERFACE="wg0"
    WG_PUBLIC_KEY=""
    WG_ENDPOINT=""
    
    if command -v wg &> /dev/null; then
        if wg show $WG_INTERFACE &> /dev/null; then
            WG_PUBLIC_KEY=$(wg show $WG_INTERFACE public-key 2>/dev/null || echo "")
            print_success "Detected WireGuard public key"
        fi
    fi
    
    # Get server IP
    SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || curl -s ipinfo.io/ip 2>/dev/null || echo "YOUR_SERVER_IP")
    WG_ENDPOINT="${SERVER_IP}:51820"
    
    cat > $INSTALL_DIR/.env << EOF
# WireGuard QR Manager Configuration
# Generated on $(date)

WG_INTERFACE=$WG_INTERFACE
WG_SERVER_PUBLIC_KEY=$WG_PUBLIC_KEY
WG_SERVER_ENDPOINT=$WG_ENDPOINT
WG_DNS=1.1.1.1, 1.0.0.1
WG_ALLOWED_IPS=0.0.0.0/0, ::/0
WG_SUBNET=10.10.0
WG_START_IP=10
DATABASE_URL=sqlite:///$INSTALL_DIR/wireguard_peers.db
APP_HOST=0.0.0.0
APP_PORT=6000
EOF

    print_success "Configuration file created at $INSTALL_DIR/.env"
    print_warning "Please edit $INSTALL_DIR/.env and set WG_SERVER_PUBLIC_KEY if not auto-detected"
else
    print_warning "Configuration file already exists, skipping..."
fi

# Step 7: Install systemd service
print_status "Installing systemd service..."
cp $INSTALL_DIR/wireguard-qr-manager.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable wireguard-qr-manager

print_success "Systemd service installed"

# Step 8: Setup Nginx
print_status "Checking Nginx configuration..."

read -p "Enter your domain name (or 'skip' to configure manually): " DOMAIN

if [ "$DOMAIN" != "skip" ] && [ -n "$DOMAIN" ]; then
    # Update nginx config with domain
    sed -i "s/vpn.yourdomain.com/$DOMAIN/g" $INSTALL_DIR/nginx/wireguard-qr-manager.conf
    
    # Copy to nginx
    cp $INSTALL_DIR/nginx/wireguard-qr-manager.conf /etc/nginx/sites-available/wireguard-qr-manager
    ln -sf /etc/nginx/sites-available/wireguard-qr-manager /etc/nginx/sites-enabled/
    
    # Create basic auth
    print_status "Setting up basic authentication..."
    read -p "Enter admin username [admin]: " ADMIN_USER
    ADMIN_USER=${ADMIN_USER:-admin}
    htpasswd -c /etc/nginx/.htpasswd $ADMIN_USER
    
    print_success "Nginx configured for $DOMAIN"
    
    # SSL setup
    read -p "Setup Let's Encrypt SSL now? (y/n): " SETUP_SSL
    if [ "$SETUP_SSL" = "y" ]; then
        print_status "Setting up SSL certificate..."
        certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email admin@$DOMAIN || true
        print_success "SSL certificate installed"
    fi
    
    nginx -t && systemctl reload nginx
else
    print_warning "Nginx configuration skipped. Manual setup required."
    print_warning "Copy $INSTALL_DIR/nginx/wireguard-qr-manager.conf to /etc/nginx/sites-available/"
fi

# Step 9: Start the service
print_status "Starting WireGuard QR Manager..."
systemctl start wireguard-qr-manager

# Check if running
sleep 2
if systemctl is-active --quiet wireguard-qr-manager; then
    print_success "Service started successfully!"
else
    print_error "Service failed to start. Check logs: journalctl -u wireguard-qr-manager -f"
fi

# Final summary
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                    Installation Complete!                     ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
print_success "Installation directory: $INSTALL_DIR"
print_success "Configuration file: $INSTALL_DIR/.env"
print_success "Database: $INSTALL_DIR/wireguard_peers.db"
echo ""
print_status "Service commands:"
echo "  Start:   systemctl start wireguard-qr-manager"
echo "  Stop:    systemctl stop wireguard-qr-manager"
echo "  Status:  systemctl status wireguard-qr-manager"
echo "  Logs:    journalctl -u wireguard-qr-manager -f"
echo ""

if [ "$DOMAIN" != "skip" ] && [ -n "$DOMAIN" ]; then
    print_status "Access your panel at: https://$DOMAIN"
else
    print_status "Access your panel at: http://YOUR_SERVER_IP:6000"
fi

echo ""
print_warning "IMPORTANT: Make sure to configure your firewall:"
echo "  ufw allow 6000/tcp   # If not using nginx"
echo "  ufw allow 443/tcp    # For HTTPS"
echo "  ufw allow 51820/udp  # WireGuard"
echo ""
print_warning "Edit $INSTALL_DIR/.env if WG_SERVER_PUBLIC_KEY is empty!"
echo ""
