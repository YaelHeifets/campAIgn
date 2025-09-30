# models.py
from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()

class Campaign(Base):
    __tablename__ = "campaigns"
    id = Column(String, primary_key=True)                  
    name = Column(String, nullable=False)
    audience = Column(String, nullable=False)
    default_channel = Column(String, nullable=False)       # Email/SMS/Social/Ads
    goal = Column(String)
    budget = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    business_desc = Column(String(500), default="")
    landing_url = Column(String(500), default="")
    archived_at = Column(DateTime, nullable=True)

    assets = relationship("Asset", back_populates="campaign", cascade="all, delete-orphan")

    from datetime import datetime
    from sqlalchemy import Column, DateTime

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Asset(Base):
    __tablename__ = "assets"
    id = Column(String, primary_key=True)
    campaign_id = Column(String, ForeignKey("campaigns.id"), nullable=False)
    kind = Column(String, nullable=False)                  # brief/email/sms/social/ads
    content = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.utcnow)

    campaign = relationship("Campaign", back_populates="assets")
