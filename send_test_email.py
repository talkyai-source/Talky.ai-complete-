import smtplib
from email.mime.text import MIMEText

SENDER = "hishamkhan682@gmail.com"
APP_PASSWORD = input("Enter your Gmail App Password: ")
RECIPIENT = "hishamkhan682@gmail.com"

msg = MIMEText("This is a test email sent from the Talky.ai project.")
msg["Subject"] = "Talky.ai - Test Email"
msg["From"] = SENDER
msg["To"] = RECIPIENT

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(SENDER, APP_PASSWORD)
    server.sendmail(SENDER, RECIPIENT, msg.as_string())

print("Test email sent successfully!")
