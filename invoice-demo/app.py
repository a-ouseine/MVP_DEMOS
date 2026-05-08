import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder="static", static_url_path="/static")

SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_APP_PASSWORD = os.getenv("SENDER_APP_PASSWORD")


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/invoice", methods=["POST"])
def invoice():
    data = request.json

    customer_name = data.get("customer_name", "").strip()
    customer_email = data.get("customer_email", "").strip()
    invoice_number = data.get("invoice_number", "").strip()
    invoice_date = data.get("invoice_date", "").strip()
    due_date = data.get("due_date", "").strip()
    business_name = data.get("business_name", "").strip()
    line_items = data.get("line_items", [])
    tax_rate = float(data.get("tax_rate", 0) or 0)
    notes = data.get("notes", "").strip()
    sender_email = data.get("sender_email", "").strip()

    if not customer_name or not customer_email or not invoice_number or not business_name:
        return jsonify({"error": "Please fill in all required fields."}), 400

    if not line_items:
        return jsonify({"error": "Please add at least one line item."}), 400

    subtotal = sum(float(item.get("qty", 0)) * float(item.get("price", 0)) for item in line_items)
    tax_amount = subtotal * (tax_rate / 100)
    total = subtotal + tax_amount

    try:
        send_invoice_email(
            customer_name=customer_name,
            customer_email=customer_email,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            due_date=due_date,
            business_name=business_name,
            line_items=line_items,
            subtotal=subtotal,
            tax_rate=tax_rate,
            tax_amount=tax_amount,
            total=total,
            notes=notes,
            sender_email=sender_email,
        )
    except Exception as e:
        print(f"EMAIL ERROR: {e}")
        return jsonify({"error": f"Invoice generated but email failed: {str(e)}"}), 500

    return jsonify({"success": True})


