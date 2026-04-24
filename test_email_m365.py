import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from dotenv import load_dotenv

# Load .env from backend
load_dotenv("backend/.env")

SENDER = os.getenv("EMAIL_USER")
PASSWORD = os.getenv("EMAIL_PASS")
RECIPIENT = "hishamkhan682@gmail.com"

print(f"Sender: {SENDER}")
print(f"Recipient: {RECIPIENT}")
print(f"Password loaded: {'Yes' if PASSWORD else 'No'}")

# Build email
msg = MIMEMultipart("alternative")
msg["Subject"] = "Talky.ai - Email Verification Test"
msg["From"] = SENDER
msg["To"] = RECIPIENT

text_body = """Hello from Talky.ai!

This is a test email to verify the email sending system is working correctly.

Your verification code is: 482916

If you did not request this, please ignore this email.

- Talky.ai Team
"""

html_body = """
<html>
<body style="font-family: Arial, sans-serif; padding: 20px;">
    <h2 style="color: #4F46E5;">Talky.ai - Email Verification</h2>
    <p>Hello from Talky.ai!</p>
    <p>This is a test email to verify the email sending system is working correctly.</p>
    <div style="background-color: #F3F4F6; padding: 20px; border-radius: 8px; text-align: center; margin: 20px 0;">
        <p style="margin: 0; font-size: 14px; color: #6B7280;">Your verification code is:</p>
        <p style="margin: 10px 0; font-size: 32px; font-weight: bold; color: #4F46E5; letter-spacing: 8px;">482916</p>
    </div>
    <p style="color: #6B7280; font-size: 12px;">If you did not request this, please ignore this email.</p>
    <p>- Talky.ai Team</p>
</body>
</html>
"""

msg.attach(MIMEText(text_body, "plain"))
msg.attach(MIMEText(html_body, "html"))

# Try Microsoft 365 SMTP (Office 365)
print("\nConnecting to Microsoft 365 SMTP (smtp.office365.com:587)...")
try:
    with smtplib.SMTP("smtp.office365.com", 587, timeout=30) as server:
        server.set_debuglevel(1)
        server.ehlo()
        server.starttls()
        server.ehlo()
        print("Logging in...")
        server.login(SENDER, PASSWORD)
        print("Sending email...")
        server.sendmail(SENDER, RECIPIENT, msg.as_string())
    print("\n✅ Test email sent successfully to", RECIPIENT)
except Exception as e:
    print(f"\n❌ Failed with Office 365 SMTP: {e}")

    # Fallback: try GoDaddy SMTP relay
    print("\nTrying alternative SMTP (smtpout.secureserver.net:465)...")
    try:
        with smtplib.SMTP_SSL("smtpout.secureserver.net", 465, timeout=30) as server:
            server.set_debuglevel(1)
            server.login(SENDER, PASSWORD)
            server.sendmail(SENDER, RECIPIENT, msg.as_string())
        print("\n✅ Test email sent successfully via GoDaddy SMTP to", RECIPIENT)
    except Exception as e2:
        print(f"\n❌ Failed with GoDaddy SMTP too: {e2}")
        print("\nPlease verify:")
        print("1. EMAIL_USER and EMAIL_PASS in backend/.env are correct")
        print("2. The email account allows SMTP access")
        print("3. If MFA is enabled, use an App Password")
