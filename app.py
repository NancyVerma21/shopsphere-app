import os
import re
import secrets
from decimal import Decimal
from functools import wraps

import mysql.connector
from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__)

app.config["SECRET_KEY"] = os.getenv(
    "SECRET_KEY",
    "shopsphere-development-secret-key-2026",
)

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

SHIPPING_THRESHOLD = Decimal("1000.00")
SHIPPING_CHARGE = Decimal("99.00")


# -----------------------------------------------------------------------------
# DATABASE
# -----------------------------------------------------------------------------


def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
    )


# -----------------------------------------------------------------------------
# AUTH HELPERS
# -----------------------------------------------------------------------------


def login_required(view_function):
    @wraps(view_function)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "error")
            next_url = request.path if request.method == "GET" else url_for("cart")
            return redirect(url_for("login", next=next_url))

        return view_function(*args, **kwargs)

    return wrapped_view


# -----------------------------------------------------------------------------
# CART HELPERS
# -----------------------------------------------------------------------------


def get_or_create_active_cart(connection, user_id):
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT id, status
            FROM carts
            WHERE user_id = %s
            LIMIT 1
            FOR UPDATE
            """,
            (user_id,),
        )

        cart = cursor.fetchone()

        if cart:
            if cart["status"] != "active":
                cursor.execute(
                    """
                    UPDATE carts
                    SET status = 'active'
                    WHERE id = %s
                    """,
                    (cart["id"],),
                )

            return cart["id"]

        cursor.execute(
            """
            INSERT INTO carts (user_id, status)
            VALUES (%s, 'active')
            """,
            (user_id,),
        )

        return cursor.lastrowid

    finally:
        cursor.close()


def calculate_shipping(subtotal):
    if subtotal <= Decimal("0.00"):
        return Decimal("0.00")

    if subtotal > SHIPPING_THRESHOLD:
        return Decimal("0.00")

    return SHIPPING_CHARGE


def create_order_number():
    return f"SS-{secrets.token_hex(6).upper()}"


def create_transaction_reference(prefix="TXN"):
    return f"{prefix}-{secrets.token_hex(8).upper()}"


def get_active_cart_items(connection, user_id, for_update=False):
    cursor = connection.cursor(dictionary=True)

    try:
        lock_clause = " FOR UPDATE" if for_update else ""

        cursor.execute(
            f"""
            SELECT
                c.id AS cart_id,
                ci.id AS cart_item_id,
                ci.quantity,
                ci.unit_price,
                p.id AS product_id,
                p.sku,
                p.name,
                p.description,
                p.stock_qty,
                p.is_active,
                (ci.quantity * ci.unit_price) AS item_total
            FROM carts c
            INNER JOIN cart_items ci
                ON ci.cart_id = c.id
            INNER JOIN products p
                ON ci.product_id = p.id
            WHERE c.user_id = %s
              AND c.status = 'active'
            ORDER BY ci.id ASC
            {lock_clause}
            """,
            (user_id,),
        )

        return cursor.fetchall()

    finally:
        cursor.close()


def get_checkout_summary(connection, user_id, for_update=False):
    items = get_active_cart_items(
        connection,
        user_id,
        for_update=for_update,
    )

    subtotal = sum(
        (item["item_total"] for item in items),
        Decimal("0.00"),
    )

    shipping_charge = calculate_shipping(subtotal)
    total_amount = subtotal + shipping_charge

    return items, subtotal, shipping_charge, total_amount


# -----------------------------------------------------------------------------
# GLOBAL TEMPLATE CONTEXT
# -----------------------------------------------------------------------------


@app.context_processor
def inject_global_context():
    cart_count = 0

    if "user_id" in session:
        connection = None
        cursor = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor()

            cursor.execute(
                """
                SELECT COALESCE(SUM(ci.quantity), 0)
                FROM carts c
                INNER JOIN cart_items ci
                    ON ci.cart_id = c.id
                WHERE c.user_id = %s
                  AND c.status = 'active'
                """,
                (session["user_id"],),
            )

            cart_count = cursor.fetchone()[0]

        except mysql.connector.Error:
            cart_count = 0

        finally:
            if cursor:
                cursor.close()

            if connection and connection.is_connected():
                connection.close()

    return {
        "logged_in": "user_id" in session,
        "current_user_name": session.get("user_name"),
        "current_user_role": session.get("user_role"),
        "cart_count": cart_count,
    }


# -----------------------------------------------------------------------------
# HOME / HEALTH
# -----------------------------------------------------------------------------


@app.route("/")
def home():
    connection = None
    cursor = None
    category_cursor = None

    categories = []
    featured_products = []

    try:
        connection = get_db_connection()

        cursor = connection.cursor(dictionary=True)
        category_cursor = connection.cursor(dictionary=True)

        category_cursor.execute(
            """
            SELECT
                c.id,
                c.name,
                COUNT(p.id) AS product_count
            FROM categories c
            LEFT JOIN products p
                ON p.category_id = c.id
                AND p.is_active = TRUE
            WHERE c.is_active = TRUE
            GROUP BY c.id, c.name
            ORDER BY c.name ASC
            """
        )

        categories = category_cursor.fetchall()

        cursor.execute(
            """
            SELECT
                p.id,
                p.sku,
                p.name,
                p.description,
                p.price,
                p.stock_qty,
                c.name AS category_name
            FROM products p
            INNER JOIN categories c
                ON p.category_id = c.id
            WHERE p.is_active = TRUE
            ORDER BY p.id ASC
            LIMIT 4
            """
        )

        featured_products = cursor.fetchall()

    except mysql.connector.Error:
        categories = []
        featured_products = []

    finally:
        if category_cursor:
            category_cursor.close()

        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()

    return render_template(
        "home.html",
        categories=categories,
        featured_products=featured_products,
    )


@app.route("/health/db")
def database_health():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()

        cursor = connection.cursor()
        cursor.execute("SELECT DATABASE(), VERSION();")

        database_name, mysql_version = cursor.fetchone()

        return {
            "status": "healthy",
            "database": database_name,
            "mysql_version": mysql_version,
        }

    except mysql.connector.Error as error:
        return {
            "status": "unhealthy",
            "error": str(error),
        }, 500

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()


# -----------------------------------------------------------------------------
# REGISTRATION / LOGIN / LOGOUT
# -----------------------------------------------------------------------------


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":

        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not first_name:
            flash("First name is required.", "error")
            return render_template("register.html")

        if not last_name:
            flash("Last name is required.", "error")
            return render_template("register.html")

        if not email:
            flash("Email is required.", "error")
            return render_template("register.html")

        if "@" not in email or "." not in email.split("@")[-1]:
            flash("Please enter a valid email address.", "error")
            return render_template("register.html")

        if not password:
            flash("Password is required.", "error")
            return render_template("register.html")

        # BUG-010: Passwords shorter than 4 characters are accepted.
        if len(password) < 4:
            flash(
                "Password must contain at least 4 characters.",
                "error",
            )
            return render_template("register.html")

        if password != confirm_password:
            flash(
                "Password and confirm password do not match.",
                "error",
            )
            return render_template("register.html")

        connection = None
        cursor = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)

            cursor.execute(
                """
                SELECT id
                FROM users
                WHERE email = %s
                """,
                (email,),
            )

            existing_user = cursor.fetchone()

            if existing_user:
                flash(
                    "An account with this email already exists.",
                    "error",
                )

                return render_template("register.html")

            password_hash = generate_password_hash(password)

            cursor.execute(
                """
                INSERT INTO users (
                    first_name,
                    last_name,
                    email,
                    password_hash,
                    role,
                    is_active
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    'customer',
                    TRUE
                )
                """,
                (
                    first_name,
                    last_name,
                    email,
                    password_hash,
                ),
            )

            connection.commit()

            flash(
                "Registration successful. Please log in.",
                "success",
            )

            return redirect(url_for("login"))

        except mysql.connector.Error as error:
            if connection:
                connection.rollback()

            flash(
                f"Registration failed: {error}",
                "error",
            )

            return render_template("register.html")

        finally:
            if cursor:
                cursor.close()

            if connection and connection.is_connected():
                connection.close()

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        next_url = request.args.get("next") or request.form.get("next")

        if not email or not password:
            flash(
                "Email and password are required.",
                "error",
            )

            return render_template(
                "login.html",
                next_url=next_url,
            )

        connection = None
        cursor = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)

            cursor.execute(
                """
                SELECT
                    id,
                    first_name,
                    last_name,
                    email,
                    password_hash,
                    role,
                    is_active
                FROM users
                WHERE email = %s
                """,
                (email,),
            )

            user = cursor.fetchone()

            if not user:
                flash(
                    "Invalid email or password.",
                    "error",
                )

                return render_template(
                    "login.html",
                    next_url=next_url,
                )

            # BUG-007: Inactive accounts are allowed to log in.

            if not check_password_hash(
                user["password_hash"],
                password,
            ):
                flash(
                    "Invalid email or password.",
                    "error",
                )

                return render_template(
                    "login.html",
                    next_url=next_url,
                )

            session.clear()

            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            session["user_name"] = (
                f"{user['first_name']} {user['last_name']}"
            )
            session["user_role"] = user["role"]

            flash(
                f"Welcome, {user['first_name']}!",
                "success",
            )

            if next_url and next_url.startswith("/"):
                return redirect(next_url)

            return redirect(url_for("home"))

        except mysql.connector.Error as error:
            flash(
                f"Login failed: {error}",
                "error",
            )

            return render_template(
                "login.html",
                next_url=next_url,
            )

        finally:
            if cursor:
                cursor.close()

            if connection and connection.is_connected():
                connection.close()

    return render_template(
        "login.html",
        next_url=request.args.get("next", ""),
    )


@app.route("/logout")
def logout():
    # BUG-008: Session is not cleared during logout.

    flash(
        "You have been logged out successfully.",
        "success",
    )

    return redirect(url_for("login"))


# -----------------------------------------------------------------------------
# CATEGORIES / PRODUCTS
# -----------------------------------------------------------------------------


@app.route("/categories")
def categories_page():
    connection = None
    cursor = None
    categories = []

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                c.id,
                c.name,
                COUNT(p.id) AS product_count
            FROM categories c
            LEFT JOIN products p
                ON p.category_id = c.id
                AND p.is_active = TRUE
            WHERE c.is_active = TRUE
            GROUP BY c.id, c.name
            ORDER BY c.name ASC
            """
        )

        categories = cursor.fetchall()

    except mysql.connector.Error:
        flash(
            "Unable to load categories right now.",
            "error",
        )

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()

    return render_template(
        "categories.html",
        categories=categories,
    )


