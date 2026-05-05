import os
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify, send_from_directory
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder="static")
client = Anthropic()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leads.db")


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                email TEXT NOT NULL,
                phone TEXT,
                service TEXT,
                message TEXT,
                ai_response TEXT,
                status TEXT DEFAULT 'New',
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
            )
        """)
        try:
            conn.execute("ALTER TABLE leads ADD COLUMN phone TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE leads ADD COLUMN address TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE leads ADD COLUMN preferred_date TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE leads ADD COLUMN preferred_time TEXT")
        except Exception:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT,
                service TEXT,
                preferred_date TEXT,
                preferred_time TEXT,
                message TEXT,
                status TEXT DEFAULT 'Pending',
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
            )
        """)


init_db()

LEAD_RESPONSE_PROMPT = """You are writing an instant reply on behalf of a home services business to a new customer inquiry.

Write a short, warm, professional email response. Follow this structure exactly:
1. Greet the customer by name (use "Hi [name]" — if no name given, use "Hi there")
2. In 1-2 sentences, acknowledge specifically what they're asking about
3. If a preferred date or time was provided, acknowledge it — say you'll be in touch to confirm (never guarantee it)
4. In 1-2 sentences, confirm you're available and interested in helping
5. Close with one clear next step — suggest they reply to confirm a time, or that you'll reach out shortly
6. Sign off with the business name

Rules:
- Maximum 4 short paragraphs, under 150 words total
- No bullet points, no headers, no pricing
- Sound like a real person wrote this, not a template
- Never ask more than one question"""

SYSTEM_PROMPT = """You are an AI quoting assistant for a home services business. Your job is to generate professional, itemized job quotes based on the customer's description.

When given a job description:
1. Identify the type of work (plumbing, electrical, roofing, landscaping, HVAC, cleaning, renovation, etc.)
2. Break it down into line items (labor + materials where applicable)
3. Provide a low and high price range per line item (account for regional variation)
4. Add a subtotal, a 10% contingency buffer, and a total
5. Include estimated time to complete
6. Keep the tone professional but plain — no jargon

Output format (always use this exact structure):
---
JOB QUOTE
Job Type: [type]
Description: [one sentence summary]

LINE ITEMS:
- [Item]: $[low] – $[high]
- [Item]: $[low] – $[high]

Subtotal: $[low] – $[high]
Contingency (10%): $[low] – $[high]
TOTAL ESTIMATE: $[low] – $[high]

Estimated completion time: [X hours / X days]

Notes: [Any important assumptions, exclusions, or recommendations — 1-3 bullet points max]
---

If the job description is too vague to quote, ask ONE specific clarifying question before generating the quote. Never ask more than one question."""


def _send_email(to_email, subject, from_name, plain_body, html_body):
    sender_email = os.getenv("SENDER_EMAIL")
    app_password = os.getenv("SENDER_APP_PASSWORD")

    if not sender_email or not app_password:
        raise ValueError("Email credentials not configured in .env")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{sender_email}>"
    msg["To"] = to_email
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender_email, app_password)
        server.sendmail(sender_email, to_email, msg.as_string())


def send_quote_email(recipient_email, customer_name, quote_text, business_name):
    greeting = f"Hi {customer_name}," if customer_name else "Hi,"

    plain_body = f"""{greeting}

Thank you for reaching out. Here is your estimate:

{quote_text}

This quote is an estimate. Final pricing will be confirmed before any work begins.
Feel free to reply to this email with any questions.

— {business_name}
"""

    html_body = f"""
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f9f9f9;padding:32px;">
  <div style="max-width:580px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;border:1px solid #e5e5e5;">
    <div style="background:#0a0a0a;padding:24px 32px;">
      <h2 style="color:#4169e1;margin:0;font-size:18px;">{business_name}</h2>
      <p style="color:#888;margin:4px 0 0;font-size:13px;">AI-Powered Quote</p>
    </div>
    <div style="padding:32px;">
      <p style="color:#333;margin:0 0 24px;">{greeting}</p>
      <p style="color:#333;margin:0 0 24px;">Thank you for reaching out. Here is your estimate:</p>
      <div style="background:#f4f4f4;border-radius:8px;padding:20px;font-family:monospace;font-size:13px;line-height:1.7;color:#222;white-space:pre-wrap;">{quote_text}</div>
      <p style="color:#666;font-size:13px;margin:24px 0 0;">This quote is an estimate. Final pricing will be confirmed before any work begins.<br>Reply to this email with any questions.</p>
    </div>
    <div style="background:#f4f4f4;padding:16px 32px;text-align:center;">
      <p style="color:#999;font-size:12px;margin:0;">— {business_name}</p>
    </div>
  </div>
</body>
</html>
"""

    _send_email(recipient_email, f"Your Quote from {business_name}", business_name, plain_body, html_body)


