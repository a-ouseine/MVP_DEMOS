import os, base64, requests, json, re, smtplib
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
import anthropic
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

load_dotenv()

app = Flask(__name__, static_folder="static", static_url_path="/static")
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_APP_PASSWORD = os.getenv("SENDER_APP_PASSWORD")

BASE_RATES = {
    "lawn_mowing":   0.012,
    "edging":        0.003,
    "leaf_cleanup":  0.008,
    "hedge_trimming": 45.0,
    "mulching":      55.0,
    "fertilization": 0.004,
    "aeration":      0.006,
}
COMPLEXITY_MULT  = {"simple": 1.0, "moderate": 1.25, "complex": 1.55}
FREQUENCY_DISC   = {"one-time": 1.0, "monthly": 0.95, "bi-weekly": 0.90, "weekly": 0.85}
FREQUENCY_LABELS = {"one-time": "One-time", "monthly": "Monthly", "bi-weekly": "Bi-weekly", "weekly": "Weekly"}
SERVICE_LABELS   = {
    "lawn_mowing":   "Lawn Mowing",
    "edging":        "Edging & Trimming",
    "leaf_cleanup":  "Leaf Cleanup",
    "hedge_trimming":"Hedge Trimming",
    "mulching":      "Mulching",
    "fertilization": "Lawn Fertilization",
    "aeration":      "Core Aeration",
}


def geocode_address(address):
    resp = requests.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": address, "key": GOOGLE_MAPS_API_KEY},
        timeout=10,
    )
    data = resp.json()
    if data.get("status") != "OK":
        return None
    result = data["results"][0]
    loc = result["geometry"]["location"]
    return {"lat": loc["lat"], "lng": loc["lng"], "formatted_address": result["formatted_address"]}


def get_satellite_image(lat, lng):
    resp = requests.get(
        "https://maps.googleapis.com/maps/api/staticmap",
        params={
            "center": f"{lat},{lng}",
            "zoom": 19,
            "size": "640x640",
            "maptype": "satellite",
            "format": "png",
            "key": GOOGLE_MAPS_API_KEY,
        },
        timeout=15,
    )
    return base64.b64encode(resp.content).decode("utf-8")