@app.route("/products")
def products():
    connection = None
    cursor = None
    category_cursor = None

    search_query = request.args.get("q", "").strip()
    category_filter = request.args.get("category", "").strip()
    sort_option = request.args.get("sort", "default").strip()

    sort_options = {
        "default": "p.id ASC",
        "price_asc": "p.price DESC",
        "price_desc": "p.price ASC",
        "name_asc": "p.name ASC",
        "name_desc": "p.name DESC",
    }

    order_by = sort_options.get(
        sort_option,
        "p.id ASC",
    )

    try:
        connection = get_db_connection()

        cursor = connection.cursor(dictionary=True)
        category_cursor = connection.cursor(dictionary=True)

        category_cursor.execute(
            """
            SELECT id, name
            FROM categories
            WHERE is_active = TRUE
            ORDER BY name ASC
            """
        )

        categories = category_cursor.fetchall()

        query = """
            SELECT
                p.id,
                p.sku,
                p.name,
                p.description,
                p.price,
                p.stock_qty,
                c.name AS category_name
            FROM products p
            INNER JOIN categories c
                ON p.category_id = c.id
            WHERE p.is_active = TRUE
        """

        parameters = []

        if search_query:
            query += """
                AND (
                    p.description LIKE %s
                    OR p.sku LIKE %s
                )
            """

            search_value = f"%{search_query}%"

            parameters.extend(
                [
                    search_value,
                    search_value,
                ]
            )

        if category_filter:
            query += """
                AND c.name = %s
            """

            parameters.append(category_filter)

        query += f" ORDER BY {order_by}"

        cursor.execute(
            query,
            parameters,
        )

        product_list = cursor.fetchall()

        return render_template(
            "products.html",
            products=product_list,
            categories=categories,
            search_query=search_query,
            category_filter=category_filter,
            sort_option=sort_option,
        )

    except mysql.connector.Error as error:
        return f"Database error: {error}", 500

    finally:
        if category_cursor:
            category_cursor.close()

        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()


