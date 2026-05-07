import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
import anthropic

load_dotenv()

app = Flask(__name__, static_folder="static", static_url_path="/static")

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_APP_PASSWORD = os.getenv("SENDER_APP_PASSWORD")
BOOKING_URL = "https://cal.com/ahamed-bangoura-sut4be/15min"


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/estimate", methods=["POST"])
def estimate():
    data = request.json
    name = data.get("name", "").strip()
    email = data.get("email", "").strip()
    job_type = data.get("job_type", "").strip()
    property_size = data.get("property_size", "").strip()
    description = data.get("description", "").strip()
    location = data.get("location", "").strip()
    owner_email = data.get("owner_email", "").strip()

    if not name or not email or not job_type or not description:
        return jsonify({"error": "Please fill in all required fields."}), 400

    prompt = f"""You are a professional landscaping estimator with 15 years of experience.
Generate a detailed, professional estimate for the following job. Be specific with price ranges based on typical North American market rates.
Do NOT ask clarifying questions. Work with what is provided and make reasonable assumptions where needed.

Client: {name}
Job Type: {job_type}
Property Size: {property_size if property_size else 'Not specified — assume average residential'}
Location: {location if location else 'Not specified'}
Job Description: {description}

Format the estimate exactly like this — use plain text, no markdown asterisks:

LANDSCAPING ESTIMATE
Prepared for: {name}

SCOPE OF WORK
[Detailed breakdown of exactly what will be done, 3-5 sentences]

ESTIMATED INVESTMENT
Labour: $X – $X
Materials: $X – $X
TOTAL: $X – $X

PROJECT TIMELINE
[Realistic start and completion timeframe]

WHATS INCLUDED
- [Item]
- [Item]
- [Item]
- [Item]

NEXT STEPS
Book a free 15-minute consultation call to confirm the exact scope, answer any questions, and get you on the schedule.

Keep it confident, specific, and professional. No filler sentences."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    estimate_text = message.content[0].text

    try:
        send_estimate_email(name, email, job_type, estimate_text, owner_email)
    except Exception as e:
        print(f"EMAIL ERROR: {e}")
        return jsonify({"error": f"Estimate generated but email failed: {str(e)}"}), 500

    return jsonify({"success": True})


def send_estimate_email(name, email, job_type, estimate_text, owner_email=""):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Your Free Landscaping Estimate — {job_type}"
    msg["From"] = SENDER_EMAIL
    msg["To"] = email

    lines = estimate_text.strip().split("\n")
    html_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            html_lines.append("<br>")
        elif line.isupper() and len(line) < 40:
            html_lines.append(f'<p style="color:#4169e1;font-weight:700;font-size:13px;letter-spacing:1px;margin:20px 0 6px;">{line}</p>')
        elif line.startswith("- "):
            html_lines.append(f'<p style="color:#c8d4e3;margin:4px 0;padding-left:12px;">• {line[2:]}</p>')
        else:
            html_lines.append(f'<p style="color:#c8d4e3;margin:6px 0;line-height:1.7;">{line}</p>')

    html_estimate = "\n".join(html_lines)

    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#0a1628;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:600px;margin:0 auto;padding:40px 20px;">

    <div style="text-align:center;margin-bottom:32px;">
      <div style="font-size:13px;color:#4a5568;letter-spacing:1px;text-transform:uppercase;">Free Landscaping Estimate</div>
    </div>

    <div style="background:#112240;border-radius:16px;padding:32px;border:1px solid #1e3a5f;">
      <h2 style="color:#ffffff;margin:0 0 8px;font-size:22px;">Hi {name},</h2>
      <p style="color:#8892a4;margin:0 0 28px;font-size:15px;">
        Here's your instant estimate for <strong style="color:#ffffff;">{job_type}</strong>.
        Everything you need is below — take your time looking it over.
      </p>

      <div style="background:#0a1628;border-radius:12px;padding:24px;border:1px solid #1e3a5f;">
        {html_estimate}
      </div>

      <div style="margin-top:32px;text-align:center;padding:24px;background:#0d2137;border-radius:12px;border:1px solid #1e3a5f;">
        <p style="color:#ffffff;font-size:16px;font-weight:600;margin:0 0 8px;">Ready to move forward?</p>
        <p style="color:#8892a4;font-size:14px;margin:0 0 20px;">Book a free 15-minute call. We'll confirm the scope and get you on the schedule.</p>
        <a href="{BOOKING_URL}"
           style="display:inline-block;background:linear-gradient(90deg,#4169e1,#00c6ff);color:#ffffff;text-decoration:none;padding:14px 32px;border-radius:8px;font-weight:600;font-size:15px;">
          Book Your Free Call →
        </a>
      </div>
    </div>

    <div style="text-align:center;margin-top:28px;">
      <p style="color:#2d3748;font-size:12px;margin:0;">
        Powered by <span style="color:#4169e1;font-weight:600;">Flocean AI</span> &nbsp;·&nbsp; Done-for-you job acquisition for landscaping businesses
      </p>
    </div>

  </div>
</body>
</html>"""

    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
        smtp.sendmail(SENDER_EMAIL, email, msg.as_string())

        if owner_email:
            copy_msg = MIMEMultipart("alternative")
            copy_msg["Subject"] = f"[Copy] Estimate sent to {name} — {job_type}"
            copy_msg["From"] = SENDER_EMAIL
            copy_msg["To"] = owner_email
            copy_msg.attach(MIMEText(html_body, "html"))
            smtp.sendmail(SENDER_EMAIL, owner_email, copy_msg.as_string())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
