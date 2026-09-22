from flask import Flask, render_template, request, redirect, url_for, session, flash
import os
import psycopg
from psycopg.rows import dict_row
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime

app = Flask(__name__, template_folder="Templates")
app.secret_key = os.environ.get('ShoppingBazarHub_SECRET', 'change-this-secret')
DB = os.path.join(os.path.dirname(__file__), 'ShoppingBazarHub.db')
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png','jpg','jpeg','webp','gif'}

STATUSES = ('Pending','Processing','Shipped','Delivered','Cancelled')


def db():
    return psycopg.connect(
        os.environ["DATABASE_URL"],
        row_factory=dict_row
    )


def add_column(c, table, column, definition):
    cols = [
        r['column_name']
        for r in c.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
        """, (table,)).fetchall()
    ]

    if column not in cols:
        c.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def init_db():
    c = db()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'customer',
            approved INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS products(
            id SERIAL PRIMARY KEY,
            supplier_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT,
            supplier_price DOUBLE PRECISION NOT NULL,
            selling_price DOUBLE PRECISION NOT NULL,
            stock INTEGER DEFAULT 0,
            image_url TEXT,
            approved INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            id SERIAL PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            total DOUBLE PRECISION NOT NULL,
            supplier_cost DOUBLE PRECISION NOT NULL,
            margin DOUBLE PRECISION NOT NULL,
            status TEXT DEFAULT 'Pending',
            address TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS order_items(
            id SERIAL PRIMARY KEY,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            supplier_id INTEGER NOT NULL,
            qty INTEGER NOT NULL,
            price DOUBLE PRECISION NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS addresses(
            id SERIAL PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            label TEXT DEFAULT 'Home',
            recipient TEXT NOT NULL,
            phone TEXT,
            address TEXT NOT NULL,
            city TEXT,
            state TEXT,
            pincode TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS reviews(
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id, customer_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS wishlists(
            id SERIAL PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            UNIQUE(customer_id, product_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS coupons(
            id SERIAL PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            discount_percent DOUBLE PRECISION DEFAULT 0,
            active INTEGER DEFAULT 1
        )
    """)

    add_column(c, 'orders', 'forwarded', 'INTEGER DEFAULT 0')
    add_column(c, 'orders', 'coupon_code', 'TEXT')
    add_column(c, 'orders', 'discount', 'DOUBLE PRECISION DEFAULT 0')
    add_column(c, 'orders', 'shipping_partner', 'TEXT')
    add_column(c, 'orders', 'tracking_number', 'TEXT')
    add_column(c, 'orders', 'payment_method', "TEXT DEFAULT 'COD'")
    add_column(c, 'orders', 'payment_status', "TEXT DEFAULT 'Pending'")

    c.execute("""
        CREATE TABLE IF NOT EXISTS order_status_history(
            id SERIAL PRIMARY KEY,
            order_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    admin = c.execute(
        "SELECT id FROM users WHERE email=%s",
        ("admin@ShoppingBazarHub.in",)
    ).fetchone()

    if admin:
        c.execute(
            """
            UPDATE users
            SET password=%s, role=%s, approved=1
            WHERE email=%s
            """,
            (
                generate_password_hash("Admin@123"),
                "admin",
                "admin@ShoppingBazarHub.in"
            )
        )
    else:
        c.execute(
            """
            INSERT INTO users
            (name, email, password, role, approved)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                "ShoppingBazarHub Admin",
                "admin@ShoppingBazarHub.in",
                generate_password_hash("Admin@123"),
                "admin",
                1
            )
        )

    coupon = c.execute(
        "SELECT id FROM coupons WHERE code=%s",
        ("WELCOME10",)
    ).fetchone()

    if not coupon:
        c.execute(
            """
            INSERT INTO coupons
            (code, discount_percent, active)
            VALUES (%s, %s, %s)
            """,
            ("WELCOME10", 10, 1)
        )

    c.commit()
    c.close()
def allowed_file(name):
    return '.' in name and name.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS


def current_user(): return session.get('user')


def role_required(role):
    return current_user() and current_user().get('role') == role


@app.context_processor
def ctx():
    cart=session.get('cart',{})
    count=sum(cart.values()) if isinstance(cart,dict) else len(cart)
    return {'logged':current_user(),'cart_count':count,'year':datetime.now().year}