@app.route("/products/<sku>")
def product_details(sku):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                p.id,
                p.sku,
                p.name,
                p.description,
                p.price,
                p.stock_qty,
                c.name AS category_name
            FROM products p
            INNER JOIN categories c
                ON p.category_id = c.id
            WHERE p.sku = %s
              AND p.is_active = TRUE
            """,
            (sku,),
        )

        product = cursor.fetchone()

        if not product:
            return render_template(
                "product_not_found.html",
                sku=sku,
            ), 404

        return render_template(
            "product_details.html",
            product=product,
        )

    except mysql.connector.Error as error:
        return f"Database error: {error}", 500

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()


# -----------------------------------------------------------------------------
# CART
# -----------------------------------------------------------------------------


@app.route("/cart")
@login_required
def cart():
    connection = None

    cart_items = []
    cart_subtotal = Decimal("0.00")
    shipping_charge = Decimal("0.00")
    cart_total = Decimal("0.00")

    try:
        connection = get_db_connection()

        (
            cart_items,
            cart_subtotal,
            shipping_charge,
            cart_total,
        ) = get_checkout_summary(
            connection,
            session["user_id"],
        )

    except mysql.connector.Error as error:
        flash(
            f"Unable to load cart: {error}",
            "error",
        )

    finally:
        if connection and connection.is_connected():
            connection.close()

    return render_template(
        "cart.html",
        cart_items=cart_items,
        cart_subtotal=cart_subtotal,
        shipping_charge=shipping_charge,
        cart_total=cart_total,
    )


@app.route("/cart/add/<sku>", methods=["POST"])
@login_required
def add_to_cart(sku):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        connection.start_transaction()

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                id,
                sku,
                name,
                price,
                stock_qty,
                is_active
            FROM products
            WHERE sku = %s
            FOR UPDATE
            """,
            (sku,),
        )

        product = cursor.fetchone()

        if not product or not product["is_active"]:
            connection.rollback()
            flash(
                "Product not found.",
                "error",
            )
            return redirect(url_for("products"))

        if product["stock_qty"] <= 0:
            connection.rollback()
            flash(
                "This product is out of stock.",
                "error",
            )
            return redirect(
                url_for("product_details", sku=sku)
            )

        cart_id = get_or_create_active_cart(
            connection,
            session["user_id"],
        )

        cursor.execute(
            """
            SELECT
                id,
                quantity
            FROM cart_items
            WHERE cart_id = %s
              AND product_id = %s
            FOR UPDATE
            """,
            (
                cart_id,
                product["id"],
            ),
        )

        existing_item = cursor.fetchone()

        if existing_item:
            new_quantity = existing_item["quantity"] + 1

            if new_quantity > product["stock_qty"]:
                connection.rollback()
                flash(
                    f"Only {product['stock_qty']} units are available.",
                    "error",
                )
                return redirect(
                    url_for("product_details", sku=sku)
                )

            cursor.execute(
                """
                UPDATE cart_items
                SET quantity = %s,
                    unit_price = %s
                WHERE id = %s
                """,
                (
                    new_quantity,
                    product["price"],
                    existing_item["id"],
                ),
            )

        else:
            cursor.execute(
                """
                INSERT INTO cart_items (
                    cart_id,
                    product_id,
                    quantity,
                    unit_price
                )
                VALUES (
                    %s,
                    %s,
                    1,
                    %s
                )
                """,
                (
                    cart_id,
                    product["id"],
                    product["price"],
                ),
            )

        connection.commit()

        flash(
            f"{product['name']} added to your cart.",
            "success",
        )

        return redirect(
            url_for("product_details", sku=sku)
        )

    except mysql.connector.Error as error:
        if connection:
            connection.rollback()

        flash(
            f"Unable to add product to cart: {error}",
            "error",
        )

        return redirect(
            url_for("product_details", sku=sku)
        )

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()