def send_invoice_email(recipient_email, customer_name, business_name,
                       invoice_number, invoice_date, due_date, line_items, total, notes):
    greeting = f"Hi {customer_name}," if customer_name else "Hi,"
    date_str = invoice_date or "—"
    due_str = due_date or "—"

    line_items_plain = "".join(
        f"  {item['description']}: ${float(item['amount']):.2f}\n" for item in line_items
    )

    plain_body = f"""{greeting}

Please find your invoice below.

Invoice #: {invoice_number}
Date: {date_str}
Due: {due_str}

LINE ITEMS:
{line_items_plain}
TOTAL: ${total:.2f}
{f'{chr(10)}Notes: {notes}' if notes else ''}

— {business_name}
"""

    line_items_html = "".join(f"""
        <tr>
          <td style="padding:10px 0;color:#333;border-bottom:1px solid #eee;">{item['description']}</td>
          <td style="padding:10px 0;color:#333;border-bottom:1px solid #eee;text-align:right;">${float(item['amount']):.2f}</td>
        </tr>""" for item in line_items)

    notes_html = f'<p style="color:#666;font-size:13px;margin:20px 0 0;padding-top:16px;border-top:1px solid #e5e5e5;">{notes}</p>' if notes else ""

    html_body = f"""
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f9f9f9;padding:32px;">
  <div style="max-width:580px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;border:1px solid #e5e5e5;">
    <div style="background:#0a0a0a;padding:24px 32px;overflow:hidden;">
      <div style="float:left;">
        <h2 style="color:#4169e1;margin:0;font-size:18px;">{business_name}</h2>
        <p style="color:#888;margin:4px 0 0;font-size:13px;">Invoice</p>
      </div>
      <div style="float:right;text-align:right;">
        <p style="color:#fff;font-size:16px;font-weight:700;margin:0;">#{invoice_number}</p>
        <p style="color:#888;font-size:12px;margin:4px 0 0;">Due: {due_str}</p>
      </div>
      <div style="clear:both;"></div>
    </div>
    <div style="padding:32px;">
      <p style="color:#333;margin:0 0 6px;">{greeting}</p>
      <p style="color:#999;font-size:13px;margin:0 0 28px;">Date: {date_str} &nbsp;&middot;&nbsp; Due: {due_str}</p>
      <table style="width:100%;border-collapse:collapse;">
        <thead>
          <tr>
            <th style="text-align:left;font-size:11px;color:#999;text-transform:uppercase;letter-spacing:0.06em;padding-bottom:8px;border-bottom:2px solid #e5e5e5;">Description</th>
            <th style="text-align:right;font-size:11px;color:#999;text-transform:uppercase;letter-spacing:0.06em;padding-bottom:8px;border-bottom:2px solid #e5e5e5;">Amount</th>
          </tr>
        </thead>
        <tbody>{line_items_html}</tbody>
        <tfoot>
          <tr>
            <td style="padding:16px 0 0;font-weight:700;font-size:15px;color:#0a0a0a;">Total</td>
            <td style="padding:16px 0 0;font-weight:700;font-size:15px;color:#0a0a0a;text-align:right;">${total:.2f}</td>
          </tr>
        </tfoot>
      </table>
      {notes_html}
    </div>
    <div style="background:#f4f4f4;padding:16px 32px;text-align:center;">
      <p style="color:#999;font-size:12px;margin:0;">— {business_name}</p>
    </div>
  </div>
</body>
</html>
"""

    _send_email(recipient_email, f"Invoice {invoice_number} from {business_name}", business_name, plain_body, html_body)


