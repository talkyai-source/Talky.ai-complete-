import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

msg = MIMEMultipart("alternative")
msg["Subject"] = "Talky.ai - Test Email Verification"
msg["From"] = "noreply@talkleeai.com"
msg["To"] = "hishamkhan682@gmail.com"
msg.attach(MIMEText("Welcome to Talky.ai! Test verification email. Token: TKN-TEST-123456", "plain"))
msg.attach(MIMEText(
    "<h2>Welcome to Talky.ai!</h2>"
    "<p>This is a test verification email sent from the backend.</p>"
    "<p>Your verification token: <strong>TKN-TEST-123456</strong></p>",
    "html",
))

ctx = ssl.create_default_context()
with smtplib.SMTP("smtp.office365.com", 587) as server:
    server.ehlo()
    server.starttls(context=ctx)
    server.ehlo()
    server.login("noreply@talkleeai.com", "cKxQ6Et93Qjfd@r")
    server.send_message(msg)
    print("SUCCESS - email sent to hishamkhan682@gmail.com")