@app.route("/cart/update/<int:item_id>", methods=["POST"])
@login_required
def update_cart_item(item_id):
    connection = None
    cursor = None

    try:
        quantity_text = request.form.get("quantity", "").strip()

        try:
            quantity = int(quantity_text)
        except ValueError:
            quantity = 0

        # BUG-011: Quantity 0 is accepted instead of being rejected.
        if quantity < 0:
            flash(
                "Quantity cannot be negative.",
                "error",
            )
            return redirect(url_for("cart"))

        connection = get_db_connection()
        connection.start_transaction()

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                ci.id,
                ci.cart_id,
                ci.quantity,
                p.name,
                p.stock_qty,
                p.price
            FROM cart_items ci
            INNER JOIN carts c
                ON ci.cart_id = c.id
            INNER JOIN products p
                ON ci.product_id = p.id
            WHERE ci.id = %s
              AND c.user_id = %s
              AND c.status = 'active'
            FOR UPDATE
            """,
            (
                item_id,
                session["user_id"],
            ),
        )

        item = cursor.fetchone()

        if not item:
            connection.rollback()
            flash(
                "Cart item not found.",
                "error",
            )
            return redirect(url_for("cart"))

        if item["stock_qty"] <= 0:
            connection.rollback()
            flash(
                f"{item['name']} is currently out of stock.",
                "error",
            )
            return redirect(url_for("cart"))

        cursor.execute(
            """
            UPDATE cart_items
            SET quantity = %s,
                unit_price = %s
            WHERE id = %s
            """,
            (
                quantity,
                item["price"],
                item_id,
            ),
        )

        connection.commit()

        flash(
            "Cart quantity updated.",
            "success",
        )

    except mysql.connector.Error as error:
        if connection:
            connection.rollback()

        flash(
            f"Unable to update cart: {error}",
            "error",
        )

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()

    return redirect(url_for("cart"))


@app.route("/cart/remove/<int:item_id>", methods=["POST"])
@login_required
def remove_cart_item(item_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            DELETE ci
            FROM cart_items ci
            INNER JOIN carts c
                ON ci.cart_id = c.id
            WHERE ci.id = %s
              AND c.user_id = %s
              AND c.status = 'active'
            """,
            (
                item_id,
                session["user_id"],
            ),
        )

        if cursor.rowcount == 0:
            flash(
                "Cart item not found.",
                "error",
            )
        else:
            connection.commit()
            flash(
                "Item removed from cart.",
                "success",
            )

    except mysql.connector.Error as error:
        if connection:
            connection.rollback()

        flash(
            f"Unable to remove item: {error}",
            "error",
        )

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()

    return redirect(url_for("cart"))


