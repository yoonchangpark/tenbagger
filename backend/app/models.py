from sqlalchemy import Column, Integer, String, Float, DateTime, Text, JSON
from datetime import datetime
from app.core.database import Base

class ScoreResult(Base):
    __tablename__ = "score_results"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String(10), unique=True, index=True)
    name = Column(String(50))
    market = Column(String(10))
    
    total_score = Column(Float)
    grade = Column(String(20))
    growth_score = Column(Float)
    stability_score = Column(Float)
    cashflow_score = Column(Float)
    dividend_score = Column(Float)
    consistency_score = Column(Float)
    
    # Store key metrics directly for easy UI access
    close_price = Column(Integer, nullable=True)
    market_cap = Column(Float, nullable=True)  # in 억 (100 million)
    per = Column(Float, nullable=True)
    pbr = Column(Float, nullable=True)
    revenue_cagr_5y = Column(Float, nullable=True)
    debt_ratio = Column(Float, nullable=True)
    avg_fcf_margin = Column(Float, nullable=True)
    
    # Full data dump for flexibility
    raw_score_data = Column(JSON, nullable=True)
    
    # AI generated explanation
    ai_explanation = Column(Text, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