def analyze_property(image_b64, address, services):
    service_list = ", ".join(SERVICE_LABELS.get(s, s) for s in services)
    prompt = f"""Analyze this aerial/satellite image of a residential property for a lawn care quote.

Address: {address}
Services requested: {service_list}

Estimate:
1. Turf/grass area in square feet (exclude house footprint, driveway, patios, walkways, pool)
2. Complexity: simple (flat open lawn), moderate (some beds/trees/irregular edges), or complex (many obstacles, slopes, heavily landscaped)
3. Number of visible garden/flower beds
4. One-sentence observation relevant to the services requested

Respond ONLY with valid JSON, no explanation:
{{"lawn_sqft": <integer>, "complexity": "simple"|"moderate"|"complex", "bed_count": <integer>, "notes": "<string>"}}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    text = msg.content[0].text.strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    return json.loads(match.group() if match else text)


def calculate_quote(analysis, services, frequency):
    lawn_sqft = max(int(analysis.get("lawn_sqft", 2500)), 500)
    complexity = analysis.get("complexity", "moderate")
    bed_count  = max(int(analysis.get("bed_count", 0)), 0)
    mult = COMPLEXITY_MULT.get(complexity, 1.25)
    disc = FREQUENCY_DISC.get(frequency, 1.0)

    items = []
    for svc in services:
        rate = BASE_RATES.get(svc)
        if rate is None:
            continue
        if svc in ("lawn_mowing", "edging", "leaf_cleanup", "fertilization", "aeration"):
            price  = lawn_sqft * rate * mult * disc
            detail = f"{lawn_sqft:,} sq ft"
        elif svc == "mulching":
            beds   = max(bed_count, 1)
            price  = rate * beds * disc
            detail = f"{beds} bed{'s' if beds != 1 else ''}"
        else:
            price  = rate * mult * disc
            detail = "Per visit"
        items.append({"service": SERVICE_LABELS[svc], "detail": detail, "price": round(price, 2)})
    return items


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/quote", methods=["POST"])
def quote_route():
    data = request.json or {}

    if not GOOGLE_MAPS_API_KEY:
        return jsonify({"error": "Google Maps API key not configured."}), 500

    business_name   = data.get("business_name", "").strip()
    owner_email     = data.get("owner_email", "").strip()
    customer_name   = data.get("customer_name", "").strip()
    customer_email  = data.get("customer_email", "").strip()
    customer_phone  = data.get("customer_phone", "").strip()
    address         = data.get("address", "").strip()
    services        = data.get("services", [])
    frequency       = data.get("frequency", "one-time").strip()
    notes           = data.get("notes", "").strip()

    if not all([business_name, customer_name, customer_email, address]):
        return jsonify({"error": "Please fill in all required fields."}), 400
    if not services:
        return jsonify({"error": "Please select at least one service."}), 400

    geo = geocode_address(address)
    if not geo:
        return jsonify({"error": "Address not found. Please try a more specific address."}), 400

    lat, lng, formatted_address = geo["lat"], geo["lng"], geo["formatted_address"]
    image_b64 = get_satellite_image(lat, lng)

    try:
        analysis = analyze_property(image_b64, formatted_address, services)
    except Exception as e:
        print(f"VISION ERROR: {e}")
        analysis = {"lawn_sqft": 2800, "complexity": "moderate", "bed_count": 2,
                    "notes": "Estimated based on typical residential property dimensions."}

    line_items = calculate_quote(analysis, services, frequency)
    total = round(sum(i["price"] for i in line_items), 2)

    try:
        send_quote_email(
            business_name=business_name, owner_email=owner_email,
            customer_name=customer_name, customer_email=customer_email,
            customer_phone=customer_phone, address=formatted_address,
            frequency=frequency, line_items=line_items, total=total,
            analysis=analysis, notes=notes,
        )
    except Exception as e:
        print(f"EMAIL ERROR: {e}")
        return jsonify({"error": f"Quote generated but email failed: {str(e)}"}), 500

    return jsonify({
        "success": True,
        "analysis": analysis,
        "line_items": line_items,
        "total": total,
        "formatted_address": formatted_address,
    })


def send_quote_email(business_name, owner_email, customer_name, customer_email,
                     customer_phone, address, frequency, line_items, total, analysis, notes):

    first_name = customer_name.split()[0]
    freq_label = FREQUENCY_LABELS.get(frequency, frequency.title())

    rows_html = "".join(f"""
        <tr>
          <td style="padding:11px 14px;color:#334155;border-bottom:1px solid #e8edf5;font-size:14px;">{item['service']}</td>
          <td style="padding:11px 14px;color:#64748b;border-bottom:1px solid #e8edf5;font-size:13px;text-align:center;">{item['detail']}</td>
          <td style="padding:11px 14px;color:#0a1628;border-bottom:1px solid #e8edf5;text-align:right;font-size:14px;font-weight:700;">${item['price']:,.2f}</td>
        </tr>""" for item in line_items)

    notes_html = f"""
      <div style="margin-top:20px;padding:14px;background:#f0f4f8;border-radius:8px;border:1px solid #dce4ef;">
        <p style="color:#4169e1;font-size:11px;font-weight:700;letter-spacing:1px;margin:0 0 6px;text-transform:uppercase;">Notes</p>
        <p style="color:#64748b;font-size:14px;margin:0;line-height:1.6;">{notes}</p>
      </div>""" if notes else ""

    customer_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f5f7fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:600px;margin:0 auto;padding:40px 20px;">
    <div style="text-align:center;margin-bottom:24px;">
      <p style="color:#4169e1;font-size:13px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;margin:0;">{business_name}</p>
    </div>
    <div style="background:#ffffff;border-radius:16px;padding:36px;border:1px solid #dce4ef;">
      <p style="color:#94a3b8;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;margin:0 0 20px;">Your Free Estimate</p>
      <p style="color:#0a1628;font-size:22px;font-weight:800;margin:0 0 6px;">Hi {first_name},</p>
      <p style="color:#64748b;font-size:15px;line-height:1.7;margin:0 0 24px;">Here's your personalized estimate for <strong style="color:#334155;">{address}</strong>.</p>
      <div style="background:#f8fafc;border-radius:10px;border:1px solid #e8edf5;overflow:hidden;margin-bottom:20px;">
        <table style="width:100%;border-collapse:collapse;">
          <thead>
            <tr style="background:#f0f4f8;">
              <th style="padding:10px 14px;color:#4169e1;font-size:11px;letter-spacing:1px;text-align:left;font-weight:700;text-transform:uppercase;">Service</th>
              <th style="padding:10px 14px;color:#4169e1;font-size:11px;letter-spacing:1px;text-align:center;font-weight:700;text-transform:uppercase;">Details</th>
              <th style="padding:10px 14px;color:#4169e1;font-size:11px;letter-spacing:1px;text-align:right;font-weight:700;text-transform:uppercase;">Price</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
      <div style="text-align:right;margin-bottom:20px;">
        <table style="margin-left:auto;border-collapse:collapse;">
          <tr>
            <td style="padding:6px 14px;color:#64748b;font-size:13px;">Frequency</td>
            <td style="padding:6px 14px;color:#334155;font-size:13px;font-weight:600;">{freq_label}</td>
          </tr>
          <tr><td colspan="2" style="padding:4px 0;"><div style="border-top:1px solid #e8edf5;"></div></td></tr>
          <tr>
            <td style="padding:10px 14px;color:#0a1628;font-size:17px;font-weight:700;">Total per visit</td>
            <td style="padding:10px 14px;text-align:right;">
              <span style="font-size:22px;font-weight:800;color:#4169e1;">${total:,.2f}</span>
            </td>
          </tr>
        </table>
      </div>
      {notes_html}
      <div style="margin-top:24px;padding:16px;background:#f0f9ff;border-radius:8px;border:1px solid #bae6fd;">
        <p style="color:#0369a1;font-size:13px;margin:0;line-height:1.6;">
          This estimate was generated using AI analysis of your property via satellite imagery. Exact pricing confirmed on first visit — no surprises.
        </p>
      </div>
    </div>
    <div style="text-align:center;margin-top:24px;">
      <p style="color:#94a3b8;font-size:12px;margin:0;">
        Powered by <span style="color:#4169e1;font-weight:600;">Flocean AI</span> &nbsp;·&nbsp; Instant estimates for lawn &amp; landscape businesses
      </p>
    </div>
  </div>
</body>
</html>"""

    lawn_sqft  = analysis.get("lawn_sqft", 0)
    complexity = analysis.get("complexity", "moderate").title()
    bed_count  = analysis.get("bed_count", 0)
    ai_notes   = analysis.get("notes", "")
    phone_row  = f'<p style="color:#64748b;font-size:13px;margin:0 0 4px;">Phone: <span style="color:#334155;font-weight:600;">{customer_phone}</span></p>' if customer_phone else ""
    owner_rows = "".join(
        f'<tr><td style="padding:10px 14px;color:#334155;border-bottom:1px solid #e8edf5;font-size:14px;">{item["service"]}</td>'
        f'<td style="padding:10px 14px;color:#0a1628;border-bottom:1px solid #e8edf5;text-align:right;font-size:14px;font-weight:700;">${item["price"]:,.2f}</td></tr>'
        for item in line_items
    )

    owner_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f5f7fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:600px;margin:0 auto;padding:40px 20px;">
    <div style="background:#ffffff;border-radius:16px;padding:36px;border:1px solid #dce4ef;">
      <div style="margin-bottom:24px;padding-bottom:20px;border-bottom:1px solid #e8edf5;">
        <p style="color:#4169e1;font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;margin:0 0 6px;">Quote Sent — {business_name}</p>
        <p style="color:#0a1628;font-size:20px;font-weight:800;margin:0;">{customer_name}</p>
      </div>
      <p style="color:#64748b;font-size:13px;margin:0 0 4px;">Email: <span style="color:#4169e1;">{customer_email}</span></p>
      {phone_row}
      <p style="color:#64748b;font-size:13px;margin:4px 0 20px;">Property: <span style="color:#334155;font-weight:600;">{address}</span></p>
      <div style="background:#f0f4f8;border-radius:8px;padding:16px;margin-bottom:20px;border:1px solid #dce4ef;">
        <p style="color:#4169e1;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;margin:0 0 10px;">Satellite Analysis</p>
        <p style="color:#334155;font-size:13px;margin:0 0 4px;">
          Turf area: <strong>{lawn_sqft:,} sq ft</strong> &nbsp;·&nbsp;
          Complexity: <strong>{complexity}</strong> &nbsp;·&nbsp;
          Garden beds: <strong>{bed_count}</strong>
        </p>
        <p style="color:#64748b;font-size:13px;margin:6px 0 0;font-style:italic;">{ai_notes}</p>
      </div>
      <div style="background:#f8fafc;border-radius:10px;border:1px solid #e8edf5;overflow:hidden;margin-bottom:20px;">
        <table style="width:100%;border-collapse:collapse;">
          <thead>
            <tr style="background:#f0f4f8;">
              <th style="padding:10px 14px;color:#4169e1;font-size:11px;text-align:left;font-weight:700;text-transform:uppercase;">Service</th>
              <th style="padding:10px 14px;color:#4169e1;font-size:11px;text-align:right;font-weight:700;text-transform:uppercase;">Price</th>
            </tr>
          </thead>
          <tbody>{owner_rows}</tbody>
        </table>
      </div>
      <div style="text-align:right;">
        <span style="font-size:24px;font-weight:800;color:#4169e1;">${total:,.2f}</span>
        <span style="color:#94a3b8;font-size:13px;"> / visit ({freq_label})</span>
      </div>
    </div>
    <div style="text-align:center;margin-top:24px;">
      <p style="color:#94a3b8;font-size:12px;margin:0;">Powered by <span style="color:#4169e1;font-weight:600;">Flocean AI</span></p>
    </div>
  </div>
</body>
</html>"""

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(SENDER_EMAIL, SENDER_APP_PASSWORD)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Your free estimate from {business_name}"
        msg["From"] = SENDER_EMAIL
        msg["To"] = customer_email
        msg.attach(MIMEText(customer_html, "html"))
        smtp.sendmail(SENDER_EMAIL, customer_email, msg.as_string())

        if owner_email:
            note = MIMEMultipart("alternative")
            note["Subject"] = f"Quote sent: {customer_name} — ${total:,.2f}/{frequency}"
            note["From"] = SENDER_EMAIL
            note["To"] = owner_email
            note.attach(MIMEText(owner_html, "html"))
            smtp.sendmail(SENDER_EMAIL, owner_email, note.as_string())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5004))
    app.run(host="0.0.0.0", port=port, debug=False)