# -----------------------------------------------------------------------------
# CHECKOUT
# -----------------------------------------------------------------------------


@app.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if request.method == "POST":
            address_choice = request.form.get("address_choice", "new").strip()
            payment_method = request.form.get("payment_method", "").strip().upper()

            if payment_method not in {"CARD", "UPI", "COD"}:
                flash(
                    "Please select a valid payment method.",
                    "error",
                )
                return redirect(url_for("checkout"))

            if address_choice == "saved":
                address_id_text = request.form.get("address_id", "").strip()

                try:
                    address_id = int(address_id_text)
                except ValueError:
                    address_id = 0

                if address_id <= 0:
                    flash(
                        "Please select a saved address.",
                        "error",
                    )
                    return redirect(url_for("checkout"))

                cursor.execute(
                    """
                    SELECT
                        id,
                        recipient_name,
                        line1,
                        line2,
                        city,
                        state,
                        postal_code,
                        country,
                        phone
                    FROM addresses
                    WHERE id = %s
                      AND user_id = %s
                    """,
                    (
                        address_id,
                        session["user_id"],
                    ),
                )

                address = cursor.fetchone()

                if not address:
                    flash(
                        "Selected address was not found.",
                        "error",
                    )
                    return redirect(url_for("checkout"))

            else:
                recipient_name = request.form.get("recipient_name", "").strip()
                line1 = request.form.get("line1", "").strip()
                line2 = request.form.get("line2", "").strip()
                city = request.form.get("city", "").strip()
                state = request.form.get("state", "").strip()
                postal_code = request.form.get("postal_code", "").strip()
                country = request.form.get("country", "India").strip() or "India"
                phone = request.form.get("phone", "").strip()

                if not recipient_name:
                    flash("Recipient name is required.", "error")
                    return redirect(url_for("checkout"))

                if not line1:
                    flash("Address line 1 is required.", "error")
                    return redirect(url_for("checkout"))

                if not city:
                    flash("City is required.", "error")
                    return redirect(url_for("checkout"))

                if not state:
                    flash("State is required.", "error")
                    return redirect(url_for("checkout"))

                if not re.fullmatch(r"[A-Za-z0-9 -]{4,20}", postal_code):
                    flash(
                        "Please enter a valid postal code.",
                        "error",
                    )
                    return redirect(url_for("checkout"))

                if not re.fullmatch(r"[+0-9() -]{7,20}", phone):
                    flash(
                        "Please enter a valid phone number.",
                        "error",
                    )
                    return redirect(url_for("checkout"))

                address = {
                    "id": None,
                    "recipient_name": recipient_name,
                    "line1": line1,
                    "line2": line2,
                    "city": city,
                    "state": state,
                    "postal_code": postal_code,
                    "country": country,
                    "phone": phone,
                }

                if request.form.get("save_address") == "on":
                    if request.form.get("make_default") == "on":
                        cursor.execute(
                            """
                            UPDATE addresses
                            SET is_default = 0
                            WHERE user_id = %s
                            """,
                            (session["user_id"],),
                        )

                    cursor.execute(
                        """
                        INSERT INTO addresses (
                            user_id,
                            label,
                            recipient_name,
                            line1,
                            line2,
                            city,
                            state,
                            postal_code,
                            country,
                            phone,
                            is_default
                        )
                        VALUES (
                            %s,
                            'Home',
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
                        )
                        """,
                        (
                            session["user_id"],
                            address["recipient_name"],
                            address["line1"],
                            address["line2"] or None,
                            address["city"],
                            address["state"],
                            address["postal_code"],
                            address["country"],
                            address["phone"],
                            1 if request.form.get("make_default") == "on" else 0,
                        ),
                    )

                    address["id"] = cursor.lastrowid

                    connection.commit()

            checkout_items, subtotal, shipping_charge, total_amount = get_checkout_summary(
                connection,
                session["user_id"],
            )

            if not checkout_items:
                flash(
                    "Your cart is empty. Add a product before checkout.",
                    "error",
                )
                return redirect(url_for("cart"))

            for item in checkout_items:
                if not item["is_active"]:
                    flash(
                        f"{item['name']} is no longer available.",
                        "error",
                    )
                    return redirect(url_for("cart"))

                if item["quantity"] > item["stock_qty"]:
                    flash(
                        f"Only {item['stock_qty']} units of {item['name']} are available.",
                        "error",
                    )
                    return redirect(url_for("cart"))

            session["checkout_data"] = {
                "recipient_name": address["recipient_name"],
                "line1": address["line1"],
                "line2": address["line2"],
                "city": address["city"],
                "state": address["state"],
                "postal_code": address["postal_code"],
                "country": address["country"],
                "phone": address["phone"],
                "payment_method": payment_method,
            }

            return redirect(url_for("payment"))

        cursor.execute(
            """
            SELECT
                id,
                label,
                recipient_name,
                line1,
                line2,
                city,
                state,
                postal_code,
                country,
                phone,
                is_default
            FROM addresses
            WHERE user_id = %s
            ORDER BY is_default DESC, id DESC
            """,
            (session["user_id"],),
        )

        addresses = cursor.fetchall()

        checkout_items, subtotal, shipping_charge, total_amount = get_checkout_summary(
            connection,
            session["user_id"],
        )

        if not checkout_items:
            flash(
                "Your cart is empty. Add a product before checkout.",
                "error",
            )
            return redirect(url_for("cart"))

        return render_template(
            "checkout.html",
            addresses=addresses,
            checkout_items=checkout_items,
            subtotal=subtotal,
            shipping_charge=shipping_charge,
            total_amount=total_amount,
        )

    except mysql.connector.Error as error:
        if connection:
            connection.rollback()

        flash(
            f"Unable to continue to checkout: {error}",
            "error",
        )
        return redirect(url_for("cart"))

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()


