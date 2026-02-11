#!/bin/bash

echo "🚀 Queyntisen Kurulum Sihirbazı Başlatılıyor..."

# 1. Python kontrolü
if ! command -v python3 &> /dev/null; then
    echo "❌ HATA: Python 3 yüklü değil! Lütfen önce Python yükleyin."
    exit 1
fi

# 2. Kurulum klasörünü oluştur (~/.queyntisen)
INSTALL_DIR="$HOME/.queyntisen"
echo "📂 Kurulum klasörü oluşturuluyor: $INSTALL_DIR"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

# 3. Dosyaları kopyala
cp editor.py "$INSTALL_DIR/"
cp requirements.txt "$INSTALL_DIR/"

# 4. Sanal ortam (venv) kur
echo "🐍 Sanal ortam (venv) hazırlanıyor..."
python3 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"

# 5. Kütüphaneleri yükle
echo "📦 Gerekli kütüphaneler yükleniyor..."
pip install -r "$INSTALL_DIR/requirements.txt" > /dev/null 2>&1

# 6. Başlatma scripti oluştur (/usr/local/bin/queyntisen)
LAUNCHER_SCRIPT="$HOME/.local/bin/queyntisen"
mkdir -p "$HOME/.local/bin"

cat <<EOF > "$LAUNCHER_SCRIPT"
#!/bin/bash
source "$INSTALL_DIR/venv/bin/activate"
python3 "$INSTALL_DIR/editor.py" "\$@"
EOF

chmod +x "$LAUNCHER_SCRIPT"

# 7. PATH kontrolü
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo "⚠️ UYARI: $HOME/.local/bin PATH içinde değil."
    echo "Lütfen şu komutu çalıştırın: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo "✅ Kurulum Tamamlandı!"
echo "Artık terminale 'queyntisen' yazarak editörü açabilirsiniz."
