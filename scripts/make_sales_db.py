import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

random.seed(42)  # same data every run

OUT = Path("data/demo/sales.sqlite")
OUT.parent.mkdir(parents=True, exist_ok=True)
if OUT.exists():
    OUT.unlink()

conn = sqlite3.connect(OUT)
cur = conn.cursor()

cur.executescript("""
CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(category_id),
    price REAL NOT NULL,
    cost REAL NOT NULL
);
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    city TEXT NOT NULL,
    country TEXT NOT NULL,
    signup_date TEXT NOT NULL
);
CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date TEXT NOT NULL,
    status TEXT NOT NULL,
    payment_method TEXT NOT NULL
);
CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id),
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL
);
""")

catalog = {
    "Electronics": ["Wireless Earbuds", "Bluetooth Speaker", "Power Bank", "Smart Watch", "USB-C Hub", "Webcam", "Gaming Mouse"],
    "Home & Kitchen": ["Air Fryer", "Mixer Grinder", "Steel Water Bottle", "Cookware Set", "Electric Kettle", "Dinner Set", "Storage Containers"],
    "Fashion": ["Cotton T-Shirt", "Denim Jeans", "Running Shoes", "Hoodie", "Backpack", "Sunglasses", "Leather Wallet"],
    "Books": ["Python Basics", "Data Structures Guide", "Startup Stories", "Budget Planner", "Science Fiction Novel", "History of India", "Cookbook"],
    "Sports": ["Yoga Mat", "Dumbbell Set", "Cricket Bat", "Football", "Skipping Rope", "Resistance Bands", "Badminton Racket"],
    "Beauty": ["Face Wash", "Sunscreen", "Hair Oil", "Moisturizer", "Perfume", "Lip Balm", "Shampoo"],
}

product_rows = []
pid = 1
for cid, (cat, names) in enumerate(catalog.items(), start=1):
    cur.execute("INSERT INTO categories VALUES (?, ?)", (cid, cat))
    for n in names:
        price = round(random.uniform(150, 6000), 2)
        cost = round(price * random.uniform(0.45, 0.8), 2)
        cur.execute("INSERT INTO products VALUES (?, ?, ?, ?, ?)", (pid, n, cid, price, cost))
        product_rows.append((pid, price))
        pid += 1

first_names = ["Aarav", "Vivaan", "Aditya", "Ananya", "Diya", "Ishaan", "Kavya", "Rohan", "Sneha", "Arjun",
               "Meera", "Rahul", "Priya", "Neha", "Karan", "Pooja", "Siddharth", "Riya", "Manish", "Tanvi",
               "John", "Emma", "Liam", "Sophia", "Noah", "Olivia", "Lucas", "Mia"]
last_names = ["Sharma", "Verma", "Gupta", "Patel", "Singh", "Das", "Roy", "Reddy", "Iyer", "Nair",
              "Mehta", "Joshi", "Khan", "Bose", "Smith", "Brown", "Taylor", "Wilson"]
places = [
    ("Mumbai", "India"), ("Delhi", "India"), ("Bengaluru", "India"), ("Kolkata", "India"),
    ("Chennai", "India"), ("Hyderabad", "India"), ("Pune", "India"), ("Kharagpur", "India"),
    ("London", "UK"), ("Manchester", "UK"), ("New York", "USA"), ("Austin", "USA"),
    ("Toronto", "Canada"), ("Sydney", "Australia"),
]
place_weights = [14, 12, 14, 10, 8, 8, 8, 3, 4, 2, 5, 3, 3, 2]

END = date(2024, 12, 31)

def rand_date(start, end):
    return start + timedelta(days=random.randint(0, (end - start).days))

customers = []
for i in range(1, 301):
    name = f"{random.choice(first_names)} {random.choice(last_names)}"
    email = f"user{i}@example.com"
    city, country = random.choices(places, weights=place_weights)[0]
    signup = rand_date(date(2022, 6, 1), date(2024, 9, 30))
    customers.append((i, signup))
    cur.execute("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)",
                (i, name, email, city, country, signup.isoformat()))

statuses = ["delivered", "shipped", "cancelled", "returned"]
status_weights = [80, 5, 8, 7]
payments = ["card", "upi", "cod", "netbanking"]
payment_weights = [35, 40, 15, 10]

item_id = 1
for oid in range(1, 3001):
    cust_id, signup = random.choice(customers)
    odate = rand_date(signup, END)
    status = random.choices(statuses, weights=status_weights)[0]
    pay = random.choices(payments, weights=payment_weights)[0]
    cur.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?)",
                (oid, cust_id, odate.isoformat(), status, pay))
    for _ in range(random.randint(1, 4)):
        p_id, price = random.choice(product_rows)
        qty = random.randint(1, 3)
        cur.execute("INSERT INTO order_items VALUES (?, ?, ?, ?, ?)",
                    (item_id, oid, p_id, qty, price))
        item_id += 1

conn.commit()
conn.close()
print("Created", OUT)