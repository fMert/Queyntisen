#!/bin/bash

echo "🚀 Queyntisen Kurulum Sihirbazı Başlatılıyor..."

# 1. Python kontrolü
if ! command -v python3 &> /dev/null; then
    echo "❌ HATA: Python 3 yüklü değil! Lütfen önce Python yükleyin."
    exit 1
fi

# 2. Kurulum klasörünü oluştur (~/.queyntisen)
INSTALL_DIR="$HOME/.queyntisen"
echo "📂 Kurulum klasörü güncelleniyor: $INSTALL_DIR"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

# 3. Dosyaları kopyala
# (Scriptin çalıştığı yerdeki dosyaları al)
cp editor.py "$INSTALL_DIR/"
cp requirements.txt "$INSTALL_DIR/"
cp -r LICENSE "$INSTALL_DIR/" 2>/dev/null

# 4. Sanal ortam (venv) kur
echo "🐍 Sanal ortam (venv) hazırlanıyor..."
python3 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"

# 5. Kütüphaneleri yükle
echo "📦 Gerekli kütüphaneler yükleniyor..."
pip install -r "$INSTALL_DIR/requirements.txt" > /dev/null 2>&1

# 6. Başlatma scripti oluştur (~/.local/bin/queyntisen)
LAUNCHER_DIR="$HOME/.local/bin"
LAUNCHER_SCRIPT="$LAUNCHER_DIR/queyntisen"

mkdir -p "$LAUNCHER_DIR"

cat <<EOF > "$LAUNCHER_SCRIPT"
#!/bin/bash
source "$INSTALL_DIR/venv/bin/activate"
python3 "$INSTALL_DIR/editor.py" "\$@"
EOF

chmod +x "$LAUNCHER_SCRIPT"
echo "✅ Başlatıcı oluşturuldu: $LAUNCHER_SCRIPT"

# --- OTOMATİK PATH AYARI (YENİ KISIM) ---
SHELL_NAME=$(basename "$SHELL")
RC_FILE=""

if [ "$SHELL_NAME" = "zsh" ]; then
    RC_FILE="$HOME/.zshrc"
elif [ "$SHELL_NAME" = "bash" ]; then
    RC_FILE="$HOME/.bashrc"
else
    # Bilinmeyen shell ise profile ekle
    RC_FILE="$HOME/.profile"
fi

# Eğer PATH ayarı dosyada yoksa ekle
if [ -f "$RC_FILE" ]; then
    if ! grep -q "$LAUNCHER_DIR" "$RC_FILE"; then
        echo "" >> "$RC_FILE"
        echo '# Queyntisen Editor PATH' >> "$RC_FILE"
        echo "export PATH=\"$LAUNCHER_DIR:\$PATH\"" >> "$RC_FILE"
        echo "🔧 PATH ayarı $RC_FILE dosyasına eklendi."
    else
        echo "👍 PATH ayarı zaten mevcut."
    fi
fi

echo "---------------------------------------------"
echo "🎉 Kurulum Başarıyla Tamamlandı!"
echo "⚠️  ÖNEMLİ: Ayarların aktif olması için terminali kapatıp açın."
echo "Sonra sadece 'queyntisen' yazarak çalıştırabilirsiniz."
echo "---------------------------------------------"
