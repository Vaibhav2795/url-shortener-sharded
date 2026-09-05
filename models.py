from sqlalchemy import Column, BigInteger, String, DateTime
import datetime

from db import Base

class URLMapping(Base):
    __tablename__ = "url_mappings"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    short_code = Column(String, unique=True, index=True, nullable=True)
    long_url = Column(String, nullable=True)

