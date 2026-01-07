#!/bin/bash
# WireGuard Server Setup Script
# Sets up WireGuard and integrates with QR Manager

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${BLUE}[*]${NC} $1"; }
print_success() { echo -e "${GREEN}[✓]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }

if [[ $EUID -ne 0 ]]; then
   print_error "Run as root: sudo bash setup-wireguard-server.sh"
   exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║            WireGuard Server Setup Script                     ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Configuration
WG_INTERFACE="wg0"
WG_PORT="51820"
WG_SUBNET="10.10.0"
WG_SERVER_IP="${WG_SUBNET}.1/24"
WG_DIR="/etc/wireguard"

# Detect main network interface
MAIN_IFACE=$(ip route | grep default | awk '{print $5}' | head -1)
print_status "Detected main interface: $MAIN_IFACE"

# Get server public IP (for endpoint)
SERVER_IP=$(curl -4 -s ifconfig.me 2>/dev/null || curl -4 -s ipinfo.io/ip 2>/dev/null)
SERVER_IP6=$(curl -6 -s ifconfig.me 2>/dev/null || echo "")

if [ -n "$SERVER_IP" ]; then
    print_status "Detected IPv4: $SERVER_IP"
fi
if [ -n "$SERVER_IP6" ]; then
    print_status "Detected IPv6: $SERVER_IP6"
fi

# Ask which IP to use for endpoint
echo ""
echo "Which IP should clients use to connect?"
echo "  1) IPv4: $SERVER_IP"
echo "  2) IPv6: $SERVER_IP6"
echo "  3) Custom hostname/IP"
read -p "Choice [1]: " IP_CHOICE
IP_CHOICE=${IP_CHOICE:-1}

case $IP_CHOICE in
    1) ENDPOINT="$SERVER_IP:$WG_PORT" ;;
    2) ENDPOINT="[$SERVER_IP6]:$WG_PORT" ;;
    3) 
        read -p "Enter hostname or IP: " CUSTOM_HOST
        ENDPOINT="$CUSTOM_HOST:$WG_PORT"
        ;;
    *) ENDPOINT="$SERVER_IP:$WG_PORT" ;;
esac

print_status "Using endpoint: $ENDPOINT"

# Step 1: Generate server keys
print_status "Generating WireGuard server keys..."
mkdir -p $WG_DIR
chmod 700 $WG_DIR

wg genkey | tee $WG_DIR/privatekey | wg pubkey > $WG_DIR/publickey
chmod 600 $WG_DIR/privatekey

SERVER_PRIVATE_KEY=$(cat $WG_DIR/privatekey)
SERVER_PUBLIC_KEY=$(cat $WG_DIR/publickey)

print_success "Server public key: $SERVER_PUBLIC_KEY"

# Step 2: Create WireGuard config
print_status "Creating WireGuard configuration..."

cat > $WG_DIR/$WG_INTERFACE.conf << EOF
# WireGuard Server Configuration
# Generated on $(date)

[Interface]
Address = $WG_SERVER_IP
ListenPort = $WG_PORT
PrivateKey = $SERVER_PRIVATE_KEY

# NAT and forwarding rules
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o $MAIN_IFACE -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o $MAIN_IFACE -j MASQUERADE

# Peers will be added below by QR Manager
# Or manually with: wg set wg0 peer <pubkey> allowed-ips <ip>/32

EOF

chmod 600 $WG_DIR/$WG_INTERFACE.conf
print_success "Created $WG_DIR/$WG_INTERFACE.conf"

# Step 3: Enable IP forwarding
print_status "Enabling IP forwarding..."

# Check if already enabled
if ! grep -q "^net.ipv4.ip_forward=1" /etc/sysctl.conf; then
    echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
fi
if ! grep -q "^net.ipv6.conf.all.forwarding=1" /etc/sysctl.conf; then
    echo "net.ipv6.conf.all.forwarding=1" >> /etc/sysctl.conf
fi

sysctl -p > /dev/null 2>&1
print_success "IP forwarding enabled"

# Step 4: Start WireGuard
print_status "Starting WireGuard interface..."

# Enable and start
systemctl enable wg-quick@$WG_INTERFACE
systemctl start wg-quick@$WG_INTERFACE

# Verify
sleep 1
if wg show $WG_INTERFACE > /dev/null 2>&1; then
    print_success "WireGuard is running!"
    wg show $WG_INTERFACE
else
    print_error "Failed to start WireGuard"
    journalctl -u wg-quick@$WG_INTERFACE -n 20 --no-pager
    exit 1
fi

# Step 5: Update QR Manager configuration
print_status "Updating QR Manager configuration..."

QR_MANAGER_ENV="/opt/wireguard-qr-manager/.env"
if [ -f "$QR_MANAGER_ENV" ]; then
    sed -i "s|^WG_SERVER_PUBLIC_KEY=.*|WG_SERVER_PUBLIC_KEY=$SERVER_PUBLIC_KEY|" $QR_MANAGER_ENV
    sed -i "s|^WG_SERVER_ENDPOINT=.*|WG_SERVER_ENDPOINT=$ENDPOINT|" $QR_MANAGER_ENV
    print_success "Updated $QR_MANAGER_ENV"
    
    # Restart QR Manager
    if systemctl is-active --quiet wireguard-qr-manager; then
        systemctl restart wireguard-qr-manager
        print_success "Restarted QR Manager service"
    fi
else
    print_warning "QR Manager .env not found at $QR_MANAGER_ENV"
    print_warning "Manually set:"
    echo "  WG_SERVER_PUBLIC_KEY=$SERVER_PUBLIC_KEY"
    echo "  WG_SERVER_ENDPOINT=$ENDPOINT"
fi

# Step 6: Firewall
print_status "Checking firewall..."

if command -v ufw &> /dev/null; then
    ufw allow $WG_PORT/udp comment "WireGuard" > /dev/null 2>&1 || true
    print_success "Added UFW rule for port $WG_PORT/udp"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║              WireGuard Server Setup Complete!                ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
print_success "WireGuard Interface: $WG_INTERFACE"
print_success "Server IP: $WG_SERVER_IP"
print_success "Listen Port: $WG_PORT"
print_success "Public Key: $SERVER_PUBLIC_KEY"
print_success "Endpoint: $ENDPOINT"
echo ""
echo "Commands:"
echo "  Status:  wg show"
echo "  Restart: systemctl restart wg-quick@$WG_INTERFACE"
echo "  Logs:    journalctl -u wg-quick@$WG_INTERFACE"
echo ""
print_status "Now create a peer in QR Manager and scan the QR code!"
echo ""

# Show current config
echo "Current WireGuard status:"
wg show