@app.route('/')
def home():
    q=request.args.get('q','').strip(); category=request.args.get('category','').strip(); sort=request.args.get('sort','newest')
    sql="SELECT p.*,u.name supplier, COALESCE((SELECT AVG(rating) FROM reviews r WHERE r.product_id=p.id),0) rating, (SELECT COUNT(*) FROM reviews r WHERE r.product_id=p.id) review_count FROM products p JOIN users u ON u.id=p.supplier_id WHERE p.approved=1 AND p.stock>0"
    args=[]
    if q: sql += " AND (p.name LIKE ? OR p.description LIKE ? OR p.category LIKE ?)"; args += [f'%{q}%']*3
    if category: sql += " AND p.category=?"; args.append(category)
    sql += {'price_low':' ORDER BY p.selling_price ASC','price_high':' ORDER BY p.selling_price DESC','rating':' ORDER BY rating DESC','newest':' ORDER BY p.id DESC'}.get(sort,' ORDER BY p.id DESC')
    c=db(); products=c.execute(sql,args).fetchall(); categories=c.execute("SELECT DISTINCT category FROM products WHERE approved=1 ORDER BY category").fetchall(); c.close()
    return render_template('home.html',products=products,categories=categories,q=q,category=category,sort=sort)


@app.route('/register',methods=['GET','POST'])
def register():
    if request.method=='POST':
        f=request.form; role=f.get('role','customer')
        if role not in ('customer','supplier'): role='customer'
        approved=1 if role=='customer' else 0
        c=db()
        try:
            c.execute('INSERT INTO users(name,email,password,role,approved) VALUES(%s,%s,%s,%s,%s)'',(f['name'].strip(),f['email'].strip().lower(),generate_password_hash(f['password']),role,approved)); c.commit()
            flash('Supplier account is awaiting admin approval.' if role=='supplier' else 'Account created. Please login.','ok'); return redirect('/login')
        except psycopg.errors.UniqueViolation: flash('Email already registered.','err')
        finally: c.close()
    return render_template('register.html')


@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        f=request.form; c=db(); u=c.execute("SELECT * FROM users WHERE LOWER(email)=LOWER(?)",(f['email'].strip(),)).fetchone()
        if u and check_password_hash(u['password'],f['password']):
            if u['role']=='supplier' and not u['approved']:
                flash('Supplier account is awaiting admin approval.','err'); return redirect('/login')
            session['user']=dict(u); return redirect(request.args.get('next') or '/')
        flash('Invalid email or password.','err')
    return render_template('login.html')


@app.route('/logout')
def logout(): session.clear(); return redirect('/')


@app.route('/account')
def account():
    if not current_user(): return redirect('/login?next=/account')
    c=db(); addresses=c.execute('SELECT * FROM addresses WHERE customer_id=? ORDER BY id DESC',(current_user()['id'],)).fetchall(); orders=c.execute('SELECT * FROM orders WHERE customer_id=? ORDER BY id DESC',(current_user()['id'],)).fetchall(); c.close()
    return render_template('account.html',addresses=addresses,orders=orders)


@app.route('/account/address',methods=['POST'])
def add_address():
    if not role_required('customer'): return redirect('/login')
    f=request.form; c=db(); c.execute('INSERT INTO addresses(customer_id,label,recipient,phone,address,city,state,pincode) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(current_user()['id'],f.get('label','Home'),f['recipient'],f.get('phone',''),f['address'],f.get('city',''),f.get('state',''),f.get('pincode',''))); c.commit(); c.close(); flash('Address saved.','ok'); return redirect('/account')


@app.route('/account/address/<int:aid>/delete')
def delete_address(aid):
    if not role_required('customer'):
        return redirect('/login')
    c=db(); c.execute('DELETE FROM addresses WHERE id=? AND customer_id=?',(aid,current_user()['id'])); c.commit(); c.close()
    flash('Address removed.','ok')
    return redirect('/account')


@app.route('/product/<int:pid>')
def product(pid):
    c=db(); p=c.execute('''SELECT p.*,u.name supplier,COALESCE((SELECT AVG(rating) FROM reviews r WHERE r.product_id=p.id),0) rating,(SELECT COUNT(*) FROM reviews r WHERE r.product_id=p.id) review_count FROM products p JOIN users u ON u.id=p.supplier_id WHERE p.id=? AND p.approved=1''',(pid,)).fetchone(); reviews=c.execute('SELECT r.*,u.name customer_name FROM reviews r JOIN users u ON u.id=r.customer_id WHERE r.product_id=? ORDER BY r.id DESC',(pid,)).fetchall() if p else []; c.close()
    return render_template('product.html',product=p,reviews=reviews) if p else redirect('/')


@app.route('/product/<int:pid>/review',methods=['POST'])
def review(pid):
    if not role_required('customer'): return redirect('/login')
    rating=max(1,min(5,int(request.form.get('rating',5)))); comment=request.form.get('comment','').strip(); c=db()
    bought=c.execute('SELECT 1 FROM order_items oi JOIN orders o ON o.id=oi.order_id WHERE oi.product_id=? AND o.customer_id=? AND o.status != "Cancelled"',(pid,current_user()['id'])).fetchone()
    if not bought: flash('You can review products you have ordered.','err')
    else:
        c.execute('INSERT INTO reviews(product_id,customer_id,rating,comment) VALUES(%s,%s,%s,%s)' ON CONFLICT(product_id,customer_id) DO UPDATE SET rating=excluded.rating,comment=excluded.comment',(pid,current_user()['id'],rating,comment)); c.commit(); flash('Review saved.','ok')
    c.close(); return redirect(f'/product/{pid}')


@app.route('/wishlist')
def wishlist():
    if not role_required('customer'): return redirect('/login')
    c=db(); products=c.execute('SELECT p.*,u.name supplier FROM wishlists w JOIN products p ON p.id=w.product_id JOIN users u ON u.id=p.supplier_id WHERE w.customer_id=? ORDER BY w.id DESC',(current_user()['id'],)).fetchall(); c.close(); return render_template('wishlist.html',products=products)


@app.route('/wishlist/toggle/<int:pid>')
def toggle_wishlist(pid):
    if not role_required('customer'): return redirect('/login')
    c=db(); row=c.execute('SELECT id FROM wishlists WHERE customer_id=? AND product_id=?',(current_user()['id'],pid)).fetchone()
    if row: c.execute('DELETE FROM wishlists WHERE id=?',(row['id'],)); flash('Removed from wishlist.','ok')
    else: c.execute('INSERT INTO wishlists(customer_id,product_id) VALUES(%s,%s) ON CONFLICT (customer_id,product_id) DO NOTHING',(current_user()['id'],pid)); flash('Added to wishlist.','ok')
    c.commit(); c.close(); return redirect(request.referrer or '/')


# CART: stored as {product_id: quantity}
@app.route('/add/<int:pid>')
def add(pid):
    c=db(); p=c.execute('SELECT id,stock FROM products WHERE id=? AND approved=1 AND stock>0',(pid,)).fetchone(); c.close()
    if not p: flash('Product is unavailable.','err'); return redirect(request.referrer or '/')
    cart=session.setdefault('cart',{}); key=str(pid); qty=int(cart.get(key,0))
    if qty < p['stock']: cart[key]=qty+1; session.modified=True; flash('Product added to cart.','ok')
    else: flash('Maximum available stock reached.','err')
    return redirect(request.referrer or '/')


@app.route('/cart')
def cart():
    cart_data=session.get('cart',{})
    if isinstance(cart_data,list):
        converted={}
        for pid in cart_data: converted[str(pid)]=converted.get(str(pid),0)+1
        cart_data=converted; session['cart']=converted; session.modified=True
    c=db(); items=[]; clean={}
    for key,qty in cart_data.items():
        try: pid=int(key); qty=max(1,int(qty))
        except: continue
        p=c.execute('SELECT * FROM products WHERE id=? AND approved=1',(pid,)).fetchone()
        if p and p['stock']>0:
            qty=min(qty,p['stock']); items.append(p); clean[str(pid)]=qty
    c.close(); session['cart']=clean; session.modified=True
    total=sum(p['selling_price']*clean[str(p['id'])] for p in items)
    return render_template('cart.html',products=items,quantities=clean,total=total)


@app.route('/cart/increase/<int:pid>')
def increase_cart(pid): return add(pid)


@app.route('/cart/decrease/<int:pid>')
def decrease_cart(pid):
    cart=session.get('cart',{}); key=str(pid); qty=int(cart.get(key,0))
    if qty>1: cart[key]=qty-1
    elif key in cart: del cart[key]
    session['cart']=cart; session.modified=True; return redirect('/cart')


@app.route('/remove/<int:pid>')
def remove_from_cart(pid):
    cart=session.get('cart',{}); cart.pop(str(pid),None); session['cart']=cart; session.modified=True; flash('Product removed from cart.','ok'); return redirect('/cart')


@app.route('/checkout',methods=['GET','POST'])
def checkout():
    if not role_required('customer'):
        return redirect('/login?next=/checkout')
    cart_data=session.get('cart',{}); c=db(); items=[]
    for key,qty in cart_data.items():
        try: pid=int(key); qty=max(1,int(qty))
        except Exception: continue
        p=c.execute('SELECT * FROM products WHERE id=? AND approved=1 AND stock>0',(pid,)).fetchone()
        if p: items.append((p,min(qty,p['stock'])))
    addresses=c.execute('SELECT * FROM addresses WHERE customer_id=? ORDER BY id DESC',(current_user()['id'],)).fetchall(); c.close()
    if not items:
        flash('Your cart is empty.','err'); return redirect('/cart')
    subtotal=sum(p['selling_price']*qty for p,qty in items); discount=0; coupon=''; payment_method='COD'
    if request.method=='POST':
        coupon=request.form.get('coupon','').strip().upper(); address=request.form.get('address','').strip(); saved=request.form.get('saved_address','').strip(); payment_method=request.form.get('payment_method','COD')
        if saved: address=saved
        if payment_method not in ('COD','DEMO_ONLINE'): payment_method='COD'
        if not address:
            flash('Delivery address is required.','err')
            return render_template('checkout.html',items=items,subtotal=subtotal,discount=0,total=subtotal,addresses=addresses,coupon=coupon,payment_method=payment_method)
        if coupon:
            cc=db(); row=cc.execute('SELECT * FROM coupons WHERE code=? AND active=1',(coupon,)).fetchone(); cc.close()
            if row: discount=round(subtotal*row['discount_percent']/100,2)
            else: flash('Invalid coupon code.','err'); coupon=''
        total=round(subtotal-discount,2); cost=sum(p['supplier_price']*qty for p,qty in items); margin=round(total-cost,2)
        pay_status='Pending' if payment_method=='COD' else 'Demo Paid'
       c = db()

cur = c.execute(
    """
    INSERT INTO orders(
        customer_id,
        total,
        supplier_cost,
        margin,
        status,
        address,
        forwarded,
        coupon_code,
        discount,
        payment_method,
        payment_status
    )
    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    RETURNING id
    """,
    (
        current_user()['id'],
        total,
        cost,
        margin,
        'Pending',
        address,
        0,
        coupon or None,
        discount,
        payment_method,
        pay_status
    )
)

oid = cur.fetchone()['id']
        for p,qty in items:
            c.execute('INSERT INTO order_items(order_id,product_id,supplier_id,qty,price) VALUES(%s,%s,%s,%s,%s)',(oid,p['id'],p['supplier_id'],qty,p['selling_price']))
            c.execute('UPDATE products SET stock=stock-? WHERE id=? AND stock>=?',(qty,p['id'],qty))
        c.execute('INSERT INTO order_status_history(order_id,status,note) VALUES(%s,%s,%s)',(oid,'Pending','Order placed by customer'))
        c.commit(); c.close(); session['cart']={}
        flash(f'Order #SN{oid} placed successfully.','ok'); return redirect('/orders')
    return render_template('checkout.html',items=items,subtotal=subtotal,discount=0,total=subtotal,addresses=addresses,coupon='',payment_method='COD')


@app.route('/orders')
def orders():
    if not role_required('customer'): return redirect('/login')
    c=db(); orders=c.execute('SELECT * FROM orders WHERE customer_id=? ORDER BY id DESC',(current_user()['id'],)).fetchall(); c.close(); return render_template('orders.html',orders=orders)


@app.route('/order/<int:oid>')
def order_detail(oid):
    if not current_user(): return redirect('/login')
    c=db(); o=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
    if not o or (current_user()['role']=='customer' and o['customer_id']!=current_user()['id']): c.close(); return redirect('/')
    items=c.execute('SELECT oi.*,p.name product_name,p.image_url FROM order_items oi JOIN products p ON p.id=oi.product_id WHERE oi.order_id=?',(oid,)).fetchall()
    history=c.execute('SELECT * FROM order_status_history WHERE order_id=? ORDER BY id',(oid,)).fetchall(); c.close()
    return render_template('order_detail.html',order=o,items=items,history=history)
@app.route('/order/<int:oid>/track')
def track_order(oid):
    if not current_user():
        return redirect('/login')

    c = db()

    order = c.execute(
        'SELECT * FROM orders WHERE id=?',
        (oid,)
    ).fetchone()

    if not order or (
        current_user()['role'] == 'customer'
        and order['customer_id'] != current_user()['id']
    ):
        c.close()
        return redirect('/')

    history = c.execute(
        '''
        SELECT *
        FROM order_status_history
        WHERE order_id=?
        ORDER BY id
        ''',
        (oid,)
    ).fetchall()

    c.close()

    return render_template(
        'track_order.html',
        order=order,
        history=history
    )
@app.route('/order/<int:oid>/invoice')
def order_invoice(oid):
    if not current_user():
        return redirect('/login')

    c = db()

    order = c.execute(
        'SELECT * FROM orders WHERE id=?',
        (oid,)
    ).fetchone()

    if not order:
        c.close()
        return redirect('/')

    if current_user()['role'] == 'customer' and order['customer_id'] != current_user()['id']:
        c.close()
        return redirect('/')

    items = c.execute(
        '''
        SELECT oi.*, p.name product_name
        FROM order_items oi
        JOIN products p ON p.id=oi.product_id
        WHERE oi.order_id=?
        ''',
        (oid,)
    ).fetchall()

    c.close()

    return render_template(
        'invoice.html',
        order=order,
        items=items
    )

@app.route('/order/<int:oid>/cancel')
def cancel_order(oid):
    if not role_required('customer'): return redirect('/login')
    c=db(); o=c.execute('SELECT * FROM orders WHERE id=? AND customer_id=?',(oid,current_user()['id'],)).fetchone()
    if o and o['status'] in ('Pending','Processing'):
        for i in c.execute('SELECT * FROM order_items WHERE order_id=?',(oid,)).fetchall(): c.execute('UPDATE products SET stock=stock+? WHERE id=?',(i['qty'],i['product_id']))
        c.execute("UPDATE orders SET status='Cancelled' WHERE id=?",(oid,)); c.execute('INSERT INTO order_status_history(order_id,status,note) VALUES(%s,%s,%s)',(oid,'Cancelled','Cancelled by customer')); c.commit(); flash('Order cancelled. Stock restored.','ok')
    c.close(); return redirect(f'/order/{oid}')
@app.route('/admin/return/<int:oid>/approve')
def approve_return(oid):
    if not role_required('admin'):
        return redirect('/login')

    c = db()

    o = c.execute(
        "SELECT * FROM orders WHERE id=?",
        (oid,)
    ).fetchone()

    if o and o['status'] == 'Return Requested':

        c.execute(
            "UPDATE orders SET status='Return Approved' WHERE id=?",
            (oid,)
        )

        c.execute(
            """
            INSERT INTO order_status_history
            (order_id,status,note)
            VALUES(%s,%s,%s)
            """,
            (
                oid,
                'Return Approved',
                'Return approved by admin'
            )
        )

        c.commit()
        flash('Return approved.', 'ok')

    c.close()

    return redirect('/admin')


@app.route('/admin/return/<int:oid>/reject')
def reject_return(oid):
    if not role_required('admin'):
        return redirect('/login')

    c = db()

    o = c.execute(
        "SELECT * FROM orders WHERE id=?",
        (oid,)
    ).fetchone()

    if o and o['status'] == 'Return Requested':

        c.execute(
            "UPDATE orders SET status='Delivered' WHERE id=?",
            (oid,)
        )

        c.execute(
            """
            INSERT INTO order_status_history
            (order_id,status,note)
            VALUES(%s,%s,%s)
            """,
            (
                oid,
                'Delivered',
                'Return rejected by admin'
            )
        )

        c.commit()
        flash('Return rejected.', 'ok')

    c.close()

    return redirect('/admin')
@app.route('/order/<int:oid>/return')
def return_order(oid):
    if not role_required('customer'):
        return redirect('/login')

    c = db()

    o = c.execute(
        'SELECT * FROM orders WHERE id=? AND customer_id=?',
        (oid, current_user()['id'])
    ).fetchone()

    if o and o['status'] == 'Delivered':

        c.execute(
            "UPDATE orders SET status='Return Requested' WHERE id=?",
            (oid,)
        )

       c.execute(
    '''
    INSERT INTO order_status_history
    (order_id,status,note)
    VALUES(%s,%s,%s)
    ''',
            (
                oid,
                'Return Requested',
                'Return requested by customer'
            )
        )

        c.commit()

        flash('Return request submitted.', 'ok')

    c.close()

    return redirect(f'/order/{oid}')
@app.route('/supplier')
def supplier():
    if not role_required('supplier'): return redirect('/login')
    c=db(); products=c.execute('SELECT * FROM products WHERE supplier_id=? ORDER BY id DESC',(current_user()['id'],)).fetchall(); items=c.execute('''SELECT oi.*,o.status,o.address,o.created_at,o.forwarded,u.name customer_name,p.name product_name,p.supplier_price FROM order_items oi JOIN orders o ON o.id=oi.order_id JOIN users u ON u.id=o.customer_id JOIN products p ON p.id=oi.product_id WHERE oi.supplier_id=? AND o.forwarded=1 ORDER BY o.id DESC''',(current_user()['id'],)).fetchall(); c.close(); return render_template('supplier.html',products=products,items=items)


@app.route('/supplier/product',methods=['POST'])
def add_product():
    if not role_required('supplier'): return redirect('/login')
    f=request.form; image_url=f.get('image_url','').strip(); file=request.files.get('image_file')
    if file and file.filename and allowed_file(file.filename):
        filename=secure_filename(file.filename); base,ext=os.path.splitext(filename); filename=f'{base}_{datetime.now().strftime("%Y%m%d%H%M%S%f")}{ext}'; file.save(os.path.join(app.config['UPLOAD_FOLDER'],filename)); image_url=url_for('static',filename=f'uploads/{filename}')
    c=db(); c.execute('INSERT INTO products(supplier_id,name,category,description,supplier_price,selling_price,stock,image_url) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(current_user()['id'],f['name'].strip(),f['category'].strip(),f.get('description','').strip(),float(f['supplier_price']),float(f['selling_price']),max(0,int(f['stock'])),image_url)); c.commit(); c.close(); flash('Product submitted for admin approval.','ok'); return redirect('/supplier')


@app.route('/supplier/product/<int:pid>/edit',methods=['GET','POST'])
def edit_product(pid):
    if not role_required('supplier'): return redirect('/login')
    c=db(); product=c.execute('SELECT * FROM products WHERE id=? AND supplier_id=?',(pid,current_user()['id'])).fetchone()
    if not product: c.close(); flash('Product not found.','err'); return redirect('/supplier')
    if request.method=='POST':
        f=request.form
        try: sp=float(f['supplier_price']); sell=float(f['selling_price']); stock=max(0,int(f['stock']))
        except (ValueError,KeyError): c.close(); flash('Enter valid price and stock.','err'); return redirect(f'/supplier/product/{pid}/edit')
        c.execute('UPDATE products SET name=?,category=?,description=?,supplier_price=?,selling_price=?,stock=?,approved=0 WHERE id=? AND supplier_id=?',(f['name'].strip(),f['category'].strip(),f.get('description','').strip(),sp,sell,stock,pid,current_user()['id'])); c.commit(); c.close(); flash('Product updated and sent for re-approval.','ok'); return redirect('/supplier')
    c.close(); return render_template('supplier_edit.html',product=product)


@app.route('/supplier/product/<int:pid>/archive')
def archive_product(pid):
    if not role_required('supplier'): return redirect('/login')
    c=db(); c.execute('UPDATE products SET approved=0 WHERE id=? AND supplier_id=?',(pid,current_user()['id'])); c.commit(); c.close(); flash('Product archived from storefront.','ok'); return redirect('/supplier')

# =========================
# SHIPPING / TRACKING
# =========================

def init_shipping_columns():
    c = db()

    add_column(c, 'orders', 'shipping_partner', 'TEXT')
    add_column(c, 'orders', 'tracking_number', 'TEXT')
    add_column(c, 'orders', 'shipped_at', 'TEXT')
    add_column(c, 'orders', 'delivered_at', 'TEXT')

    c.commit()
    c.close()


@app.route('/supplier/order/<int:oid>/shipping', methods=['POST'])
def supplier_shipping(oid):
    if not role_required('supplier'):
        return redirect('/login')

    partner = request.form.get('shipping_partner', '').strip()
    tracking = request.form.get('tracking_number', '').strip()

    if not partner or not tracking:
        flash('Shipping partner and tracking number are required.', 'err')
        return redirect('/supplier')

    c = db()

    ok = c.execute(
        '''
        SELECT 1
        FROM order_items
        WHERE order_id=? AND supplier_id=?
        ''',
        (oid, current_user()['id'])
    ).fetchone()

    if ok:
        from datetime import datetime

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        c.execute(
            '''
            UPDATE orders
            SET shipping_partner=?,
                tracking_number=?,
                shipped_at=?,
                status='Shipped'
            WHERE id=? AND forwarded=1
            ''',
            (partner, tracking, now, oid)
        )

        c.execute(
            '''
            INSERT INTO order_status_history
            (order_id,status,note)
            VALUES(%s,%s,%s)
            ''',
            (
                oid,
                'Shipped',
                f'Shipped by supplier via {partner}. Tracking: {tracking}'
            )
        )

        c.commit()
        flash('Order shipped and tracking number saved.', 'ok')

    c.close()

    return redirect('/supplier')

@app.route('/supplier/order/<int:oid>/<status>', methods=['GET', 'POST'])
def supplier_status(oid, status):
    if not role_required('supplier') or status not in STATUSES[1:]:
        return redirect('/login')

    c = db()

    ok = c.execute(
        'SELECT 1 FROM order_items WHERE order_id=? AND supplier_id=?',
        (oid, current_user()['id'])
    ).fetchone()

    if not ok:
        c.close()
        return redirect('/supplier')

    if status == 'Shipped':
        shipping_partner = request.form.get('shipping_partner', '').strip()
        tracking_number = request.form.get('tracking_number', '').strip()

        if not shipping_partner or not tracking_number:
            c.close()
            flash('Shipping Partner and Tracking Number are required.', 'err')
            return redirect('/supplier')

        c.execute(
            '''
            UPDATE orders
            SET status=?,
                shipping_partner=?,
                tracking_number=?
            WHERE id=? AND forwarded=1
            ''',
            ('Shipped', shipping_partner, tracking_number, oid)
        )

        c.execute(
            '''
            INSERT INTO order_status_history
            (order_id, status, note)
            VALUES (?, ?, ?)
            ''',
            (
                oid,
                'Shipped',
                f'Shipped by supplier via {shipping_partner}. Tracking: {tracking_number}'
            )
        )

    else:
        c.execute(
            '''
            UPDATE orders
            SET status=?
            WHERE id=? AND forwarded=1
            ''',
            (status, oid)
        )

        c.execute(
            '''
            INSERT INTO order_status_history
            (order_id, status, note)
            VALUES (?, ?, ?)
            ''',
            (oid, status, 'Updated by supplier')
        )

        if status == 'Delivered':
            c.execute(
                '''
                UPDATE orders
                SET payment_status =
                    CASE
                        WHEN payment_method='COD'
                        THEN 'Pending Collection'
                        ELSE payment_status
                    END
                WHERE id=?
                ''',
                (oid,)
            )

    c.commit()
    c.close()

    return redirect('/supplier')

@app.route('/admin')
def admin():
    if not role_required('admin'):
        return redirect('/login')

    c = db()

    suppliers = c.execute(
        "SELECT * FROM users WHERE role='supplier' ORDER BY id DESC"
    ).fetchall()

    customers = c.execute(
        "SELECT * FROM users WHERE role='customer' ORDER BY id DESC"
    ).fetchall()

    pending = c.execute(
        """
        SELECT p.*, u.name supplier
        FROM products p
        JOIN users u ON u.id = p.supplier_id
        WHERE p.approved = 0
        ORDER BY p.id DESC
        """
    ).fetchall()

    products = c.execute(
        """
        SELECT p.*, u.name supplier
        FROM products p
        JOIN users u ON u.id = p.supplier_id
        ORDER BY p.id DESC
        """
    ).fetchall()

    orders = c.execute(
        """
        SELECT o.*, u.name customer
        FROM orders o
        JOIN users u ON u.id = o.customer_id
        ORDER BY o.id DESC
        """
    ).fetchall()

    return_requests = c.execute(
        """
        SELECT o.*, u.name customer
        FROM orders o
        JOIN users u ON u.id = o.customer_id
        WHERE o.status = 'Return Requested'
        ORDER BY o.id DESC
        """
    ).fetchall()

    margin = c.execute(
        """
        SELECT COALESCE(SUM(margin), 0) AS m
        FROM orders
        WHERE status != 'Cancelled'
        """
    ).fetchone()['m']

    revenue = c.execute(
        """
        SELECT COALESCE(SUM(total), 0) AS x
        FROM orders
        WHERE status != 'Cancelled'
        """
    ).fetchone()['x']

    coupons = c.execute(
        """
        SELECT *
        FROM coupons
        ORDER BY id DESC
        """
    ).fetchall()

    c.close()

    return render_template(
        'admin.html',
        suppliers=suppliers,
        customers=customers,
        pending=pending,
        products=products,
        orders=orders,
        return_requests=return_requests,
        coupons=coupons,
        margin=margin,
        revenue=revenue
    )

@app.route('/admin/supplier/<int:uid>/approve')
def approve_supplier(uid):
    if not role_required('admin'): return redirect('/login')
    c=db(); c.execute('UPDATE users SET approved=1 WHERE id=? AND role="supplier"',(uid,)); c.commit(); c.close(); return redirect('/admin')


@app.route('/admin/product/<int:pid>/approve')
def approve_product(pid):
    if not role_required('admin'): return redirect('/login')
    c=db(); c.execute('UPDATE products SET approved=1 WHERE id=?',(pid,)); c.commit(); c.close(); return redirect('/admin')


@app.route('/admin/product/<int:pid>/reject')
def reject_product(pid):
    if not role_required('admin'): return redirect('/login')
    c=db(); c.execute('UPDATE products SET approved=0 WHERE id=?',(pid,)); c.commit(); c.close(); return redirect('/admin')


@app.route('/admin/order/<int:oid>/<status>')
def admin_status(oid,status):
    if not role_required('admin') or status not in STATUSES: return redirect('/login')
    c=db(); c.execute('UPDATE orders SET status=? WHERE id=?',(status,oid)); c.execute('INSERT INTO order_status_history(order_id,status,note) VALUES(%s,%s,%s)',(oid,status,'Updated by admin'))(oid,status,'Updated by admin')); c.commit(); c.close(); return redirect('/admin')


@app.route('/admin/order/<int:oid>/forward')
def forward_order(oid):
    if not role_required('admin'): return redirect('/login')
    c=db(); c.execute("UPDATE orders SET forwarded=1,status='Processing' WHERE id=? AND status != 'Cancelled'",(oid,)); c.execute('INSERT INTO order_status_history(order_id,status,note) VALUES(%s,%s,%s)',(oid,'Processing','Forwarded to supplier by admin')); c.commit(); c.close(); flash(f'Order #{oid} forwarded to supplier.','ok'); return redirect('/admin')


@app.route('/admin/coupon',methods=['POST'])
def add_coupon():
    if not role_required('admin'): return redirect('/login')
    code=request.form['code'].strip().upper(); pct=max(0,min(100,float(request.form['discount_percent']))); c=db()
    try: c.execute('INSERT INTO coupons(code,discount_percent,active) VALUES(%s,%s,1)',(code,pct)); c.commit(); flash('Coupon created.','ok')
    except psycopg.errors.UniqueViolation: flash('Coupon already exists.','err')
    finally: c.close()
    return redirect('/admin')


@app.route('/admin/coupon/<int:cid>/toggle')
def toggle_coupon(cid):
    if not role_required('admin'): return redirect('/login')
    c=db(); c.execute('UPDATE coupons SET active=CASE WHEN active=1 THEN 0 ELSE 1 END WHERE id=?',(cid,)); c.commit(); c.close(); flash('Coupon status updated.','ok'); return redirect('/admin')


@app.route('/admin/coupon/<int:cid>/delete')
def delete_coupon(cid):
    if not role_required('admin'): return redirect('/login')
    c=db(); c.execute('DELETE FROM coupons WHERE id=?',(cid,)); c.commit(); c.close(); flash('Coupon deleted.','ok'); return redirect('/admin')


init_db()
init_shipping_columns()
if __name__=='__main__': app.run(debug=True)