# -----------------------------------------------------------------------------
# PAYMENT + ORDER CREATION
# -----------------------------------------------------------------------------


@app.route("/payment", methods=["GET", "POST"])
@login_required
def payment():
    checkout_data = session.get("checkout_data")

    if not checkout_data:
        flash(
            "Please complete the checkout details first.",
            "error",
        )
        return redirect(url_for("checkout"))

    connection = None

    try:
        connection = get_db_connection()

        if request.method == "POST":
            payment_method = request.form.get(
                "payment_method",
                checkout_data["payment_method"],
            ).strip().upper()

            if payment_method not in {"CARD", "UPI", "COD"}:
                flash(
                    "Invalid payment method.",
                    "error",
                )
                return redirect(url_for("payment"))

            failure_message = None
            payment_failed = False

            if payment_method == "CARD":
                card_number = re.sub(
                    r"\s+",
                    "",
                    request.form.get("card_number", ""),
                )
                expiry = request.form.get("expiry", "").strip()
                cvv = request.form.get("cvv", "").strip()

                if not re.fullmatch(r"\d{16}", card_number):
                    failure_message = "Please enter a valid 16-digit card number."
                elif not re.fullmatch(r"(0[1-9]|1[0-2])/\d{2}", expiry):
                    failure_message = "Please enter expiry in MM/YY format."
                elif not re.fullmatch(r"\d{3,4}", cvv):
                    failure_message = "Please enter a valid CVV."
                elif card_number.endswith("0002"):
                    failure_message = "Payment declined by the test payment gateway."

            elif payment_method == "UPI":
                upi_id = request.form.get("upi_id", "").strip().lower()

                if not re.fullmatch(r"[a-z0-9._-]+@[a-z0-9._-]+", upi_id):
                    failure_message = "Please enter a valid UPI ID."
                elif upi_id == "fail@upi":
                    failure_message = "Payment declined by the test payment gateway."

            if failure_message:
                payment_failed = True
                flash(
                    failure_message,
                    "error",
                )

            connection.start_transaction()

            cursor = connection.cursor(dictionary=True)

            cursor.execute(
                """
                SELECT id
                FROM carts
                WHERE user_id = %s
                  AND status = 'active'
                LIMIT 1
                FOR UPDATE
                """,
                (session["user_id"],),
            )

            active_cart = cursor.fetchone()

            if not active_cart:
                connection.rollback()
                flash(
                    "Your cart is empty. Please add a product before payment.",
                    "error",
                )
                return redirect(url_for("cart"))

            cursor.execute(
                """
                SELECT
                    ci.id AS cart_item_id,
                    ci.product_id,
                    ci.quantity,
                    ci.unit_price,
                    p.name,
                    p.sku,
                    p.stock_qty,
                    p.is_active,
                    (ci.quantity * ci.unit_price) AS line_total
                FROM cart_items ci
                INNER JOIN products p
                    ON ci.product_id = p.id
                WHERE ci.cart_id = %s
                ORDER BY ci.id ASC
                FOR UPDATE
                """,
                (active_cart["id"],),
            )

            items = cursor.fetchall()

            if not items:
                connection.rollback()
                flash(
                    "Your cart is empty. Please add a product before payment.",
                    "error",
                )
                return redirect(url_for("cart"))

            subtotal = sum(
                (item["line_total"] for item in items),
                Decimal("0.00"),
            )
            shipping_charge = calculate_shipping(subtotal)
            total_amount = subtotal + shipping_charge

            for item in items:
                if not item["is_active"]:
                    connection.rollback()
                    flash(
                        f"{item['name']} is no longer available.",
                        "error",
                    )
                    return redirect(url_for("cart"))

                if item["quantity"] > item["stock_qty"]:
                    connection.rollback()
                    flash(
                        f"Only {item['stock_qty']} units of {item['name']} are available.",
                        "error",
                    )
                    return redirect(url_for("cart"))

            order_number = create_order_number()

            shipping = checkout_data

            cursor.execute(
                """
                INSERT INTO orders (
                    order_number,
                    user_id,
                    status,
                    subtotal,
                    shipping_charge,
                    subtotal,  # BUG-019: order total excludes shipping.
                    shipping_recipient_name,
                    shipping_line1,
                    shipping_line2,
                    shipping_city,
                    shipping_state,
                    shipping_postal_code,
                    shipping_country,
                    shipping_phone
                )
                VALUES (
                    %s,
                    %s,
                    'PLACED',
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    order_number,
                    session["user_id"],
                    subtotal,
                    shipping_charge,
                    total_amount,
                    shipping["recipient_name"],
                    shipping["line1"],
                    shipping["line2"] or None,
                    shipping["city"],
                    shipping["state"],
                    shipping["postal_code"],
                    shipping["country"],
                    shipping["phone"],
                ),
            )

            order_id = cursor.lastrowid

            for item in items:
                cursor.execute(
                    """
                    INSERT INTO order_items (
                        order_id,
                        product_id,
                        product_name,
                        quantity,
                        unit_price,
                        line_total
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        order_id,
                        item["product_id"],
                        item["name"],
                        item["quantity"],
                        item["unit_price"],
                        item["unit_price"],  # BUG-017: line_total equals unit_price even when quantity > 1.
                    ),
                )

                cursor.execute(
                    """
                    UPDATE products
                    SET stock_qty = stock_qty - %s
                    WHERE id = %s
                      AND stock_qty >= %s
                    """,
                    (
                        item["quantity"] * 2,  # BUG-018: inventory is decremented twice.
                        item["product_id"],
                        item["quantity"] * 2,
                    ),
                )

                if cursor.rowcount != 1:
                    raise mysql.connector.Error(
                        msg=f"Stock update failed for {item['sku']}."
                    )

            if payment_method == "COD":
                payment_status = "PENDING"
                transaction_reference = create_transaction_reference("COD")
            elif payment_failed:
                payment_status = "FAILED"
                transaction_reference = None
            else:
                payment_status = "SUCCESS"
                transaction_reference = create_transaction_reference("TXN")

            cursor.execute(
                """
                INSERT INTO payments (
                    order_id,
                    payment_method,
                    payment_status,
                    transaction_reference,
                    amount
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    order_id,
                    payment_method,
                    payment_status,
                    transaction_reference,
                    subtotal,  # BUG-016: Payment amount excludes shipping.
                ),
            )

            cursor.execute(
                """
                DELETE FROM cart_items
                WHERE cart_id = %s
                """,
                (active_cart["id"],),
            )

            cursor.execute(
                """
                UPDATE carts
                SET status = 'checked_out'
                WHERE id = %s
                """,
                (active_cart["id"],),
            )

            connection.commit()

            session.pop("checkout_data", None)

            return redirect(
                url_for(
                    "order_confirmation",
                    order_number=order_number,
                )
            )

        checkout_items, subtotal, shipping_charge, total_amount = get_checkout_summary(
            connection,
            session["user_id"],
        )

        if not checkout_items:
            flash(
                "Your cart is empty. Please add a product before payment.",
                "error",
            )
            return redirect(url_for("cart"))

        return render_template(
            "payment.html",
            checkout_data=checkout_data,
            checkout_items=checkout_items,
            subtotal=subtotal,
            shipping_charge=shipping_charge,
            total_amount=total_amount,
        )

    except mysql.connector.Error as error:
        if connection:
            connection.rollback()

        flash(
            f"Unable to process the order: {error}",
            "error",
        )
        return redirect(url_for("payment"))

    finally:
        if connection and connection.is_connected():
            connection.close()


@app.route("/admin")
@login_required
def admin_dashboard():
    # BUG-009: No role check; a normal customer can access admin functionality.
    return render_template("admin.html")


@app.route("/orders")
@login_required
def orders():
    connection = None
    cursor = None
    order_list = []

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                o.id,
                o.order_number,
                o.status,
                o.subtotal,
                o.shipping_charge,
                o.total_amount,
                o.created_at,
                p.payment_method,
                p.payment_status,
                COUNT(oi.id) AS item_count
            FROM orders o
            LEFT JOIN payments p
                ON p.order_id = o.id
            LEFT JOIN order_items oi
                ON oi.order_id = o.id
            WHERE o.user_id = %s
            GROUP BY
                o.id,
                o.order_number,
                o.status,
                o.subtotal,
                o.shipping_charge,
                o.total_amount,
                o.created_at,
                p.payment_method,
                p.payment_status
            ORDER BY o.created_at DESC, o.id DESC
            """,
            (session["user_id"],),
        )

        order_list = cursor.fetchall()

    except mysql.connector.Error as error:
        flash(
            f"Unable to load orders: {error}",
            "error",
        )

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()

    return render_template(
        "orders.html",
        orders=order_list,
    )


