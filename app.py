#!/usr/bin/env python3
"""
Instagram Report Telegram Bot
"""

import requests
import time
import random
import json
import logging
import asyncio
import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import threading

# Config import
from config import TELEGRAM_BOT_TOKEN, ADMIN_IDS, DB_PATH, MAX_REPORTS_PER_USER, INSTAGRAM_ACCOUNTS

# === VERİTABANI ===
class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                reports_used INTEGER DEFAULT 0,
                banned BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                target_username TEXT,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        self.conn.commit()
    
    def get_user(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return cursor.fetchone()
    
    def create_user(self, user_id, username):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username)
        )
        self.conn.commit()
    
    def increment_reports(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE users SET reports_used = reports_used + 1 WHERE user_id = ?",
            (user_id,)
        )
        self.conn.commit()
    
    def add_report(self, user_id, target_username, status):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO reports (user_id, target_username, status) VALUES (?, ?, ?)",
            (user_id, target_username, status)
        )
        self.conn.commit()

# === INSTAGRAM BOT ===
class InstagramReporter:
    def __init__(self):
        self.session = requests.Session()
        self.setup_session()
    
    def setup_session(self):
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Origin': 'https://www.instagram.com',
            'Referer': 'https://www.instagram.com/',
            'X-IG-App-ID': '936619743392459',
            'X-Requested-With': 'XMLHttpRequest',
        })
    
    def login(self, username, password):
        """Instagram'a giriş yap"""
        try:
            # CSRF token al
            response = self.session.get('https://www.instagram.com/accounts/login/')
            csrf_token = response.cookies.get('csrftoken')
            
            login_data = {
                'username': username,
                'enc_password': f'#PWD_INSTAGRAM_BROWSER:0:{int(time.time())}:{password}',
                'queryParams': '{}',
                'optIntoOneTap': 'false',
            }
            
            headers = {
                'X-CSRFToken': csrf_token,
                'X-Instagram-AJAX': '1',
                'X-IG-App-ID': '936619743392459',
            }
            
            response = self.session.post(
                'https://www.instagram.com/accounts/login/ajax/',
                data=login_data,
                headers=headers
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('authenticated'):
                    logging.info(f"✅ Başarılı giriş: {username}")
                    return True
            
            logging.error(f"❌ Giriş başarısız: {username}")
            return False
            
        except Exception as e:
            logging.error(f"Giriş hatası: {e}")
            return False
    
    def get_user_id(self, username):
        """Kullanıcı ID'sini al"""
        try:
            response = self.session.get(f'https://www.instagram.com/{username}/?__a=1')
            if response.status_code == 200:
                data = response.json()
                return data['graphql']['user']['id']
        except Exception as e:
            logging.error(f"ID alma hatası: {e}")
        return None
    
    def report_user(self, target_username, reason="spam"):
        """Kullanıcıyı rapor et"""
        try:
            user_id = self.get_user_id(target_username)
            if not user_id:
                return False, "Kullanıcı bulunamadı"
            
            report_data = {
                'source_name': '',
                'reason_id': self.get_reason_id(reason),
                'user_id': user_id,
            }
            
            response = self.session.post(
                'https://www.instagram.com/users/report/',
                data=report_data
            )
            
            if response.status_code == 200:
                logging.info(f"✅ Rapor gönderildi: {target_username}")
                return True, "Rapor başarıyla gönderildi"
            else:
                return False, f"Hata: {response.status_code}"
                
        except Exception as e:
            logging.error(f"Raporlama hatası: {e}")
            return False, f"Hata: {str(e)}"
    
    def get_reason_id(self, reason):
        reasons = {
            "spam": 1,
            "fake": 2,
            "abuse": 3,
        }
        return reasons.get(reason, 1)

# === TELEGRAM BOT ===
class TelegramReportBot:
    def __init__(self):
        self.db = Database()
        self.instagram = InstagramReporter()
        self.application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.accounts = INSTAGRAM_ACCOUNTS
        self.current_account_index = 0
        self.setup_handlers()
    
    def setup_handlers(self):
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("report", self.report_command))
        self.application.add_handler(CommandHandler("stats", self.stats))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.create_user(user.id, user.username)
        
        welcome_text = """
🤖 **Instagram Report Bot**

Özellikler:
✅ Otomatik raporlama
✅ Çoklu hesap desteği  
✅ Güvenli ve hızlı

Komutlar:
/report - Kullanıcı raporla
/stats - İstatistikleriniz
/help - Yardım

⚠️ *Sadece yasal amaçlar için kullanın*
        """
        
        keyboard = [
            [InlineKeyboardButton("📊 Rapor Gönder", callback_data="report")],
            [InlineKeyboardButton("📈 İstatistikler", callback_data="stats")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def report_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "📝 Raporlamak istediğiniz Instagram kullanıcı adını giriniz:\n\n"
            "Örnek: `instagram_username`",
            parse_mode='Markdown'
        )
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        target_username = update.message.text.strip()
        
        if target_username.startswith('/'):
            return
            
        # Kullanıcı kontrolü
        user_data = self.db.get_user(user.id)
        if not user_data:
            await update.message.reply_text("❌ Lütfen önce /start komutunu kullanın")
            return
        
        if user_data[3]:  # banned
            await update.message.reply_text("❌ Hesabınız askıya alınmıştır")
            return
        
        if user_data[2] >= MAX_REPORTS_PER_USER:
            await update.message.reply_text("❌ Günlük rapor limitine ulaştınız")
            return
        
        # Rapor işlemini başlat
        processing_msg = await update.message.reply_text("🔄 Rapor işlemi başlatılıyor...")
        
        # Thread'de raporlama işlemi
        thread = threading.Thread(
            target=self.process_report,
            args=(user.id, target_username, processing_msg.message_id, update.effective_chat.id)
        )
        thread.start()
    
    def process_report(self, user_id, target_username, message_id, chat_id):
        """Raporlama işlemini thread'de gerçekleştir"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def async_process():
            try:
                # Instagram hesabı seç
                account = self.get_next_account()
                if not account:
                    await self.edit_message(chat_id, message_id, "❌ Aktif Instagram hesabı bulunamadı")
                    return
                
                await self.edit_message(chat_id, message_id, f"🔐 Giriş yapılıyor: {account['username']}...")
                
                # Giriş yap
                if not self.instagram.login(account['username'], account['password']):
                    await self.edit_message(chat_id, message_id, "❌ Instagram girişi başarısız")
                    return
                
                await self.edit_message(chat_id, message_id, "📨 Rapor gönderiliyor...")
                
                # Rapor et
                success, message = self.instagram.report_user(target_username)
                
                if success:
                    self.db.increment_reports(user_id)
                    self.db.add_report(user_id, target_username, "success")
                    await self.edit_message(chat_id, message_id, f"✅ **Başarılı!**\n\nKullanıcı: `{target_username}`\nDurum: Rapor gönderildi\nHesap: {account['username']}")
                else:
                    self.db.add_report(user_id, target_username, f"failed: {message}")
                    await self.edit_message(chat_id, message_id, f"❌ **Başarısız**\n\nKullanıcı: `{target_username}`\nHata: {message}")
                    
            except Exception as e:
                logging.error(f"Rapor işleme hatası: {e}")
                await self.edit_message(chat_id, message_id, f"❌ Sistem hatası: {str(e)}")
        
        loop.run_until_complete(async_process())
        loop.close()
    
    def get_next_account(self):
        """Sıradaki Instagram hesabını al"""
        if not self.accounts:
            return None
        
        account = self.accounts[self.current_account_index]
        self.current_account_index = (self.current_account_index + 1) % len(self.accounts)
        return account
    
    async def edit_message(self, chat_id, message_id, text):
        """Mesajı düzenle"""
        try:
            await self.application.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode='Markdown'
            )
        except Exception as e:
            logging.error(f"Mesaj düzenleme hatası: {e}")
    
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_data = self.db.get_user(user.id)
        
        if user_data:
            stats_text = f"""
📊 **Kullanıcı İstatistikleri**

👤 Kullanıcı: {user_data[1] or 'N/A'}
📨 Gönderilen Raporlar: {user_data[2]}
📅 Kayıt Tarihi: {user_data[4]}
🎯 Kalan Rapor: {MAX_REPORTS_PER_USER - user_data[2]}
            """
            await update.message.reply_text(stats_text, parse_mode='Markdown')
        else:
            await update.message.reply_text("❌ Kullanıcı bulunamadı. /start komutunu kullanın")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = f"""
ℹ️ **Instagram Report Bot Yardım**

**Komutlar:**
/start - Botu başlat
/report - Kullanıcı raporla  
/stats - İstatistiklerinizi görün
/help - Yardım

**Kullanım:**
1. /report komutunu kullanın
2. Instagram kullanıcı adını gönderin
3. Bot otomatik raporu gönderir

**⚠️ Bilgiler:**
- Günlük rapor limiti: {MAX_REPORTS_PER_USER}
- Aktif hesaplar: {len(self.accounts)}
- Sadece yasal amaçlar için kullanın
        """
        
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    def run(self):
        """Botu çalıştır"""
        logging.info("Telegram Bot başlatılıyor...")
        self.application.run_polling()

# === ANA PROGRAM ===
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    print("🤖 Instagram Report Telegram Bot")
    print(f"📊 {len(INSTAGRAM_ACCOUNTS)} Instagram hesabı yüklendi")
    
    bot = TelegramReportBot()
    bot.run()
