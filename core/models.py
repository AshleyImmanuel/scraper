from pydantic import BaseModel, Field
from typing import Literal

class ExtractionRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=1000)
    minViews: int = Field(default=0, ge=0)
    maxViews: int = Field(default=0, ge=0)  # 0 = no upper limit
    minSubs: int = Field(default=0, ge=0)
    maxSubs: int = Field(default=0, ge=0)   # 0 = no upper limit
    region: Literal["Both", "US", "UK"] = "Both"
    dateFilter: Literal["Today", "This Week", "Last Month", "This Year"] = "This Year"
    videoType: Literal["All", "Shorts", "Long"] = "All"
    searchPoolSize: int = Field(default=500, ge=50, le=5000)