@app.route("/orders/<order_number>")
@login_required
def order_details(order_number):
    # BUG-012: Order details can be viewed by another authenticated user if the order number is known.
    connection = None
    order_cursor = None
    item_cursor = None

    try:
        connection = get_db_connection()

        order_cursor = connection.cursor(dictionary=True)
        item_cursor = connection.cursor(dictionary=True)

        order_cursor.execute(
            """
            SELECT
                o.*,
                p.payment_method,
                p.payment_status,
                p.transaction_reference,
                p.amount AS payment_amount,
                p.failure_reason
            FROM orders o
            LEFT JOIN payments p
                ON p.order_id = o.id
            WHERE o.order_number = %s
            """,
            (
                order_number,
            ),
        )

        order = order_cursor.fetchone()

        if not order:
            return render_template(
                "product_not_found.html",
                sku=order_number,
            ), 404

        item_cursor.execute(
            """
            SELECT
                id,
                product_id,
                product_name,
                quantity,
                unit_price,
                line_total
            FROM order_items
            WHERE order_id = %s
            ORDER BY id ASC
            """,
            (order["id"],),
        )

        items = item_cursor.fetchall()

        return render_template(
            "order_details.html",
            order=order,
            items=items,
        )

    except mysql.connector.Error as error:
        flash(
            f"Unable to load order: {error}",
            "error",
        )
        return redirect(url_for("orders"))

    finally:
        if order_cursor:
            order_cursor.close()

        if item_cursor:
            item_cursor.close()

        if connection and connection.is_connected():
            connection.close()


