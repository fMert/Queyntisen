# Queyntisen - AI Native Terminal Editor
# Copyright (C) 2026 fMert


import curses
import sys
import os
import re
import threading  # <--- YENİ: Paralel işlem için
import time       # <--- YENİ: Bekleme süresi simülasyonu için
import textwrap
from openai import OpenAI # <--- YENİ KÜTÜPHANE


# --- YAPAY ZEKA AYARLARI (OTOMATİK ALGILAMA) ---
API_KEY = "lm-studio" 
BASE_URL = "http://localhost:1234/v1"

# Başlangıç değerleri
client = None
ai_available = False
MODEL_NAME = "local-model" # Eğer bulamazsak bunu deneriz

def initialize_ai():
    global client, ai_available, MODEL_NAME
    try:
        # 1. İstemciyi Başlat
        client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
        
        # 2. Sunucuya Bağlan ve Modelleri Sor (Handshake)
        # Bu işlem sunucu kapalıysa hata fırlatır ve 'except'e düşer
        available_models = client.models.list()
        
        # 3. Listeden İlk Modeli Seç
        if available_models.data:
            first_model_id = available_models.data[0].id
            MODEL_NAME = first_model_id
            ai_available = True
            # Debug için (Curses başlamadan önce terminale basar ama göremeyebilirsin)
            # print(f"Bulunan Model: {MODEL_NAME}")
        else:
            # Liste boşsa varsayılanı kullan
            MODEL_NAME = "local-model"
            ai_available = True
            
    except Exception as e:
        # Sunucu kapalıysa veya hata varsa AI'yı devre dışı bırak
        ai_available = False
        MODEL_NAME = "Bilinmiyor"

# Fonksiyonu hemen çağır ki program başlarken ayarlasın
initialize_ai()

# İstemciyi (Client) Başlat
try:
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    ai_available = True
except:
    ai_available = False

# --- YARDIMCI FONKSİYONLAR ---

# Python Anahtar Kelimeleri (Highlight için)
KEYWORDS = {
    "def", "class", "import", "from", "return", "if", "elif", "else",
    "while", "for", "in", "try", "except", "print", "True", "False", "None",
    "break", "continue", "pass", "and", "or", "not", "with", "as"
}

def draw_colored_line(stdscr, y, x, line):
    """
    Satırı analiz eder ve renkli olarak ekrana basar.
    """
    height, width = stdscr.getmaxyx()

    # Yorum satırı kontrolü (#)
    if "#" in line:
        try:
            code_part, comment_part = line.split("#", 1)
        except ValueError:
            # Nadir durum: Sadece # var ise
            code_part = ""
            comment_part = line.replace("#", "", 1)

        current_x = x

        # 1. Kod Kısmını Çiz
        tokens = re.split(r'(\W+)', code_part)
        for token in tokens:
            color = curses.color_pair(0) # Beyaz
            if token in KEYWORDS:
                color = curses.color_pair(1) | curses.A_BOLD # Mavi
            elif token.isdigit():
                color = curses.color_pair(4) # Magenta (Sayılar)
            elif token.startswith('"') or token.startswith("'"):
                color = curses.color_pair(2) # Sarı (String)

            try:
                # Ekran dışına taşarsa hata vermesin diye try-except
                if current_x < width:
                    stdscr.addstr(y, current_x, token, color)
                    current_x += len(token)
            except curses.error:
                pass

        # 2. Yorum Kısmını Çiz (# ve sonrası)
        try:
            if current_x < width:
                stdscr.addstr(y, current_x, "#" + comment_part, curses.color_pair(3))
        except curses.error:
            pass

    else:
        # Normal kod satırı (Yorum yok)
        tokens = re.split(r'(\W+)', line)
        current_x = x
        for token in tokens:
            color = curses.color_pair(0)

            if token in KEYWORDS:
                color = curses.color_pair(1) | curses.A_BOLD
            elif token.isdigit():
                color = curses.color_pair(4)
            elif token.startswith('"') or token.startswith("'"):
                color = curses.color_pair(2)

            try:
                if current_x < width:
                    stdscr.addstr(y, current_x, token, color)
                    current_x += len(token)
            except curses.error:
                pass