def send_lead_response_email(recipient_email, response_text, business_name):
    plain_body = response_text

    html_paragraphs = "".join(
        f'<p style="color:#333;font-size:15px;line-height:1.7;margin:0 0 16px;">{p.strip()}</p>'
        for p in response_text.split("\n\n") if p.strip()
    )

    html_body = f"""
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fff;padding:40px 32px;max-width:560px;margin:0 auto;">
  {html_paragraphs}
</body>
</html>
"""

    _send_email(recipient_email, f"Re: Your {business_name} Inquiry", business_name, plain_body, html_body)


def send_owner_notification_email(owner_email, business_name, name, email, phone, service, address, message, ai_response, preferred_date="", preferred_time=""):
    display_name = name or "Unknown"
    display_service = service or "Not specified"
    address_line = f"\nAddress: {address}" if address else ""
    date_line = f"\nPreferred Date: {preferred_date}" if preferred_date else ""
    time_line = f"\nPreferred Time: {preferred_time}" if preferred_time else ""

    plain_body = f"""New lead from {business_name} contact form.

Name: {display_name}
Email: {email}
Phone: {phone or 'Not provided'}
Service: {display_service}{address_line}{date_line}{time_line}

Their message:
{message}

---
AI response sent to them:
{ai_response}
"""

    html_body = f"""
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f9f9f9;padding:32px;">
  <div style="max-width:580px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;border:1px solid #e5e5e5;">
    <div style="background:#0a0a0a;padding:24px 32px;">
      <h2 style="color:#4169e1;margin:0;font-size:18px;">New Lead — {business_name}</h2>
      <p style="color:#888;margin:4px 0 0;font-size:13px;">Contact form submission</p>
    </div>
    <div style="padding:32px;">
      <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
        <tr><td style="padding:8px 0;color:#999;font-size:13px;width:110px;">Name</td><td style="padding:8px 0;color:#111;font-size:14px;font-weight:600;">{display_name}</td></tr>
        <tr><td style="padding:8px 0;color:#999;font-size:13px;">Email</td><td style="padding:8px 0;color:#111;font-size:14px;">{email}</td></tr>
        <tr><td style="padding:8px 0;color:#999;font-size:13px;">Phone</td><td style="padding:8px 0;color:#111;font-size:14px;">{phone or '—'}</td></tr>
        <tr><td style="padding:8px 0;color:#999;font-size:13px;">Service</td><td style="padding:8px 0;color:#111;font-size:14px;">{display_service}</td></tr>
        {f'<tr><td style="padding:8px 0;color:#999;font-size:13px;">Address</td><td style="padding:8px 0;color:#111;font-size:14px;">{address}</td></tr>' if address else ''}
        {f'<tr><td style="padding:8px 0;color:#999;font-size:13px;">Preferred Date</td><td style="padding:8px 0;color:#111;font-size:14px;font-weight:600;">{preferred_date}</td></tr>' if preferred_date else ''}
        {f'<tr><td style="padding:8px 0;color:#999;font-size:13px;">Preferred Time</td><td style="padding:8px 0;color:#111;font-size:14px;">{preferred_time}</td></tr>' if preferred_time else ''}
      </table>
      <p style="font-size:12px;font-weight:700;color:#999;text-transform:uppercase;letter-spacing:0.06em;margin:0 0 8px;">Their Message</p>
      <div style="background:#f4f4f4;border-radius:8px;padding:16px;font-size:14px;color:#333;line-height:1.6;white-space:pre-wrap;margin-bottom:24px;">{message}</div>
      <p style="font-size:12px;font-weight:700;color:#999;text-transform:uppercase;letter-spacing:0.06em;margin:0 0 8px;">AI Response Sent to Them</p>
      <div style="background:#eef2ff;border:1px solid #b4c7fb;border-radius:8px;padding:16px;font-size:14px;color:#1e40af;line-height:1.7;white-space:pre-wrap;">{ai_response}</div>
    </div>
  </div>
</body>
</html>
"""

    _send_email(owner_email, f"New Lead: {display_name} ({display_service})", business_name, plain_body, html_body)


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/contact", methods=["GET"])
def contact_page():
    business_name = os.getenv("BUSINESS_NAME", "Home Services Pro")
    contact_html_path = os.path.join(app.static_folder, "contact.html")
    with open(contact_html_path) as f:
        html = f.read().replace("{{BUSINESS_NAME}}", business_name)
    return html


