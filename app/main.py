"""
WireGuard QR Code Manager - FastAPI Service
Generates and manages WireGuard VPN access QR codes
"""

import os
import subprocess
import secrets
import io
import base64
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict

from fastapi import FastAPI, HTTPException, Depends, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import qrcode
from pydantic import BaseModel


# Configuration
class Config:
    WG_INTERFACE = os.getenv("WG_INTERFACE", "wg0")
    WG_SERVER_PUBLIC_KEY = os.getenv("WG_SERVER_PUBLIC_KEY", "")
    WG_SERVER_ENDPOINT = os.getenv("WG_SERVER_ENDPOINT", "vpn.example.com:51820")
    WG_DNS = os.getenv("WG_DNS", "1.1.1.1, 1.0.0.1")
    WG_ALLOWED_IPS = os.getenv("WG_ALLOWED_IPS", "0.0.0.0/0, ::/0")
    WG_SUBNET = os.getenv("WG_SUBNET", "10.10.0")
    WG_START_IP = int(os.getenv("WG_START_IP", "10"))
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./wireguard_peers.db")
    APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT = int(os.getenv("APP_PORT", "6000"))

config = Config()

# Database setup
engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Peer(Base):
    __tablename__ = "peers"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=True)
    public_key = Column(String(64), unique=True, index=True)
    private_key = Column(String(64))
    preshared_key = Column(String(64), nullable=True)
    ip_address = Column(String(20), unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used = Column(DateTime, nullable=True)
    usage_count = Column(Integer, default=0)
    is_active = Column(Integer, default=1)
    config_text = Column(Text)
    # Stats tracking
    total_rx = Column(BigInteger, default=0)  # Total received bytes
    total_tx = Column(BigInteger, default=0)  # Total sent bytes
    last_handshake = Column(DateTime, nullable=True)


Base.metadata.create_all(bind=engine)


# FastAPI app
app = FastAPI(title="WireGuard QR Manager", version="1.1.0")

templates_dir = Path(__file__).parent / "templates"
templates_dir.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_wireguard_stats() -> Dict[str, dict]:
    """Get current WireGuard peer stats from wg show"""
    stats = {}
    try:
        result = subprocess.run(
            ["wg", "show", config.WG_INTERFACE, "dump"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode != 0:
            return stats
        
        lines = result.stdout.strip().split('\n')
        # Skip first line (interface info)
        for line in lines[1:]:
            parts = line.split('\t')
            if len(parts) >= 8:
                public_key = parts[0]
                # parts: pubkey, psk, endpoint, allowed-ips, latest-handshake, rx, tx, keepalive
                latest_handshake = int(parts[4]) if parts[4] != '0' else 0
                rx_bytes = int(parts[5]) if parts[5] else 0
                tx_bytes = int(parts[6]) if parts[6] else 0
                
                stats[public_key] = {
                    'latest_handshake': datetime.fromtimestamp(latest_handshake) if latest_handshake else None,
                    'rx_bytes': rx_bytes,
                    'tx_bytes': tx_bytes,
                    'is_connected': latest_handshake > 0 and (datetime.now().timestamp() - latest_handshake) < 130  # ~2 min
                }
    except Exception as e:
        print(f"Error getting WireGuard stats: {e}")
    
    return stats


def format_bytes(bytes_val: int) -> str:
    """Format bytes to human readable"""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_val / (1024 * 1024 * 1024):.2f} GB"


def time_ago(dt: datetime) -> str:
    """Format datetime as time ago string"""
    if not dt:
        return "Never"
    
    now = datetime.now()
    diff = now - dt
    
    if diff.total_seconds() < 60:
        return "Just now"
    elif diff.total_seconds() < 3600:
        mins = int(diff.total_seconds() / 60)
        return f"{mins}m ago"
    elif diff.total_seconds() < 86400:
        hours = int(diff.total_seconds() / 3600)
        return f"{hours}h ago"
    elif diff.days < 7:
        return f"{diff.days}d ago"
    else:
        return dt.strftime("%b %d")


def generate_wireguard_keys() -> tuple[str, str]:
    try:
        private_key = subprocess.run(
            ["wg", "genkey"], capture_output=True, text=True, check=True
        ).stdout.strip()
        public_key = subprocess.run(
            ["wg", "pubkey"], input=private_key, capture_output=True, text=True, check=True
        ).stdout.strip()
        return private_key, public_key
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="WireGuard tools not installed")
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Key generation failed: {e}")


def generate_preshared_key() -> str:
    try:
        return subprocess.run(
            ["wg", "genpsk"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except:
        return base64.b64encode(secrets.token_bytes(32)).decode()


def get_next_ip(db: Session) -> str:
    last_peer = db.query(Peer).order_by(Peer.id.desc()).first()
    if last_peer:
        last_octet = int(last_peer.ip_address.split('.')[-1].split('/')[0])
        next_octet = last_octet + 1
    else:
        next_octet = config.WG_START_IP
    
    if next_octet > 254:
        raise HTTPException(status_code=500, detail="IP address pool exhausted")
    
    return f"{config.WG_SUBNET}.{next_octet}/32"


def create_client_config(private_key: str, ip_address: str, preshared_key: Optional[str] = None) -> str:
    config_lines = [
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {ip_address}",
        f"DNS = {config.WG_DNS}",
        "",
        "[Peer]",
        f"PublicKey = {config.WG_SERVER_PUBLIC_KEY}",
        f"AllowedIPs = {config.WG_ALLOWED_IPS}",
        f"Endpoint = {config.WG_SERVER_ENDPOINT}",
        "PersistentKeepalive = 25"
    ]
    if preshared_key:
        config_lines.insert(-1, f"PresharedKey = {preshared_key}")
    return "\n".join(config_lines)


def add_peer_to_wireguard(public_key: str, ip_address: str, preshared_key: Optional[str] = None):
    try:
        cmd = ["wg", "set", config.WG_INTERFACE, "peer", public_key, "allowed-ips", ip_address]
        if preshared_key:
            cmd.extend(["preshared-key", "/dev/stdin"])
            subprocess.run(cmd, input=preshared_key, text=True, check=True, capture_output=True)
        else:
            subprocess.run(cmd, check=True, capture_output=True)
        subprocess.run(["wg-quick", "save", config.WG_INTERFACE], capture_output=True)
        return True
    except Exception as e:
        print(f"Warning: Could not add peer: {e}")
        return False


def remove_peer_from_wireguard(public_key: str):
    try:
        subprocess.run(
            ["wg", "set", config.WG_INTERFACE, "peer", public_key, "remove"],
            check=True, capture_output=True
        )
        subprocess.run(["wg-quick", "save", config.WG_INTERFACE], capture_output=True)
        return True
    except Exception as e:
        print(f"Warning: Could not remove peer: {e}")
        return False


def generate_qr_code(config_text: str) -> str:
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(config_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return base64.b64encode(buffer.getvalue()).decode()


# Routes
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    peers = db.query(Peer).order_by(Peer.created_at.desc()).all()
    wg_stats = get_wireguard_stats()
    
    # Enrich peers with live stats
    connected_count = 0
    peers_data = []
    
    for peer in peers:
        stats = wg_stats.get(peer.public_key, {})
        is_connected = stats.get('is_connected', False)
        
        if is_connected:
            connected_count += 1
        
        # Update last_handshake in DB if we have new data
        if stats.get('latest_handshake'):
            peer.last_handshake = stats['latest_handshake']
        
        peer_data = {
            'id': peer.id,
            'name': peer.name,
            'ip_address': peer.ip_address,
            'created_at': peer.created_at,
            'last_used': peer.last_used,
            'usage_count': peer.usage_count,
            'is_active': peer.is_active,
            'public_key': peer.public_key,
            # Live stats
            'is_connected': is_connected,
            'last_handshake': stats.get('latest_handshake') or peer.last_handshake,
            'last_handshake_ago': time_ago(stats.get('latest_handshake') or peer.last_handshake),
            'rx_bytes': stats.get('rx_bytes', 0),
            'tx_bytes': stats.get('tx_bytes', 0),
            'rx_formatted': format_bytes(stats.get('rx_bytes', 0)),
            'tx_formatted': format_bytes(stats.get('tx_bytes', 0)),
            'total_transfer': format_bytes(stats.get('rx_bytes', 0) + stats.get('tx_bytes', 0))
        }
        peers_data.append(peer_data)
    
    db.commit()
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "peers": peers_data,
        "connected_count": connected_count,
        "server_configured": bool(config.WG_SERVER_PUBLIC_KEY),
        "now": datetime.now().strftime("%H:%M:%S")
    })


@app.post("/api/peers")
async def create_peer(
    name: Optional[str] = Form(None),
    use_preshared_key: bool = Form(True),
    db: Session = Depends(get_db)
):
    if not config.WG_SERVER_PUBLIC_KEY:
        raise HTTPException(status_code=400, detail="Server not configured")
    
    private_key, public_key = generate_wireguard_keys()
    preshared_key = generate_preshared_key() if use_preshared_key else None
    ip_address = get_next_ip(db)
    config_text = create_client_config(private_key, ip_address, preshared_key)
    
    peer = Peer(
        name=name or f"Peer-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        public_key=public_key,
        private_key=private_key,
        preshared_key=preshared_key,
        ip_address=ip_address,
        config_text=config_text
    )
    
    db.add(peer)
    db.commit()
    db.refresh(peer)
    
    add_peer_to_wireguard(public_key, ip_address, preshared_key)
    
    return {"id": peer.id, "name": peer.name, "ip_address": peer.ip_address}


@app.get("/api/peers")
async def list_peers(db: Session = Depends(get_db)):
    peers = db.query(Peer).order_by(Peer.created_at.desc()).all()
    wg_stats = get_wireguard_stats()
    
    result = []
    for p in peers:
        stats = wg_stats.get(p.public_key, {})
        result.append({
            "id": p.id,
            "name": p.name,
            "ip_address": p.ip_address,
            "created_at": p.created_at.isoformat(),
            "last_used": p.last_used.isoformat() if p.last_used else None,
            "usage_count": p.usage_count,
            "is_active": bool(p.is_active),
            "is_connected": stats.get('is_connected', False),
            "last_handshake": stats.get('latest_handshake').isoformat() if stats.get('latest_handshake') else None,
            "rx_bytes": stats.get('rx_bytes', 0),
            "tx_bytes": stats.get('tx_bytes', 0)
        })
    return result


@app.get("/api/peers/{peer_id}")
async def get_peer(peer_id: int, db: Session = Depends(get_db)):
    peer = db.query(Peer).filter(Peer.id == peer_id).first()
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found")
    
    peer.last_used = datetime.utcnow()
    peer.usage_count += 1
    db.commit()
    
    wg_stats = get_wireguard_stats()
    stats = wg_stats.get(peer.public_key, {})
    
    return {
        "id": peer.id,
        "name": peer.name,
        "ip_address": peer.ip_address,
        "created_at": peer.created_at.isoformat(),
        "last_used": peer.last_used.isoformat() if peer.last_used else None,
        "usage_count": peer.usage_count,
        "is_active": bool(peer.is_active),
        "qr_code": generate_qr_code(peer.config_text),
        "config": peer.config_text,
        "is_connected": stats.get('is_connected', False),
        "last_handshake": stats.get('latest_handshake').isoformat() if stats.get('latest_handshake') else None,
        "rx_bytes": stats.get('rx_bytes', 0),
        "tx_bytes": stats.get('tx_bytes', 0),
        "rx_formatted": format_bytes(stats.get('rx_bytes', 0)),
        "tx_formatted": format_bytes(stats.get('tx_bytes', 0))
    }


@app.delete("/api/peers/{peer_id}")
async def delete_peer(peer_id: int, db: Session = Depends(get_db)):
    peer = db.query(Peer).filter(Peer.id == peer_id).first()
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found")
    
    remove_peer_from_wireguard(peer.public_key)
    db.delete(peer)
    db.commit()
    
    return {"message": "Peer deleted"}


@app.post("/api/peers/{peer_id}/toggle")
async def toggle_peer(peer_id: int, db: Session = Depends(get_db)):
    peer = db.query(Peer).filter(Peer.id == peer_id).first()
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found")
    
    if peer.is_active:
        remove_peer_from_wireguard(peer.public_key)
        peer.is_active = 0
    else:
        add_peer_to_wireguard(peer.public_key, peer.ip_address, peer.preshared_key)
        peer.is_active = 1
    
    db.commit()
    return {"is_active": bool(peer.is_active)}


@app.get("/api/stats")
async def get_stats(db: Session = Depends(get_db)):
    """Get live WireGuard stats"""
    peers = db.query(Peer).all()
    wg_stats = get_wireguard_stats()
    
    connected = []
    for peer in peers:
        stats = wg_stats.get(peer.public_key, {})
        if stats.get('is_connected'):
            connected.append({
                'id': peer.id,
                'name': peer.name,
                'ip_address': peer.ip_address,
                'rx_formatted': format_bytes(stats.get('rx_bytes', 0)),
                'tx_formatted': format_bytes(stats.get('tx_bytes', 0)),
                'last_handshake_ago': time_ago(stats.get('latest_handshake'))
            })
    
    return {
        'connected_count': len(connected),
        'connected_peers': connected,
        'total_peers': len(peers)
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.APP_HOST, port=config.APP_PORT)
