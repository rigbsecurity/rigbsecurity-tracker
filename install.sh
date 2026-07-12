#!/bin/bash

R='\033[31m'
G='\033[32m'
C='\033[36m'
W='\033[0m'
Y='\033[33m'

echo -e "${G}"
cat << "EOF"
 ____  _       _     ____                       _ _         
|  _ $$_) __ _| |__ / ___|  ___  ___ _   _ _ __(_) |_ _   _ 
| |_) | |/ _` | '_ \\___ \ / _ \/ __| | | | '__| | __| | | |
|  _ <| | (_| | |_) |___) |  __/ (__| |_| | |  | | |_| |_| |
|_| \_\_|\__, |_.__/|____/ \___|\___|\__,_|_|  |_|\__|\__, |
         |___/            TRACKER v2.0                 |___/ 
EOF
echo -e "${W}"

echo -e "${G}[+]${C} Installing RigbSecurity Tracker...${W}\n"

# Detect OS
if [ -f /data/data/com.termux/files/usr/bin/bash ]; then
    echo -e "${G}[+]${C} Termux detected${W}"
    pkg update -y
    pkg install -y python git
    pip install -r requirements.txt
elif [ -f /etc/os-release ]; then
    . /etc/os-release
    echo -e "${G}[+]${C} ${NAME} detected${W}"
    
    if command -v apt &> /dev/null; then
        sudo apt update
        sudo apt install -y python3 python3-pip git curl
    elif command -v pacman &> /dev/null; then
        sudo pacman -Sy python python-pip git curl --noconfirm
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y python3 python3-pip git curl
    fi
    
    pip3 install -r requirements.txt
fi

# Install cloudflared
if ! command -v cloudflared &> /dev/null; then
    echo -e "${G}[+]${C} Installing Cloudflared...${W}"
    if [ "$(uname -m)" = "aarch64" ]; then
        curl -Lo cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
    elif [ "$(uname -m)" = "armv7l" ]; then
        curl -Lo cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm
    else
        curl -Lo cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
    fi
    chmod +x cloudflared
    sudo mv cloudflared /usr/local/bin/ 2>/dev/null || mv cloudflared $PREFIX/bin/ 2>/dev/null
fi

# Create directories
mkdir -p db captures logs static

echo -e "\n${G}[+]${C} Installation Complete!${W}"
echo -e "${G}[+]${C} Run: ${W}python3 tracker.py${W}\n"