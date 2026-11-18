from pydantic_settings import BaseSettings
from app.settings import get_settings

class DeliveryReceiptSender():
  def __init__(self):
    self.settings = get_settings()
    
  def create(self):
    pass

  def send(self, message_id: str, status: str, delay: int = 0):
    pass