@app.route("/contact", methods=["POST"])
def contact_submit():
    data = request.get_json()
    name = data.get("name", "").strip()
    email = data.get("email", "").strip()
    phone = data.get("phone", "").strip()
    service = data.get("service", "").strip()
    message = data.get("message", "").strip()
    address = data.get("address", "").strip()
    preferred_date = data.get("preferred_date", "").strip()
    preferred_time = data.get("preferred_time", "").strip()
    business_name = os.getenv("BUSINESS_NAME", "Home Services Pro")

    if not email or not message:
        return jsonify({"error": "Email and message are required."}), 400

    user_content = f"Lead name: {name or 'not provided'}\nService requested: {service or 'not specified'}\nAddress: {address or 'not provided'}\nBusiness name: {business_name}\nPreferred date: {preferred_date or 'not specified'}\nPreferred time: {preferred_time or 'not specified'}\n\nTheir message:\n{message}"

    ai_message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=LEAD_RESPONSE_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    ai_response = ai_message.content[0].text

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO leads (name, email, phone, service, address, message, ai_response, preferred_date, preferred_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, email, phone, service, address, message, ai_response, preferred_date, preferred_time)
        )

    try:
        send_lead_response_email(email, ai_response, business_name)
    except Exception as e:
        return jsonify({"success": True, "warning": f"Lead saved but email failed: {str(e)}"})

    owner_email = os.getenv("SENDER_EMAIL")
    if owner_email:
        try:
            send_owner_notification_email(owner_email, business_name, name, email, phone, service, address, message, ai_response, preferred_date, preferred_time)
        except Exception:
            pass

    return jsonify({"success": True})


@app.route("/api/leads", methods=["GET"])
def get_leads():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM leads ORDER BY id DESC").fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/leads/<int:lead_id>/status", methods=["PATCH"])
def update_lead_status(lead_id):
    data = request.get_json()
    status = data.get("status", "").strip()
    valid = {"New", "Responded", "Quoted", "Booked", "Closed", "Lost"}
    if status not in valid:
        return jsonify({"error": "Invalid status."}), 400
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
    return jsonify({"success": True})


@app.route("/quote", methods=["POST"])
def generate_quote():
    data = request.get_json()
    job_description = data.get("description", "").strip()
    customer_email = data.get("customer_email", "").strip()
    customer_name = data.get("customer_name", "").strip()
    business_name = data.get("business_name", "Home Services Pro").strip()
    send_email = data.get("send_email", False)

    if not job_description:
        return jsonify({"error": "Please describe the job."}), 400

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": job_description}],
    )

    quote_text = message.content[0].text

    if "TOTAL ESTIMATE" not in quote_text:
        return jsonify({
            "quote": quote_text,
            "emailed": False,
            "incomplete": True,
            "error_email": "Claude needs more detail to generate a quote. Add more info in the Additional Details field and try again."
        })

    if send_email:
        if not customer_email:
            return jsonify({"error": "Customer email is required to send the quote."}), 400
        try:
            send_quote_email(customer_email, customer_name, quote_text, business_name)
            return jsonify({"quote": quote_text, "emailed": True})
        except ValueError as e:
            return jsonify({"error": str(e)}), 500
        except Exception as e:
            return jsonify({"error": f"Failed to send email: {str(e)}"}), 500

    return jsonify({"quote": quote_text, "emailed": False})


