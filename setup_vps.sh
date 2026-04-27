#!/bin/bash
# FTMO Bot — Ubuntu VPS Otomatik Kurulum Scripti
# Kullanım: bash setup_vps.sh

set -e

echo "=========================================="
echo "  FTMO Bot — Ubuntu VPS Kurulum"
echo "=========================================="

# 1. Sistem güncelle
echo "[1/8] Sistem güncelleniyor..."
apt update && apt upgrade -y

# 2. Wine ve bağımlılıklar
echo "[2/8] Wine kuruluyor..."
dpkg --add-architecture i386
apt update
apt install -y wine64 wine32 xvfb wget curl python3 python3-pip python3-venv cabextract

# 3. Sanal ekran
echo "[3/8] Sanal ekran başlatılıyor..."
Xvfb :99 -screen 0 1024x768x16 &
export DISPLAY=:99
sleep 2

# 4. MT5 indir ve kur
echo "[4/8] MetaTrader 5 kuruluyor..."
mkdir -p /root/mt5
cd /root/mt5
if [ ! -f mt5setup.exe ]; then
    wget https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
fi
wine mt5setup.exe /auto
sleep 30

# 5. Python ortamı
echo "[5/8] Python ortamı kuruluyor..."
cd /root/ftmo-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements_linux.txt

# 6. mt_executor.py'yi Linux'a uyarla
echo "[6/8] MT5 Linux bridge ayarlanıyor..."
cd /root/ftmo-bot/src
if grep -q "import MetaTrader5 as mt5" mt_executor.py; then
    sed -i 's/import MetaTrader5 as mt5/from mt5linux import MetaTrader5 as mt5/' mt_executor.py
    echo "  mt_executor.py güncellendi"
else
    echo "  mt_executor.py zaten güncel"
fi

# 7. Systemd servisleri oluştur
echo "[7/8] Systemd servisleri kuruluyor..."

cat > /etc/systemd/system/xvfb.service << 'XVFB'
[Unit]
Description=Virtual Frame Buffer
After=network.target
[Service]
Type=simple
ExecStart=/usr/bin/Xvfb :99 -screen 0 1024x768x16
Restart=always
[Install]
WantedBy=multi-user.target
XVFB

cat > /etc/systemd/system/mt5.service << 'MT5'
[Unit]
Description=MetaTrader 5
After=xvfb.service
Requires=xvfb.service
[Service]
Type=simple
Environment=DISPLAY=:99
ExecStart=/usr/bin/wine "/root/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe"
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
MT5

cat > /etc/systemd/system/ftmo-bot.service << 'BOT'
[Unit]
Description=FTMO Trading Bot
After=mt5.service
Requires=mt5.service
[Service]
Type=simple
WorkingDirectory=/root/ftmo-bot/src
Environment=DISPLAY=:99
ExecStart=/root/ftmo-bot/venv/bin/python3 main.py
Restart=always
RestartSec=15
[Install]
WantedBy=multi-user.target
BOT

systemctl daemon-reload
systemctl enable xvfb mt5 ftmo-bot

# 8. Servisleri başlat
echo "[8/8] Servisler başlatılıyor..."
systemctl start xvfb
sleep 2
systemctl start mt5
sleep 15
systemctl start ftmo-bot

echo ""
echo "=========================================="
echo "  KURULUM TAMAMLANDI!"
echo "=========================================="
echo ""
echo "  Durumu kontrol et:"
echo "    systemctl status ftmo-bot"
echo ""
echo "  Logları takip et:"
echo "    journalctl -u ftmo-bot -f"
echo ""
echo "  Trade logları:"
echo "    cat /root/ftmo-bot/logs/trade_logs.txt"
echo ""