@app.route("/orders/<order_number>/cancel", methods=["POST"])
@login_required
def cancel_order(order_number):
    # BUG-014: Order cancellation does not verify the order belongs to the current user.
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                id,
                status
            FROM orders
            WHERE order_number = %s
            FOR UPDATE
            """,
            (
                order_number,
            ),
        )

        order = cursor.fetchone()

        if not order:
            connection.rollback()
            flash(
                "Order not found.",
                "error",
            )
            return redirect(url_for("orders"))

        # BUG-015: Already-cancelled orders can be cancelled again, restoring stock twice.
        if order["status"] not in {"PLACED", "PROCESSING", "CANCELLED"}:
            connection.rollback()
            flash(
                "This order can no longer be cancelled.",
                "error",
            )
            return redirect(
                url_for("order_details", order_number=order_number)
            )

        cursor.execute(
            """
            SELECT
                oi.product_id,
                oi.quantity,
                p.stock_qty
            FROM order_items oi
            INNER JOIN products p
                ON p.id = oi.product_id
            WHERE oi.order_id = %s
            FOR UPDATE
            """,
            (order["id"],),
        )

        items = cursor.fetchall()

        for item in items:
            cursor.execute(
                """
                UPDATE products
                SET stock_qty = stock_qty + %s
                WHERE id = %s
                """,
                (
                    item["quantity"],
                    item["product_id"],
                ),
            )

        cursor.execute(
            """
            UPDATE orders
            SET status = 'CANCELLED'
            WHERE id = %s
            """,
            (order["id"],),
        )

        connection.commit()

        flash(
            f"Order {order_number} has been cancelled.",
            "success",
        )

        return redirect(
            url_for(
                "order_details",
                order_number=order_number,
            )
        )

    except mysql.connector.Error as error:
        if connection:
            connection.rollback()

        flash(
            f"Unable to cancel order: {error}",
            "error",
        )

        return redirect(url_for("orders"))

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()


@app.route("/order-confirmation/<order_number>")
@login_required
def order_confirmation(order_number):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                o.order_number,
                o.status,
                o.subtotal,
                o.shipping_charge,
                o.total_amount,
                o.shipping_recipient_name,
                o.shipping_line1,
                o.shipping_line2,
                o.shipping_city,
                o.shipping_state,
                o.shipping_postal_code,
                o.shipping_country,
                o.shipping_phone,
                o.created_at,
                p.payment_method,
                p.payment_status,
                p.transaction_reference
            FROM orders o
            LEFT JOIN payments p
                ON p.order_id = o.id
            WHERE o.order_number = %s
            """,
            (
                order_number,
            ),
        )

        # BUG-013: Order confirmation can be viewed by another user who knows the order number.
        order = cursor.fetchone()

        if not order:
            return redirect(url_for("orders"))

        return render_template(
            "order_confirmation.html",
            order=order,
        )

    except mysql.connector.Error as error:
        flash(
            f"Unable to load order confirmation: {error}",
            "error",
        )
        return redirect(url_for("orders"))

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
    )