@app.route("/invoice", methods=["POST"])
def create_invoice():
    data = request.get_json()
    customer_email = data.get("customer_email", "").strip()
    customer_name = data.get("customer_name", "").strip()
    business_name = data.get("business_name", "Home Services Pro").strip()
    invoice_number = data.get("invoice_number", "").strip()
    invoice_date = data.get("invoice_date", "").strip()
    due_date = data.get("due_date", "").strip()
    line_items = data.get("line_items", [])
    notes = data.get("notes", "").strip()

    if not customer_email:
        return jsonify({"error": "Customer email is required."}), 400
    if not line_items:
        return jsonify({"error": "At least one line item is required."}), 400

    total = sum(float(item.get("amount", 0)) for item in line_items)

    try:
        send_invoice_email(
            customer_email, customer_name, business_name,
            invoice_number, invoice_date, due_date,
            line_items, total, notes
        )
        return jsonify({"success": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": f"Failed to send invoice: {str(e)}"}), 500


def send_booking_confirmation_email(recipient_email, customer_name, business_name,
                                    service, preferred_date, preferred_time, message):
    greeting = f"Hi {customer_name}," if customer_name else "Hi,"
    date_str = preferred_date or "To be confirmed"
    time_str = preferred_time or "To be confirmed"
    service_str = service or "General service"

    plain_body = f"""{greeting}

Thanks for booking with {business_name}! We've received your request and will confirm your appointment shortly.

Here's what we have:
Service: {service_str}
Preferred Date: {date_str}
Preferred Time: {time_str}
{f'Notes: {message}' if message else ''}

We'll be in touch within a few hours to confirm. If you need to reach us sooner, just reply to this email.

— {business_name}
"""

    html_body = f"""
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f9f9f9;padding:32px;">
  <div style="max-width:580px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;border:1px solid #e5e5e5;">
    <div style="background:#0a0a0a;padding:24px 32px;">
      <h2 style="color:#4169e1;margin:0;font-size:18px;">{business_name}</h2>
      <p style="color:#888;margin:4px 0 0;font-size:13px;">Booking Request Received</p>
    </div>
    <div style="padding:32px;">
      <p style="color:#333;margin:0 0 20px;">{greeting}</p>
      <p style="color:#333;margin:0 0 24px;">Thanks for booking with <strong>{business_name}</strong>! We've received your request and will confirm your appointment shortly.</p>
      <div style="background:#f4f4f4;border-radius:8px;padding:20px;margin-bottom:24px;">
        <table style="width:100%;border-collapse:collapse;">
          <tr><td style="padding:6px 0;color:#999;font-size:13px;width:130px;">Service</td><td style="padding:6px 0;color:#111;font-size:14px;font-weight:600;">{service_str}</td></tr>
          <tr><td style="padding:6px 0;color:#999;font-size:13px;">Preferred Date</td><td style="padding:6px 0;color:#111;font-size:14px;font-weight:600;">{date_str}</td></tr>
          <tr><td style="padding:6px 0;color:#999;font-size:13px;">Preferred Time</td><td style="padding:6px 0;color:#111;font-size:14px;font-weight:600;">{time_str}</td></tr>
          {f'<tr><td style="padding:6px 0;color:#999;font-size:13px;vertical-align:top;">Notes</td><td style="padding:6px 0;color:#111;font-size:14px;">{message}</td></tr>' if message else ''}
        </table>
      </div>
      <p style="color:#666;font-size:13px;margin:0;">We'll be in touch within a few hours to confirm. Reply to this email if you need anything sooner.</p>
    </div>
    <div style="background:#f4f4f4;padding:16px 32px;text-align:center;">
      <p style="color:#999;font-size:12px;margin:0;">— {business_name}</p>
    </div>
  </div>
</body>
</html>
"""

    _send_email(recipient_email, f"Booking Request Received — {business_name}", business_name, plain_body, html_body)


def send_booking_notification_email(owner_email, business_name, name, email, phone,
                                    service, preferred_date, preferred_time, message):
    display_service = service or "Not specified"
    date_str = preferred_date or "Not specified"
    time_str = preferred_time or "Not specified"

    plain_body = f"""New booking request from {business_name}.

Name: {name}
Email: {email}
Phone: {phone or 'Not provided'}
Service: {display_service}
Preferred Date: {date_str}
Preferred Time: {time_str}
{f'Notes: {message}' if message else ''}
"""

    html_body = f"""
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f9f9f9;padding:32px;">
  <div style="max-width:580px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;border:1px solid #e5e5e5;">
    <div style="background:#0a0a0a;padding:24px 32px;">
      <h2 style="color:#4169e1;margin:0;font-size:18px;">New Booking — {business_name}</h2>
      <p style="color:#888;margin:4px 0 0;font-size:13px;">Appointment request</p>
    </div>
    <div style="padding:32px;">
      <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
        <tr><td style="padding:8px 0;color:#999;font-size:13px;width:130px;">Name</td><td style="padding:8px 0;color:#111;font-size:14px;font-weight:600;">{name}</td></tr>
        <tr><td style="padding:8px 0;color:#999;font-size:13px;">Email</td><td style="padding:8px 0;color:#111;font-size:14px;">{email}</td></tr>
        <tr><td style="padding:8px 0;color:#999;font-size:13px;">Phone</td><td style="padding:8px 0;color:#111;font-size:14px;">{phone or '—'}</td></tr>
        <tr><td style="padding:8px 0;color:#999;font-size:13px;">Service</td><td style="padding:8px 0;color:#111;font-size:14px;">{display_service}</td></tr>
        <tr><td style="padding:8px 0;color:#999;font-size:13px;">Preferred Date</td><td style="padding:8px 0;color:#111;font-size:14px;font-weight:600;">{date_str}</td></tr>
        <tr><td style="padding:8px 0;color:#999;font-size:13px;">Preferred Time</td><td style="padding:8px 0;color:#111;font-size:14px;font-weight:600;">{time_str}</td></tr>
      </table>
      {f'<p style="font-size:12px;font-weight:700;color:#999;text-transform:uppercase;letter-spacing:0.06em;margin:0 0 8px;">Notes</p><div style="background:#f4f4f4;border-radius:8px;padding:16px;font-size:14px;color:#333;line-height:1.6;">{message}</div>' if message else ''}
    </div>
  </div>
</body>
</html>
"""

    _send_email(owner_email, f"New Booking: {name} — {display_service}", business_name, plain_body, html_body)


@app.route("/book", methods=["GET"])
def booking_page():
    business_name = os.getenv("BUSINESS_NAME", "Home Services Pro")
    booking_html_path = os.path.join(app.static_folder, "booking.html")
    with open(booking_html_path) as f:
        html = f.read().replace("{{BUSINESS_NAME}}", business_name)
    return html


@app.route("/book", methods=["POST"])
def booking_submit():
    data = request.get_json()
    name = data.get("name", "").strip()
    email = data.get("email", "").strip()
    phone = data.get("phone", "").strip()
    service = data.get("service", "").strip()
    preferred_date = data.get("preferred_date", "").strip()
    preferred_time = data.get("preferred_time", "").strip()
    message = data.get("message", "").strip()
    business_name = os.getenv("BUSINESS_NAME", "Home Services Pro")

    if not name or not email:
        return jsonify({"error": "Name and email are required."}), 400

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO bookings (name, email, phone, service, preferred_date, preferred_time, message) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, email, phone, service, preferred_date, preferred_time, message)
        )

    try:
        send_booking_confirmation_email(email, name, business_name, service, preferred_date, preferred_time, message)
    except Exception as e:
        return jsonify({"success": True, "warning": f"Booking saved but confirmation email failed: {str(e)}"})

    owner_email = os.getenv("SENDER_EMAIL")
    if owner_email:
        try:
            send_booking_notification_email(owner_email, business_name, name, email, phone,
                                            service, preferred_date, preferred_time, message)
        except Exception:
            pass

    return jsonify({"success": True})


@app.route("/api/bookings", methods=["GET"])
def get_bookings():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM bookings ORDER BY id DESC").fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/bookings/<int:booking_id>/status", methods=["PATCH"])
def update_booking_status(booking_id):
    data = request.get_json()
    status = data.get("status", "").strip()
    valid = {"Pending", "Confirmed", "Cancelled", "Completed"}
    if status not in valid:
        return jsonify({"error": "Invalid status."}), 400
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE bookings SET status = ? WHERE id = ?", (status, booking_id))
    return jsonify({"success": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=False, host="0.0.0.0", port=port)
