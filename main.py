import os
import sqlite3
import random
import requests
from typing import List
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Fazilka SHG Mart API", version="1.0")

# Enable CORS (Storefront connect karne ke liye)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_NAME = "fazilka_shg.db"

# --- Database Initialization ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 1. SHG Members Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS shg_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        shg_name TEXT NOT NULL,
        phone TEXT NOT NULL,
        block TEXT NOT NULL,
        village TEXT NOT NULL
    )
    ''')
    
    # 2. Products Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        price REAL NOT NULL,
        stock INTEGER DEFAULT 10,
        FOREIGN KEY(member_id) REFERENCES shg_members(id)
    )
    ''')
    
    # 3. Orders Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_code TEXT UNIQUE,
        customer_name TEXT,
        customer_phone TEXT,
        address TEXT,
        total_amount REAL,
        status TEXT DEFAULT 'PLACED'
    )
    ''')

    # Seed initial test data if empty
    cursor.execute("SELECT COUNT(*) FROM shg_members")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO shg_members (name, shg_name, phone, block, village) VALUES (?, ?, ?, ?, ?)",
                       ("Manjit Kaur", "Kirpa SHG", "919876543210", "Fazilka", "Salemshah"))
        cursor.execute("INSERT INTO shg_members (name, shg_name, phone, block, village) VALUES (?, ?, ?, ?, ?)",
                       ("Paramjit Kaur", "Noor SHG", "919876543211", "Abohar", "Khuian Sarwar"))
        
        cursor.execute("INSERT INTO products (member_id, title, price, stock) VALUES (?, ?, ?, ?)",
                       (1, "Handmade Achar (Aam)", 180.0, 50))
        cursor.execute("INSERT INTO products (member_id, title, price, stock) VALUES (?, ?, ?, ?)",
                       (2, "Traditional Phulkari Dupatta", 850.0, 10))
    
    conn.commit()
    conn.close()

init_db()

# --- WhatsApp Dispatch Function (Meta API) ---
def send_whatsapp_alert(member_phone: str, member_name: str, order_code: str, customer_name: str, address: str, items: list, subtotal: float):
    token = os.getenv("META_WHATSAPP_TOKEN")
    phone_id = os.getenv("META_PHONE_NUMBER_ID")
    version = os.getenv("META_API_VERSION", "v19.0")
    
    # Format message in Punjabi / Hindi
    items_text = "\n".join([f"- {it['title']} (Qty: {it['quantity']}) - ₹{it['price'] * it['quantity']}" for it in items])
    
    body = (
        f"ਸਤਿ ਸ਼੍ਰੀ ਅਕਾਲ / Namaste {member_name} ਜੀ,\n"
        f"ਫਾਜ਼ਿਲਕਾ SHG ਮਾਰਟ ਤੇ ਨਵਾਂ ਆਰਡਰ ਆਇਆ ਹੈ!\n\n"
        f"🔹 Order ID: #{order_code}\n"
        f"👤 Customer: {customer_name}\n"
        f"📍 Delivery Address: {address}\n\n"
        f"📦 ਸਮਾਨ ਪੈਕ ਕਰੋ (Items):\n{items_text}\n\n"
        f"💰 ਕੁਲ ਰਕਮ (Value): ₹{subtotal}\n\n"
        f"ਕਿਰਪਾ ਕਰਕੇ ਇਹ ਆਰਡਰ ਤਿਆਰ ਰੱਖੋ।"
    )
    
    if not token or not phone_id:
        print(f"[SIMULATED WHATSAPP TO {member_phone}]:\n{body}\n")
        return

    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": member_phone.replace("+", ""),
        "type": "text",
        "text": {"preview_url": False, "body": body}
    }
    
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"WhatsApp sent to {member_phone}: status {res.status_code}")
    except Exception as e:
        print(f"Failed to send WhatsApp to {member_phone}: {str(e)}")


# --- Pydantic Data Models ---
class CartItem(BaseModel):
    product_id: int
    quantity: int

class CheckoutRequest(BaseModel):
    customer_name: str
    customer_phone: str
    delivery_address: str
    items: List[CartItem]


# --- API Routes ---

@app.get("/")
def home():
    return {"message": "Fazilka SHG Mart API 24/7 is Active & Running"}

@app.get("/api/products")
def get_products():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    SELECT p.id, p.title, p.price, p.stock, m.id as member_id, m.name, m.shg_name, m.phone, m.block, m.village
    FROM products p
    JOIN shg_members m ON p.member_id = m.id
    ''')
    rows = cursor.fetchall()
    conn.close()
    
    products = []
    for r in rows:
        products.append({
            "id": r[0], "title": r[1], "price": r[2], "stock": r[3],
            "member_id": r[4], "member_name": r[5], "shg_name": r[6],
            "member_phone": r[7], "block": r[8], "village": r[9]
        })
    return {"success": True, "data": products}

@app.post("/api/checkout")
def checkout(order_data: CheckoutRequest, background_tasks: BackgroundTasks):
    if not order_data.items:
        raise HTTPException(status_code=400, detail="Cart is empty")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 1. Fetch products & vendor mappings
    item_ids = [it.product_id for it in order_data.items]
    placeholders = ",".join("?" for _ in item_ids)
    
    cursor.execute(f'''
    SELECT p.id, p.title, p.price, m.id, m.name, m.phone
    FROM products p
    JOIN shg_members m ON p.member_id = m.id
    WHERE p.id IN ({placeholders})
    ''', item_ids)
    
    prod_data = cursor.fetchall()
    prod_map = {row[0]: {"title": row[1], "price": row[2], "member_id": row[3], "member_name": row[4], "member_phone": row[5]} for row in prod_data}
    
    # 2. Split Order by SHG Member
    member_groups = {}
    total_order_val = 0.0
    
    for item in order_data.items:
        p = prod_map.get(item.product_id)
        if not p:
            continue
        line_total = p["price"] * item.quantity
        total_order_val += line_total
        
        m_id = p["member_id"]
        if m_id not in member_groups:
            member_groups[m_id] = {
                "member_name": p["member_name"],
                "member_phone": p["member_phone"],
                "subtotal": 0.0,
                "items": []
            }
        member_groups[m_id]["subtotal"] += line_total
        member_groups[m_id]["items"].append({
            "title": p["title"],
            "quantity": item.quantity,
            "price": p["price"]
        })
    
    # 3. Save Master Order
    order_code = f"FZK-{random.randint(1000, 9999)}"
    cursor.execute('''
    INSERT INTO orders (order_code, customer_name, customer_phone, address, total_amount)
    VALUES (?, ?, ?, ?, ?)
    ''', (order_code, order_data.customer_name, order_data.customer_phone, order_data.delivery_address, total_order_val))
    conn.commit()
    conn.close()
    
    # 4. Trigger WhatsApp in Background for each respective member
    for m_id, group in member_groups.items():
        background_tasks.add_task(
            send_whatsapp_alert,
            member_phone=group["member_phone"],
            memberName=group["member_name"],
            order_code=order_code,
            customer_name=order_data.customer_name,
            address=order_data.delivery_address,
            items=group["items"],
            subtotal=group["subtotal"]
        )
    
    return {
        "success": True,
        "message": "Order placed successfully! WhatsApp notifications are being sent.",
        "order_code": order_code,
        "total_amount": total_order_val,
        "shg_members_notified": len(member_groups)
    }