def send_invoice_email(customer_name, customer_email, invoice_number, invoice_date,
                       due_date, business_name, line_items, subtotal, tax_rate,
                       tax_amount, total, notes, sender_email=""):

    rows_html = ""
    for item in line_items:
        desc = item.get("description", "")
        qty = float(item.get("qty", 0))
        price = float(item.get("price", 0))
        amount = qty * price
        rows_html += f"""
        <tr>
          <td style="padding:11px 14px;color:#334155;border-bottom:1px solid #e8edf5;font-size:14px;">{desc}</td>
          <td style="padding:11px 14px;color:#334155;border-bottom:1px solid #e8edf5;text-align:center;font-size:14px;">{qty:g}</td>
          <td style="padding:11px 14px;color:#334155;border-bottom:1px solid #e8edf5;text-align:right;font-size:14px;">${price:,.2f}</td>
          <td style="padding:11px 14px;color:#0a1628;border-bottom:1px solid #e8edf5;text-align:right;font-size:14px;font-weight:700;">${amount:,.2f}</td>
        </tr>"""

    tax_row = ""
    if tax_rate > 0:
        tax_row = f"""
        <tr>
          <td colspan="3" style="padding:8px 14px;color:#64748b;text-align:right;font-size:13px;">Tax ({tax_rate:g}%)</td>
          <td style="padding:8px 14px;color:#334155;text-align:right;font-size:13px;">${tax_amount:,.2f}</td>
        </tr>"""

    notes_html = ""
    if notes:
        notes_html = f"""
      <div style="margin-top:24px;padding:16px;background:#f0f4f8;border-radius:8px;border:1px solid #dce4ef;">
        <p style="color:#4169e1;font-size:11px;font-weight:700;letter-spacing:1px;margin:0 0 8px;text-transform:uppercase;">Notes & Payment Terms</p>
        <p style="color:#64748b;font-size:14px;margin:0;line-height:1.7;">{notes}</p>
      </div>"""

    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f5f7fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:640px;margin:0 auto;padding:40px 20px;">

    <div style="background:#ffffff;border-radius:16px;padding:36px;border:1px solid #dce4ef;">

      <!-- Header -->
      <div style="display:flex;justify-content:space-between;align-items:flex-start;padding-bottom:24px;border-bottom:1px solid #e8edf5;margin-bottom:28px;">
        <div>
          <h1 style="color:#0a1628;margin:0 0 6px;font-size:28px;font-weight:800;letter-spacing:-0.5px;">INVOICE</h1>
          <p style="color:#4169e1;margin:0;font-size:14px;font-weight:700;letter-spacing:1px;">#{invoice_number}</p>
        </div>
        <div style="text-align:right;">
          <p style="color:#0a1628;font-size:17px;font-weight:700;margin:0 0 4px;">{business_name}</p>
          <p style="color:#94a3b8;font-size:12px;margin:0;">Powered by Flocean AI</p>
        </div>
      </div>

      <!-- Bill To / Dates -->
      <div style="display:flex;justify-content:space-between;margin-bottom:32px;">
        <div>
          <p style="color:#4169e1;font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;margin:0 0 10px;">Bill To</p>
          <p style="color:#0a1628;font-size:16px;font-weight:600;margin:0 0 4px;">{customer_name}</p>
          <p style="color:#64748b;font-size:13px;margin:0;">{customer_email}</p>
        </div>
        <div style="text-align:right;">
          <p style="color:#4169e1;font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;margin:0 0 10px;">Details</p>
          <p style="color:#64748b;font-size:13px;margin:0 0 4px;">Issued: <span style="color:#334155;">{invoice_date}</span></p>
          <p style="color:#64748b;font-size:13px;margin:0;">Due: <span style="color:#0a1628;font-weight:700;">{due_date}</span></p>
        </div>
      </div>

      <!-- Line Items -->
      <div style="background:#f8fafc;border-radius:10px;border:1px solid #e8edf5;overflow:hidden;margin-bottom:20px;">
        <table style="width:100%;border-collapse:collapse;">
          <thead>
            <tr style="background:#f0f4f8;">
              <th style="padding:10px 14px;color:#4169e1;font-size:11px;letter-spacing:1px;text-align:left;font-weight:700;text-transform:uppercase;">Description</th>
              <th style="padding:10px 14px;color:#4169e1;font-size:11px;letter-spacing:1px;text-align:center;font-weight:700;text-transform:uppercase;">Qty</th>
              <th style="padding:10px 14px;color:#4169e1;font-size:11px;letter-spacing:1px;text-align:right;font-weight:700;text-transform:uppercase;">Price</th>
              <th style="padding:10px 14px;color:#4169e1;font-size:11px;letter-spacing:1px;text-align:right;font-weight:700;text-transform:uppercase;">Amount</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>

      <!-- Totals -->
      <div style="text-align:right;margin-bottom:8px;">
        <table style="margin-left:auto;border-collapse:collapse;min-width:220px;">
          <tr>
            <td style="padding:6px 14px;color:#64748b;font-size:14px;">Subtotal</td>
            <td style="padding:6px 14px;color:#334155;font-size:14px;text-align:right;">${subtotal:,.2f}</td>
          </tr>
          {tax_row}
          <tr>
            <td colspan="2" style="padding:4px 0;"><div style="border-top:1px solid #e8edf5;"></div></td>
          </tr>
          <tr>
            <td style="padding:10px 14px;color:#0a1628;font-size:17px;font-weight:700;">Total Due</td>
            <td style="padding:10px 14px;text-align:right;">
              <span style="font-size:20px;font-weight:800;color:#4169e1;">${total:,.2f}</span>
            </td>
          </tr>
        </table>
      </div>

      {notes_html}

    </div>

    <div style="text-align:center;margin-top:24px;">
      <p style="color:#94a3b8;font-size:12px;margin:0;">
        Powered by <span style="color:#4169e1;font-weight:600;">Flocean AI</span> &nbsp;·&nbsp; Done-for-you job acquisition for home service businesses
      </p>
    </div>

  </div>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Invoice #{invoice_number} from {business_name}"
    msg["From"] = SENDER_EMAIL
    msg["To"] = customer_email
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
        smtp.sendmail(SENDER_EMAIL, customer_email, msg.as_string())

        if sender_email:
            copy_msg = MIMEMultipart("alternative")
            copy_msg["Subject"] = f"[Copy] Invoice #{invoice_number} sent to {customer_name}"
            copy_msg["From"] = SENDER_EMAIL
            copy_msg["To"] = sender_email
            copy_msg.attach(MIMEText(html_body, "html"))
            smtp.sendmail(SENDER_EMAIL, sender_email, copy_msg.as_string())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