# --- ANA FONKSİYON ---

def main(stdscr):
    # İmleci görünür yap
    curses.curs_set(1)

    curses.raw()  # <--- Sinyalleri (Ctrl+Z, Ctrl+C) tuş olarak okumasını sağlar

        
    # --- FARE DESTEĞİNİ AÇ ---
    # -1 parametresi "Tüm fare olaylarını yakala" demektir
    curses.mousemask(-1)
    
    # Renkleri Başlat
    curses.start_color()
    curses.use_default_colors()

    # Renk Çiftleri (ID, Foreground, Background)
    # -1 terminalin varsayılanı demektir
    curses.init_pair(1, curses.COLOR_CYAN, -1)    # Keywords
    curses.init_pair(2, curses.COLOR_YELLOW, -1)  # Strings
    curses.init_pair(3, curses.COLOR_GREEN, -1)   # Comments
    curses.init_pair(4, curses.COLOR_MAGENTA, -1) # Numbers

    # Tuş ayarları
    stdscr.keypad(True) # Ok tuşlarını yakala
    curses.noecho()     # Tuşları ekrana otomatik basma

    # --- EDİTÖR DURUM DEĞİŞKENLERİ ---
    mode = "NORMAL"
    cy, cx = 0, 0
    filename = None
    buffer = [""]  # Varsayılan boş sayfa
    message = ""   # Durum çubuğu mesajı
    top_line = 0
    line_number_mode = "RELATIVE"
    last_search = ""
    clipboard = None
    selection_start = (0, 0)
    history = []
    chat_buffer = ["AI Chat Başladı...", "Buraya sorunu yazabilirsin."] # Sağ tarafın metni
    active_window = "CODE"  # Hangi penceredeyiz? "CODE" veya "CHAT"
    chat_cy = 0             # Chat penceresi imleç satırı
    chat_buffer = ["AI Chat Başladı...", "-----------------"] 
    chat_input = ""  # <--- YENİ: O an yazdığımız mesaj
    active_window = "CODE"    
    # --- DOSYA YÜKLEME ---
    if len(sys.argv) > 1:
        filename = sys.argv[1]
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    buffer = f.read().splitlines()
                if not buffer: buffer = [""]
                message = f"'{filename}' açıldı."
            except Exception as e:
                buffer = [f"Hata: {e}"]
        else:
            message = f"Yeni dosya: {filename}"

    # --- ARKA PLAN İŞÇİSİ (GERÇEK API) ---
    def ai_worker(user_message, current_code_context, buffer_ref): 
        
        if not ai_available:
            chat_buffer.append("HATA: AI Bağlı Değil.")
            return

        try:
            chat_buffer.append(f"AI: Kod analizi yapılıyor... ({MODEL_NAME})")
            
            # ... (System prompt ve OpenAI isteği kısımları AYNI KALSIN) ...
            system_prompt = (
                "Sen uzman bir Python geliştiricisisin. "
                "Kullanıcının gönderdiği kodu analiz et ve isteğine göre revize et. "
                "Cevabında SADECE revize edilmiş tam kodu ver. "
                "Açıklama yapma, markdown kullanma. "
                "Doğrudan kodu bas."
            )
            
            full_prompt = f"ŞU ANKİ KOD:\n{current_code_context}\n\nİSTEK: {user_message}\n\nYENİ KOD:"

            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": full_prompt}
                ],
                temperature=0.2, 
            )
            
            # Cevabı Al ve Temizle
            new_code_raw = response.choices[0].message.content
            new_code_clean = new_code_raw.replace("```python", "").replace("```", "").strip()
            
            # --- UYGULAMA KISMI (Burasi değişti) ---
            new_buffer = new_code_clean.split('\n')
            
            # buffer_ref listesini güncelle (Referans olduğu için ana listede değişir)
            buffer_ref[:] = new_buffer 
            
            chat_buffer.append("AI: Kod güncellendi! ✅")
            chat_buffer.append("-" * 20)

        except Exception as e:
            chat_buffer.append(f"HATA: {str(e)}")

            # --- ANA DÖNGÜ ---
        
    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()


        # --- SCROLLING MANTIĞI (KAMERA AYARI) ---
        # 1. Eğer imleç yukarı taştıysa, kamerayı yukarı çek
        if cy < top_line:
            top_line = cy

        # 2. Eğer imleç aşağı taştıysa, kamerayı aşağı it
        # (height - 1) dememizin sebebi en alt satırın durum çubuğu olması
        if cy >= top_line + (height - 1):
            top_line = cy - (height - 1) + 1

        # --- 1. TAMPONU VE SATIR NUMARALARINI ÇİZ ---
            
        # --- EKRAN AYIRMA HESABI ---
        split_col = int(width * 0.7) # Ekranın %70'i kod için
        
        # --- 1. SOL PENCERE (KOD) ÇİZİMİ ---
        # Eğer aktif pencere CODE ise, kenarlık rengini vurgula (opsiyonel)
        
        # Margin (Satır Numarası) Hesabı (Aynı kalıyor)
        if line_number_mode == "NONE":
            margin_width = 0
        else:
            margin_width = 5

        for i in range(height - 1): # Son satır durum çubuğu
            # --- Kod Kısmı ---
            file_line_idx = top_line + i
            
            # Ekranı temizle (Sadece sol tarafı)
            stdscr.addstr(i, 0, " " * split_col)
            
            # Satır Numaraları
            if margin_width > 0:
                # ... (Eski numara çizim kodu buraya, ama x sınırı var) ...
                if file_line_idx < len(buffer):
                    num_str = ""
                    if line_number_mode == "RELATIVE":
                        if file_line_idx == cy: num_str = str(file_line_idx + 1)
                        else: num_str = str(abs(file_line_idx - cy))
                    elif line_number_mode == "ABSOLUTE":
                        num_str = str(file_line_idx + 1)
                    
                    if line_number_mode != "NONE":
                        stdscr.addstr(i, 0, num_str.rjust(margin_width - 1), curses.color_pair(4))

            # Metin Çizimi (Sınırlandırılmış)
            if file_line_idx < len(buffer):
                line = buffer[file_line_idx]
                # Metni split_col genişliğinde kesiyoruz!
                visible_line = line[:split_col - margin_width - 1]
                
                # Renkli çizim fonksiyonunu çağır
                # Not: draw_colored_line fonksiyonun artık x sınırını bilmeli ama şimdilik basit addstr yapalım
                # Veya mevcut draw_colored_line fonksiyonun "width" parametresini split_col olarak almalı.
                # Basitlik için direkt yazıyorum, renklendirme sonra düzeltilir:
                try:
                    stdscr.addstr(i, margin_width, visible_line)
                except curses.error: pass
            else:
                stdscr.addstr(i, margin_width, "~", curses.color_pair(0))

            # --- AYIRICI ÇİZGİ (|) ---
            try:
                stdscr.addch(i, split_col, curses.ACS_VLINE) # Dikey çizgi
            except curses.error: pass

            # --- 2. SAĞ PENCERE (CHAT) ÇİZİMİ ---
            # Chat buffer'dan satırları al
            # A) Geçmiş Mesajları Çiz
            # Chat penceresinin satır limiti
            chat_height = height - 2 # En alt satır input için ayrıldı
            
            for i in range(chat_height):
                if i < len(chat_buffer):
                    line = chat_buffer[i]
                    visible = line[:width - split_col - 2]
                    try:
                        stdscr.addstr(i, split_col + 2, visible, curses.color_pair(2))
                    except curses.error: pass

            # B) Input Alanını Çiz (En Alt Satır)
            # Ayırıcı bir çizgi çekelim
            try:
                stdscr.addstr(height - 2, split_col + 1, "-" * (width - split_col - 1))
                # İstemi (Prompt) çiz: ">>> "
                prompt = ">>> " + chat_input
                stdscr.addstr(height - 1, split_col + 2, prompt, curses.A_BOLD)
            except curses.error: pass        
                        # 2. DURUM ÇUBUĞUNU ÇİZ
        # A) SOL TARAF (Editör Durumu)
        if message:
            status_text = message
        else:
            fname_display = filename if filename else "[Adsız]"
            status_text = f"MOD: {mode} | {fname_display} | {cy}:{cx}"
        
        # Durum çubuğunu sadece 'split_col' kadar (sol panel) çiz
        # string'i sola yasla ve boşlukla doldur
        status_text = status_text.ljust(split_col)
        # Uzunsa kes
        status_text = status_text[:split_col]
        
        try:
            # Sol alt köşeye yaz
            stdscr.addstr(height - 1, 0, status_text, curses.A_REVERSE)
        except curses.error: pass

        # B) SAĞ TARAF (Chat Input Kutusu)
        # Burayı belirgin yapmak için farklı renk kullanalım
        # Eğer AI aktifse model adını kısaca göster, değilse HATA yaz
        model_display = MODEL_NAME.split('/')[-1][:10] if ai_available else "OFFLINE"
        
        prompt_text = f" [{model_display}] SOR: " + chat_input        
        # Sağ tarafın genişliği
        chat_width = width - split_col
        
        # Inputu sığacak kadar kes (Scrolling yok şimdilik)
        visible_prompt = prompt_text
        if len(prompt_text) > chat_width - 1:
            visible_prompt = prompt_text[-(chat_width - 1):] # Sona odaklan
            
        # Kalan boşluğu doldur
        visible_prompt = visible_prompt.ljust(chat_width - 1)
        
        try:
            # Sağ alt köşeye yaz (Prompt Kutusu)
            # split_col konumundan başla
            # Renk çifti 2 (Sarı) veya A_REVERSE kullanabilirsin
            if active_window == "CHAT":
                # Aktifse Parlak Yeşil/Beyaz (Focus belli olsun)
                stdscr.addstr(height - 1, split_col, visible_prompt, curses.color_pair(2) | curses.A_REVERSE)
            else:
                # Pasifse sönük
                stdscr.addstr(height - 1, split_col, visible_prompt, curses.color_pair(0))
                
            # Ayırıcı karakteri düzelt (Status bar ile Input arasındaki birleşim)
            stdscr.addch(height - 1, split_col, '|')
        except curses.error: pass
        
        # --- 3. İMLECİ YERLEŞTİR (Cursor Placement) ---
        try:
            if active_window == "CODE":
                # Sol Pencere (Kod)
                stdscr.move(cy - top_line, cx + margin_width)
            else:
                # Sağ Pencere (Chat):
                # İmleci " SOR: " yazısının sonuna koyacağız
                # " SOR: " uzunluğu = 6 karakter (boşluklar dahil)
                prefix_len = 6 
                
                cursor_x = split_col + prefix_len + len(chat_input)
                
                # Eğer yazı çok uzarsa imleci ekran sonunda tut
                if cursor_x >= width:
                    cursor_x = width - 1
                
                stdscr.move(height - 1, cursor_x)                
        except curses.error:  # <--- İŞTE BU EKSİK!
            pass              # <--- VE BU!

        stdscr.refresh()


        # --- DİNAMİK ZAMAN AŞIMI (SENİN FİKRİN) ---
        if active_window == "CHAT":
            # Chat modundaysak akan yazı için bekleme yapma (100ms)
            stdscr.timeout(100)
        else:
            # Kod modundaysak sakin ol, tuş bekle (Blocking)
            # Böylece :, /, dd, yy gibi komutlar bozulmaz!
            stdscr.timeout(-1)

                        # 4. TUŞ DİNLEME
        key = stdscr.getch()

        if key == -1:
            continue
        
        # --- GLOBAL TUŞLAR (Her yerde çalışanlar) ---
        if key == 23: # Ctrl+W (Pencere Değiştir)
            if active_window == "CODE":
                active_window = "CHAT"
                message = ">> CHAT MODU <<"
                curses.curs_set(1)
            else:
                active_window = "CODE"
                message = ">> KOD MODU <<"
            continue # Döngünün başına dön (Aşağıdaki kodları işletme)

        # --- CHAT MODU MANTIĞI ---
        if active_window == "CHAT":
            # Kod editörü mantığını atla, sadece burası çalışsın
            
            if key == 10: # Enter Tuşu
                if chat_input.strip():
                    # 1. Kullanıcı mesajını ekle
                    user_msg = chat_input  # Mesajı yedekle
                    # 1. Dosya İçeriğini Hazırla
                    current_code = "\n".join(buffer)
                    
                    # 2. Thread Başlat (Mesaj + Kod Metni + BUFFER LİSTESİ)
                    # args=(user_msg, current_code, buffer) <-- buffer eklendi
                    t = threading.Thread(target=ai_worker, args=(user_msg, current_code, buffer))
                    t.daemon = True
                    t.start()      
            elif key == 127 or key == curses.KEY_BACKSPACE: # Silme
                chat_input = chat_input[:-1]
                
            elif key == 27: # ESC (Code moduna hızlı dönüş)
                active_window = "CODE"
                message = ">> KOD MODU <<"
                
            else:
                # Harf Yazma
                try:
                    # Sadece yazılabilir karakterleri al
                    if 32 <= key <= 126:
                        chat_input += chr(key)
                except: pass
            
            # Chat modundaysak, aşağıdaki Kod Editörü (Normal/Insert) bloklarını çalıştırma!
            continue

        # --- FARE (MOUSE) TIKLAMASI ---
        if key == curses.KEY_MOUSE:
            try:
                # getmouse() -> (id, x, y, z, bstate) döner
                _, mx, my, _, _ = curses.getmouse()
                
                # 1. Durum çubuğuna tıklanırsa işlem yapma
                if my < height - 1:
                    
                    # 2. Gerçek Satırı (Y) Hesapla
                    # Ekrandaki satır + Kaydırma Miktarı
                    target_y = top_line + my
                    
                    # Eğer dosyanın var olan satırlarına tıkladıysa
                    if target_y < len(buffer):
                        cy = target_y
                        
                        # 3. Kenar Boşluğunu (Margin) Hesaba Kat
                        # (Döngüdeki margin_width mantığının aynısı)
                        current_margin = 0
                        if line_number_mode != "NONE":
                            current_margin = 5
                        
                        # 4. Gerçek Sütunu (X) Hesapla
                        # Tıklanan X - Kenar Boşluğu
                        target_x = mx - current_margin
                        
                        # Eğer numaraların üzerine tıkladıysa 0'a sabitle
                        if target_x < 0: 
                            target_x = 0
                        
                        # 5. Satır sonunu aşıyorsa, satır sonuna sabitle
                        line_len = len(buffer[cy])
                        if target_x > line_len:
                            cx = line_len
                        else:
                            cx = target_x
                            
                        # Tıklayınca moda göre mesaj verilebilir (opsiyonel)
                        # message = f"Tıklandı: {cy}:{cx}"
                        
            except curses.error:
                pass
            
            # Fare işlemi bitti, döngünün başına dön ki 'q' falan algılamasın
            continue
        
        # --- NORMAL MOD ---
        if mode == "NORMAL":
            if key == ord('i'):
                history.append((buffer[:], cy, cx))
                mode = "INSERT"
                message = ""
            elif key == ord('q'):
                message = "Çıkmak için :q yazın."
            # --- HAREKET (Hem hjkl hem Ok Tuşları) ---

            # Ctrl+C ile çıkışı engelle veya yönet
            elif key == 3: # Ctrl+C
                message = "Zorla kapatmak için :q kullanın."
            
            # SOL (h veya Sol Ok)
            elif key in [ord('h'), curses.KEY_LEFT]:
                if cx > 0: cx -= 1
            
            # SAĞ (l veya Sağ Ok)
            elif key in [ord('l'), curses.KEY_RIGHT]:
                if cx < len(buffer[cy]): cx += 1
            
            # YUKARI (k veya Yukarı Ok)
            elif key in [ord('k'), curses.KEY_UP]:
                if cy > 0:
                    cy -= 1
                    # Satır sonuna yapışma (Clamping)
                    if cx > len(buffer[cy]): cx = len(buffer[cy])
            
            # AŞAĞI (j veya Aşağı Ok)
            elif key in [ord('j'), curses.KEY_DOWN]:
                if cy < len(buffer) - 1:
                    cy += 1
                    # Satır sonuna yapışma (Clamping)
                    if cx > len(buffer[cy]): cx = len(buffer[cy])

    
            # --- KOPYALA (yy) ---
            elif key == ord('y'):
                # İkinci tuşu bekle (Kullanıcı ikinci kez 'y' basmalı)
                next_key = stdscr.getch()
                if next_key == ord('y'):
                    clipboard = buffer[cy]
                    message = "Satır kopyalandı (yank)."
            
            # --- YAPIŞTIR (p) ---
            elif key == ord('p'):
                if clipboard is None:
                    message = "Pano boş!"
                else:
                    # --- KAYIT NOKTASI 2 ---
                    history.append((buffer[:], cy, cx))
                    
                    buffer.insert(cy + 1, clipboard)
                    cy += 1
                    message = "Yapıştırıldı."            # --- GERİ AL (Undo / Ctrl+Z) ---
            elif key == ord('u') or key == 26: # 26 = Ctrl+Z
                if len(history) > 0:
                    # Tarihçeden son durumu çek
                    # pop() son eklenen elemanı listeden alır ve siler
                    last_buffer, last_cy, last_cx = history.pop()
                    
                    # Mevcut durumu güncelle
                    buffer = last_buffer
                    cy = last_cy
                    cx = last_cx
                    
                    # Satır sayısı değiştiyse ve imleç dışarıda kaldıysa düzelt
                    if cy >= len(buffer): cy = len(buffer) - 1
                    
                    message = "Geri alındı."
                else:
                    message = "Geri alınacak işlem yok (Başlangıç)."
            
            # --- SİL / KES (dd) ---
            elif key == ord('d'):
                # İkinci tuşu bekle
                next_key = stdscr.getch()
                if next_key == ord('d'):
                    # --- KAYIT NOKTASI 3 ---
                    history.append((buffer[:], cy, cx))
                    
                    clipboard = buffer[cy]
                    buffer.pop(cy)
                    if len(buffer) == 0: buffer = [""]
                    elif cy >= len(buffer): cy = len(buffer) - 1
                       
                                           
                    message = "Satır silindi/kesildi (dd)."
            
                    # --- ARAMA (SEARCH) ---
            elif key == ord('/'):
                # 1. UI Hazırlığı
                stdscr.move(height - 1, 0)
                stdscr.clrtoeol()
                stdscr.addstr("/", curses.A_BOLD)
                curses.echo()
                
                # 2. Girdiyi Al
                query_bytes = stdscr.getstr()
                query = query_bytes.decode("utf-8")
                curses.noecho()
                
                if query:
                    last_search = query
                    found = False
                    
                    # 3. Arama Döngüsü
                    # İmlecin olduğu satırdan başla, dosya boyu kadar dön (wrapping dahil)
                    for i in range(len(buffer)):
                        # Şu an bakılacak satırın indeksi (Dairesel döngü)
                        # Örnek: 100 satır var, 90'dayız. 10 satır sonra 100%100 = 0. satıra döner.
                        target_y = (cy + i) % len(buffer)
                        line = buffer[target_y]
                        
                        # Aramaya hangi sütundan başlayacağız?
                        start_col = 0
                        # Eğer aramaya başladığımız (imlecin olduğu) satırdaysak
                        # imlecin 1 sağına bakmalıyız ki aynı kelimede takılı kalmayalım.
                        if target_y == cy:
                            start_col = cx + 1
                        
                        # Python'un 'find' metodu ile ara
                        match_x = line.find(query, start_col)
                        
                        if match_x != -1:
                            # BULUNDU!
                            cy = target_y
                            cx = match_x
                            found = True
                            
                            # Ekranı kaydır (Bulunan satırı ortala)
                            if cy < top_line or cy >= top_line + height - 1:
                                top_line = max(0, cy - (height // 2))
                                
                            message = f"Bulundu: {query}"
                            break # Döngüden çık
                    
                    if not found:
                        message = f"Bulunamadı: {query}"
            
            # --- İLERİ (n) VE GERİ (b) ARAMA ---
            elif key in [ord('n'), ord('b')]:
                if not last_search:
                    message = "Önce bir arama yapın (/)"
                else:
                    found = False
                    # Yön Belirleme: 'n' ise +1 (aşağı), 'b' ise -1 (yukarı)
                    direction = 1 if key == ord('n') else -1
                    
                    # Dairesel Arama Döngüsü
                    # Dosyadaki tüm satırları sırayla gezeceğiz
                    for i in range(len(buffer)):
                        
                        # HEDEF SATIR HESABI
                        # n basınca: (cy + 0), (cy + 1), (cy + 2)...
                        # b basınca: (cy - 0), (cy - 1), (cy - 2)... 
                        # Python'da negatif modüler aritmetik (ör: -1 % 10 = 9) sayesinde
                        # yukarı çıkarken en tepeye gelirsen otomatik en alta (wrap) döner.
                        target_y = (cy + (i * direction)) % len(buffer)
                        line = buffer[target_y]
                        
                        match_x = -1
                        
                        # --- İLERİ ARAMA MANTIĞI (n) ---
                        if key == ord('n'):
                            # Eğer şu an bulunduğumuz satırdaysak, imlecin sağından başla
                            start_col = 0
                            if target_y == cy:
                                start_col = cx + 1
                            
                            # find: Soldan sağa arar
                            match_x = line.find(last_search, start_col)

                        # --- GERİ ARAMA MANTIĞI (b) ---
                        else:
                            # Eğer şu an bulunduğumuz satırdaysak, imlecin soluna bak (0'dan cx'e kadar)
                            end_col = len(line)
                            if target_y == cy:
                                end_col = cx
                            
                            # rfind: Sağdan sola (tersten) arar
                            # 0'dan end_col'a kadar olan kısımda arar
                            match_x = line.rfind(last_search, 0, end_col)
                        
                        # --- SONUÇ KONTROLÜ ---
                        if match_x != -1:
                            cy = target_y
                            cx = match_x
                            found = True
                            
                            # Ekranı bulunan kelimeye ortala
                            if cy < top_line or cy > top_line + height - 2:
                                top_line = max(0, cy - (height // 2))
                            
                            yon_mesaji = "Sonraki" if direction == 1 else "Önceki"
                            message = f"{yon_mesaji}: {last_search}"
                            break # Bulduk, döngüden çık
                    
                    if not found:
                        message = f"Bulunamadı: {last_search}"
            # Komut Modu (:)
            elif key == ord(':'):
                message = ""
                stdscr.move(height - 1, 0)
                stdscr.clrtoeol()
                stdscr.addstr(":", curses.A_BOLD)
                curses.echo()

                # Komutu al
                command_bytes = stdscr.getstr()
                command = command_bytes.decode("utf-8").strip()
                curses.noecho()

                parts = command.split()
                if len(parts) > 0:
                    cmd = parts[0]
                # --- NUMARA KOMUTLARI ---
                    if cmd == 'numara':
                        if len(parts) > 1:
                            subcmd = parts[1]
                            if subcmd == 'goreli':
                                line_number_mode = "RELATIVE"
                                message = "Mod: Göreli Numaralar"
                            elif subcmd == 'normal':
                                line_number_mode = "ABSOLUTE"
                                message = "Mod: Normal Numaralar"
                            elif subcmd == 'yok':
                                line_number_mode = "NONE"
                                message = "Mod: Numaralar Kapalı"
                            else:
                                message = "Hata: goreli, normal veya yok kullanın."
                        else:
                            message = "Kullanım: :numara [goreli|normal|yok]"
                            
                    elif cmd == 'q':
                        return # Çıkış

                    elif cmd == 'w':
                        if len(parts) > 1: filename = parts[1]
                        if filename:
                            try:
                                with open(filename, "w", encoding="utf-8") as f:
                                    f.write("\n".join(buffer))
                                message = f"'{filename}' kaydedildi."
                            except Exception as e:
                                message = f"Hata: {e}"
                        else:
                            message = "Dosya adı yok!"

                    elif cmd == 'wq':
                        if filename:
                            with open(filename, "w", encoding="utf-8") as f:
                                f.write("\n".join(buffer))
                            return
                        else:
                            message = "Dosya adı yok!"
                    else:
                        message = f"Bilinmeyen komut: {cmd}"
                
                # --- PENCERE DEĞİŞTİR (Ctrl+W) ---
            elif key == 23: # Ctrl+W
                if active_window == "CODE":
                    active_window = "CHAT"
                    message = ">> CHAT MODU <<"
                    # İmleci Chat penceresine taşı (Görsel)
                    curses.curs_set(1) 
                else:
                    active_window = "CODE"
                    message = ">> KOD MODU <<"
        # --- INSERT MOD ---
        elif mode == "INSERT":
            if key == 27: # ESC
                mode = "NORMAL"
            # --- INSERT MODUNDA CTRL+Z ---
            elif key == 9: # ASCII 9 = Tab Tuşu
                # 4 tane boşluk ekle
                space_block = "    " 
                line = buffer[cy]
                buffer[cy] = line[:cx] + space_block + line[cx:]
                
                # İmleci 4 birim sağa kaydır
                cx += 4

            elif key == 127 or key == curses.KEY_BACKSPACE:
                if cx > 0:
                    line = buffer[cy]
                    # Sol tarafta 4 tane boşluk var mı kontrol et
                    if cx >= 4 and line[cx-4:cx] == "    ":
                        # Varsa 4'ünü birden sil
                        buffer[cy] = line[:cx-4] + line[cx:]
                        cx -= 4
                    else:
                        # Yoksa normal sil (tek harf)
                        buffer[cy] = line[:cx-1] + line[cx:]
                        cx -= 1
            
            elif key == 26: # Ctrl+Z
                # Normal moda dön ve Undo işlemini tetikle
                mode = "NORMAL"
                # Undo mantığını burada tekrar çağırıyoruz (veya yukarıdaki koda düşmesini sağlıyoruz)
                # En kolayı: Tarihçeden geri yükle
                if len(history) > 0:
                    buffer, cy, cx = history.pop()
                    message = "Geri alındı (Insert iptal)."
            elif key == 10: # Enter
                current_line = buffer[cy]
                buffer[cy] = current_line[:cx]
                buffer.insert(cy + 1, current_line[cx:])
                cy += 1
                cx = 0

            # --- INSERT MODUNDA OK TUŞLARI ---
            elif key == curses.KEY_LEFT:
                if cx > 0: cx -= 1
            
            elif key == curses.KEY_RIGHT:
                if cx < len(buffer[cy]): cx += 1
            
            elif key == curses.KEY_UP:
                if cy > 0:
                    cy -= 1
                    if cx > len(buffer[cy]): cx = len(buffer[cy])
            
            elif key == curses.KEY_DOWN:
                if cy < len(buffer) - 1:
                    cy += 1
                    if cx > len(buffer[cy]): cx = len(buffer[cy])
            
            elif key == 127 or key == curses.KEY_BACKSPACE: # Silme
                if cx > 0:
                    line = buffer[cy]
                    buffer[cy] = line[:cx-1] + line[cx:]
                    cx -= 1
                elif cy > 0:
                    prev_line_len = len(buffer[cy-1])
                    buffer[cy-1] += buffer[cy]
                    buffer.pop(cy)
                    cy -= 1
                    cx = prev_line_len
            else:
                # Yazma
                try:
                    char = chr(key)
                    line = buffer[cy]
                    buffer[cy] = line[:cx] + char + line[cx:]
                    cx += 1
                except ValueError:
                    pass

if __name__ == "__main__":
    curses.wrapper(main)
