import logging
import email
from email.message import EmailMessage
import aiosmtplib
import aioimaplib
from app.db.client import get_supabase_client
from app.common.enums import ContactChannelType
from app.config import settings

logger = logging.getLogger(__name__)

class EmailAdapter:
    def __init__(self):
        self.channel_type = ContactChannelType.EMAIL
        self.db = get_supabase_client()
        self.smtp_server = settings.smtp_server
        self.smtp_port = settings.smtp_port
        self.smtp_user = settings.smtp_user
        self.smtp_password = settings.smtp_password
        self.imap_server = settings.imap_server
        self.imap_port = settings.imap_port

    async def send(self, destination: str, message_text: str, context: dict) -> dict:
        """
        Sends an email using aiosmtplib.
        """
        if not self.smtp_server:
            logger.warning("[EmailAdapter] SMTP server not configured. Simulating send.")
            return {"status": "SENT", "external_id": f"sim_{context.get('conversation_id', 'unknown')}"}

        message = EmailMessage()
        message["From"] = self.smtp_user
        message["To"] = destination
        message["Subject"] = "Inquiry regarding your property listing"
        
        # Add conversation ID in headers to correlate replies
        conv_id = context.get('conversation_id')
        if conv_id:
            message["References"] = f"<{conv_id}@flathunter.app>"
            message["In-Reply-To"] = f"<{conv_id}@flathunter.app>"
            
        message.set_content(message_text)

        try:
            await aiosmtplib.send(
                message,
                hostname=self.smtp_server,
                port=self.smtp_port,
                username=self.smtp_user,
                password=self.smtp_password,
                start_tls=True if self.smtp_port == 587 else False,
                use_tls=True if self.smtp_port == 465 else False
            )
            logger.info(f"[EmailAdapter] Sent email to {destination}")
            return {"status": "SENT", "external_id": f"email_{conv_id}"}
        except Exception as e:
            logger.error(f"[EmailAdapter] Failed to send email: {e}")
            return {"status": "FAILED", "external_id": None}

    async def poll_inbound(self) -> list[dict]:
        """
        Polls IMAP for inbound replies.
        """
        if not self.imap_server:
            # logger.debug("[EmailAdapter] IMAP server not configured. Skipping poll.")
            return []
            
        results = []
        try:
            imap_client = aioimaplib.IMAP4_SSL(host=self.imap_server, port=self.imap_port)
            await imap_client.wait_hello_from_server()
            await imap_client.login(self.smtp_user, self.smtp_password)
            await imap_client.select('INBOX')
            
            # Search for unread messages
            typ, data = await imap_client.search('UNSEEN')
            if typ == 'OK' and data[0]:
                msg_ids = data[0].decode().split()
                for msg_id in msg_ids:
                    typ, msg_data = await imap_client.fetch(msg_id, '(RFC822)')
                    if typ == 'OK':
                        raw_email = msg_data[1]
                        msg = email.message_from_bytes(raw_email)
                        
                        # Try to extract conversation_id from References
                        references = msg.get("References", "")
                        conv_id = None
                        if "@flathunter.app" in references:
                            parts = references.split("@flathunter.app")
                            if parts:
                                possible = parts[0].split("<")[-1]
                                conv_id = possible
                        
                        # Extract text
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    body = part.get_payload(decode=True).decode()
                                    break
                        else:
                            body = msg.get_payload(decode=True).decode()
                            
                        results.append({
                            "conversation_id": conv_id,
                            "text": body,
                            "from": msg.get("From")
                        })
                        
            await imap_client.logout()
        except Exception as e:
            logger.error(f"[EmailAdapter] Failed to poll IMAP: {e}")
            
        return results
