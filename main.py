from typing import Final
import uuid
import sqlite3
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler, ConversationHandler
)
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv('TELEGRAM_TOKEN')

admin_raw = os.getenv('ADMIN_IDS', '')
SHOPKEEPER_IDS = [int(i) for i in admin_raw.split(',') if i]



MENU = {
    'acai': {'name': 'Classic Acai Bowl', 'price': 6.00},
    'banana': {'name': 'Banana Pudding Acai', 'price': 7.00},
}

MENU_STATE, GRANOLA_STATE, DRIZZLE_STATE, REQUEST_STATE = range(4)


def init_db():
    conn = sqlite3.connect('acai_bot.db')
    c = conn.cursor()
    #orders table
    c.execute('''CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY, 
                    customer_id INTEGER, 
                    customer_name TEXT, 
                    items TEXT, 
                    total REAL, 
                    status TEXT)''')
    #settings table
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, 
                    value TEXT)''')
    
    #default shop status
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('shop_open', '1')")
    conn.commit()
    conn.close()

def is_shop_open() -> bool:
    conn = sqlite3.connect('acai_bot.db')
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='shop_open'")
    result = c.fetchone() #returns list
    conn.close()
    return result[0] == '1' if result else True

def set_shop_open(is_open: bool):
    conn = sqlite3.connect('acai_bot.db')
    c = conn.cursor()
    c.execute("UPDATE settings SET value=? WHERE key='shop_open'", ('1' if is_open else '0',))
    conn.commit()
    conn.close()

def add_order(order_id, customer_id, customer_name, items, total):
    conn = sqlite3.connect('acai_bot.db')
    c = conn.cursor()
    #items as json string
    c.execute("""INSERT INTO orders (id, customer_id, customer_name, items, total, status) 
                 VALUES (?, ?, ?, ?, ?, 'pending')""",
              (order_id, customer_id, customer_name, json.dumps(items), total))
    conn.commit()
    conn.close()

def get_pending_orders():
    conn = sqlite3.connect('acai_bot.db')
    conn.row_factory = sqlite3.Row #format like dict
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE status='pending'")
    rows = c.fetchall()
    conn.close()
    
    orders = []
    for r in rows:
        orders.append({
            'id': r['id'],
            'customer_id': r['customer_id'],
            'customer_name': r['customer_name'],
            'items': json.loads(r['items']), # json string to list of dicts
            'total': r['total']
        })
    return orders

def get_order(order_id):
    conn = sqlite3.connect('acai_bot.db')
    conn.row_factory = sqlite3.Row #format like dict
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE id=?", (order_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            'id': row['id'],
            'customer_id': row['customer_id'],
            'customer_name': row['customer_name'],
            'items': json.loads(row['items']),
            'total': row['total']
        }
    return None

def mark_order_served(order_id):
    conn = sqlite3.connect('acai_bot.db')
    c = conn.cursor()
    c.execute("UPDATE orders SET status='served' WHERE id=?", (order_id,))
    conn.commit()
    conn.close()

###################################################################################################

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    #entry pt in convhandler
    if not is_shop_open():
        await update.message.reply_text(
            "🔴 **ACAILABILITY IS CLOSED** 🔴\n\nSorry, we are not accepting new orders right now :(\nIn the meantime, look out for the next drop in the telegram chat!",
            parse_mode='Markdown'
        )
        return ConversationHandler.END

    if 'cart' not in context.user_data:
        context.user_data['cart'] = []

    keyboard = [
        [
            InlineKeyboardButton("🍓 Classic Acai Bowl", callback_data='menu_acai'),
            InlineKeyboardButton("🍌 Banana Pudding Acai", callback_data='menu_banana')
        ],
        [InlineKeyboardButton("🛒 View Cart / Checkout", callback_data='view_cart')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👋 Hello and welcome to Acailability! \n\nPlease select an item from the menu below to start your order:",
        reply_markup=reply_markup
    )
    return MENU_STATE

async def toggle_shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in SHOPKEEPER_IDS:
        await update.message.reply_text("⛔️ Access Denied: You are not Melvin.")
        return
    
    currently_open = is_shop_open()
    new_status = not currently_open
    set_shop_open(new_status)
    
    status_icon = "🟢" if new_status else "🔴"
    status_text = "OPEN" if new_status else "CLOSED"
    
    await update.message.reply_text(f"✅ Shop status updated: **{status_text}** {status_icon}", parse_mode='Markdown')

async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in SHOPKEEPER_IDS:
        await update.message.reply_text("⛔️ Access Denied: You are not Melvin.")
        return

    pending_orders = get_pending_orders()
    if not pending_orders:
        await update.message.reply_text("✅ The queue is currently empty!")
        return

    await update_queue_display(update, context, is_new_message=True)

async def update_queue_display(update: Update, context: ContextTypes.DEFAULT_TYPE, is_new_message=False):
    pending_orders = get_pending_orders()
    
    if not pending_orders:
        text = "✅ All orders served! The queue is empty."
        reply_markup = None
        if is_new_message:
            await update.message.reply_text(text)
        else:
            await update.callback_query.edit_message_text(text)
        return

    text = "📋 **Active Order Queue**\n\n"
    keyboard = []

    for order in pending_orders:
        text += f"🆔 **#{order['id']}** | 👤 {order['customer_name']}\n"
        for item in order['items']:
            text += f" - {item['name']}\n"
            if item.get('request'):
                text += f"   ⚠️ *Note:* {item['request']}\n"
                
        text += f"💰 Total: ${order['total']:.2f}\n"
        text += "-------------------\n"
        
        keyboard.append([InlineKeyboardButton(f"✅ Serve Order #{order['id']}", callback_data=f"serve_{order['id']}")])

    keyboard.append([InlineKeyboardButton("🔄 Refresh Queue", callback_data="refresh_queue")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if is_new_message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_menu_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == 'view_cart':
        return await handle_cart(update, context)

    item_key = data.replace('menu_', '')
    item = MENU.get(item_key)
    
    if not item:
        if item_key == 'banana':
             await query.answer("This item is currently unavailable.", show_alert=True)
        return MENU_STATE

    if item_key == 'acai':
        context.user_data['current_customization'] = {
            'name': item['name'],
            'price': item['price'],
            'granola': None,
            'drizzle': None,
            'request': None #initialize empty request
        }
        
        granola_buttons = [
            [InlineKeyboardButton("Choco Banana 🍫🍌", callback_data='granola_choco_banana')],
            [InlineKeyboardButton("Maple Syrup 🍁", callback_data='granola_maple')],
            [InlineKeyboardButton("Matcha 🍵", callback_data='granola_matcha')],
            [InlineKeyboardButton("Strawberry 🍓", callback_data='granola_strawberry')], 
        ]
        
        await query.edit_message_text(
            text=f"Customizing **{item['name']}**\nChoose your Granola!",
            reply_markup=InlineKeyboardMarkup(granola_buttons),
            parse_mode='Markdown'
        )
        return GRANOLA_STATE

    else:
        context.user_data['cart'].append(item)
        return await show_menu_again(update, context, f"✅ Added **{item['name']}** to cart!")


async def handle_granola(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    choice = data.replace('granola_', '').replace('_', ' ').title()
    context.user_data['current_customization']['granola'] = choice
    
    drizzle_buttons = [
        [InlineKeyboardButton("Hazelnut 🌰", callback_data='drizzle_hazelnut')],
        [InlineKeyboardButton("Peanut 🥜", callback_data='drizzle_peanut')],
        [InlineKeyboardButton("Honey 🍯", callback_data='drizzle_honey')],
        [InlineKeyboardButton("Cookie 🍪", callback_data='drizzle_cookie')],
    ]
    
    await query.edit_message_text(
        text=f"Now choose your Drizzle!",
        reply_markup=InlineKeyboardMarkup(drizzle_buttons),
        parse_mode='Markdown'
    )
    return DRIZZLE_STATE

async def handle_drizzle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    choice = data.replace('drizzle_', '').title()
    context.user_data['current_customization']['drizzle'] = choice
    
    keyboard = [[InlineKeyboardButton("⏭ Skip / No Requests", callback_data='skip_request')]]
    
    await query.edit_message_text(
        text="📝 **Any special requests/messages for Melvin?**\nText it below (e.g., 'No bananas', 'I love Melvin'), or click the skip button :)",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return REQUEST_STATE


async def handle_special_request_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if len(user_text) > 100:
        user_text = user_text[:100] + "..."

    context.user_data['current_customization']['request'] = user_text
    return await finalize_custom_item(update, context)

async def handle_special_request_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # No request text needed
    return await finalize_custom_item(update, context)

async def finalize_custom_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    temp_item = context.user_data['current_customization']
    
    final_name = f"{temp_item['name']} ({temp_item['granola']}, {temp_item['drizzle']} Drizzle)"
    
    final_item = {
        'name': final_name,
        'price': temp_item['price'],
        'request': temp_item.get('request')
    }
    
    context.user_data['cart'].append(final_item)
    
    #clean up temp data
    del context.user_data['current_customization']
    
    success_msg = f"✅ Added **{final_name}** to cart!"
    if temp_item.get('request'):
        success_msg += f"\n📝 Note: {temp_item['request']}"

    if update.callback_query: #true if they hit skip
        return await show_menu_again(update, context, success_msg)

    else:
        return await show_menu_again_new_msg(update, context, success_msg)

#no special req
async def show_menu_again(update, context, message_text):
    total_price = sum(i['price'] for i in context.user_data['cart'])
    
    keyboard = [
        [InlineKeyboardButton("🍓 Classic Acai Bowl", callback_data='menu_acai')],
        [InlineKeyboardButton("🛒 View Cart / Checkout", callback_data='view_cart')]
    ]
    
    await update.callback_query.edit_message_text(
        text=f"{message_text}\n\nCurrent Total: ${total_price:.2f}\nWhat else would you like?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return MENU_STATE

#special req
async def show_menu_again_new_msg(update, context, message_text):
    total_price = sum(i['price'] for i in context.user_data['cart'])
    
    keyboard = [
        [InlineKeyboardButton("🍓 Classic Acai Bowl", callback_data='menu_acai')],
        [InlineKeyboardButton("🛒 View Cart / Checkout", callback_data='view_cart')]
    ]
    
    await update.message.reply_text(
        text=f"{message_text}\n\nCurrent Total: ${total_price:.2f}\nWhat else would you like?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return MENU_STATE

async def handle_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()

    cart = context.user_data.get('cart', [])
    
    if not cart:
        text = "Your cart is empty!"
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_main')]]
    else:
        text = "🛒 **Your Cart:**\n\n"
        for item in cart:
            text += f"• {item['name']} - ${item['price']:.2f}\n"
            if item.get('request'):
                text += f"   📝 *Note:* {item['request']}\n"
                
        text += f"\n**Total: ${sum(i['price'] for i in cart):.2f}**"
        
        keyboard = [
            [InlineKeyboardButton("✅ Checkout", callback_data='checkout')],
            [InlineKeyboardButton("❌ Remove Item", callback_data='remove_menu')],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_main')]
        ]

    if update.callback_query:
        await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        
    return MENU_STATE

async def handle_remove_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, should_answer=True):
    query = update.callback_query
    if should_answer: await query.answer()
    
    cart = context.user_data.get('cart', [])
    if not cart: return await handle_cart(update, context)

    keyboard = []
    for i, item in enumerate(cart):
        keyboard.append([InlineKeyboardButton(f"❌ {item['name']}", callback_data=f"delete_{i}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back to Cart", callback_data='view_cart')])
    
    await query.edit_message_text(
        text="🗑 **Tap an item to remove it:**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return MENU_STATE

async def handle_delete_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    try:
        index_to_delete = int(data.replace('delete_', ''))
        cart = context.user_data.get('cart', [])
        
        if 0 <= index_to_delete < len(cart):
            removed = cart.pop(index_to_delete)
            await query.answer(f"Removed {removed['name']}", show_alert=False)
            if not cart:
                return await handle_cart(update, context)
            else:
                return await handle_remove_menu(update, context, should_answer=True)
        else:
            await query.answer("Item not found.", show_alert=True)
            return await handle_remove_menu(update, context, should_answer=False)
    except ValueError:
        return MENU_STATE

async def handle_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    cart = context.user_data.get('cart', [])
    total_price = sum(i['price'] for i in cart)
    order_id = str(uuid.uuid4())[:5]

    customer_id = update.effective_user.id
    customer_name = update.effective_user.username or update.effective_user.first_name

    # save to db
    add_order(order_id, customer_id, customer_name, list(cart), total_price)

    order_summary = ""
    for item in cart:
        order_summary += f"- {item['name']} (${item['price']:.2f})\n"
        if item.get('request'):
            order_summary += f"   ⚠️ Note: {item['request']}\n"

    shopkeeper_msg = (
        f"🚨 **NEW ORDER RECEIVED** 🚨\n"
        f"🆔 Order #{order_id}\n"
        f"👤 Customer: @{customer_name}\n"
        f"💰 Total: ${total_price:.2f}\n\n"
        f"Items:\n{order_summary}\n"
        f"Use /queue to manage orders."
    )
    
    for admin_id in SHOPKEEPER_IDS:
        try: #just in case admin cant get msg
            await context.bot.send_message(chat_id=admin_id, text=shopkeeper_msg, parse_mode='Markdown')
        except Exception:
            pass
    
    context.user_data['cart'] = []
    
    keyboard = [[InlineKeyboardButton("🆕 Order Again", callback_data='back_to_main')]]
    await query.edit_message_text(
        text=f"🎉 **Order Confirmed!** 🎉\nOrder ID: #{order_id}\n\nWe have received your order.\nTotal: **${total_price:.2f}**\n\nThank you for shopping with Acailability!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return MENU_STATE

async def handle_queue_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'refresh_queue':
        await update_queue_display(update, context, is_new_message=False)
        return

    if data.startswith('serve_'):
        order_id = data.replace('serve_', '')
        
        order_to_serve = get_order(order_id) #pull from db
        
        if order_to_serve:
            try:
                await context.bot.send_message(
                    chat_id=order_to_serve['customer_id'],
                    text=f"🥣 Order #{order_id} is ready!\nThank you for ordering with Acailability! 🍓🍌",
                    parse_mode='Markdown'
                )
            except Exception:
                pass

            mark_order_served(order_id) #mark served in db
            
            for admin_id in SHOPKEEPER_IDS:
                try:
                    await context.bot.send_message(chat_id=admin_id, text=f"✅ Order #{order_id} marked served.")
                except Exception:
                    pass
        
        await update_queue_display(update, context, is_new_message=False)

async def back_to_main_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await show_menu_again(update, context, "👋 Welcome back!")

async def error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f'Update {update} caused error {context.error}')

if __name__ == '__main__':
    print('Initializing database...')
    init_db()  #initialise db on start

    print('Starting bot...')
    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            MENU_STATE: [
                CallbackQueryHandler(handle_menu_selection, pattern='^menu_'),
                CallbackQueryHandler(handle_cart, pattern='^view_cart$'),
                CallbackQueryHandler(handle_remove_menu, pattern='^remove_menu$'), 
                CallbackQueryHandler(handle_delete_item, pattern='^delete_'),      
                CallbackQueryHandler(handle_checkout, pattern='^checkout$'),
                CallbackQueryHandler(back_to_main_handler, pattern='^back_to_main$')
            ],
            GRANOLA_STATE: [CallbackQueryHandler(handle_granola, pattern='^granola_')],
            DRIZZLE_STATE: [CallbackQueryHandler(handle_drizzle, pattern='^drizzle_')],
            REQUEST_STATE: [
                CallbackQueryHandler(handle_special_request_skip, pattern='^skip_request$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_special_request_text)
            ]
        },
        fallbacks=[CommandHandler('start', start_command)]
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler('queue', queue_command))
    app.add_handler(CommandHandler('toggleshop', toggle_shop_command))
    app.add_handler(CallbackQueryHandler(handle_queue_action, pattern='^(serve_|refresh_queue)'))
    app.add_error_handler(error)

    print('Polling...')
    app.run_polling(poll_interval=0.1)