# app.py — FastAPI server for Atmosphere
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel, Field, Session, select, create_engine
from typing import Optional, List
from dotenv import load_dotenv
from requests import RequestException
import requests
import os

load_dotenv()  # load .env so GOOGLE_PLACES_KEY/DATABASE_URL are available

# ---- DB ----
DB_URL = os.getenv("DATABASE_URL", "sqlite:///atmosphere.db")
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, echo=False, connect_args=connect_args)

# ---- Models ----
class Venue(SQLModel, table=True):
    id: str = Field(primary_key=True)          # can be Google place_id
    name: str
    address: str
    cover_img_url: Optional[str] = None
    vibes: str = ""                            # "quiet,bright,outlets"
    summary: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    source: Optional[str] = None               # "google" | "yelp" | etc.

class Rating(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user: str
    venue_id: str
    swipe: str            # 'up' or 'down'
    stars: Optional[int] = None
    comment: Optional[str] = None

# ---- App ----
app = FastAPI(title="Atmosphere API", version="0.1.0")
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# CORS so web/mobile can call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/venues", response_model=List[Venue])
def list_venues(q: Optional[str] = None, limit: int = 25, offset: int = 0):
    with Session(engine) as s:
        stmt = select(Venue)
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(
                Venue.vibes.ilike(like) |
                Venue.name.ilike(like) |
                Venue.summary.ilike(like)
            )
        return s.exec(stmt.limit(limit).offset(offset)).all()

@app.get("/venues/{venue_id}", response_model=Venue)
def get_venue(venue_id: str):
    with Session(engine) as s:
        v = s.get(Venue, venue_id)
        if not v:
            raise HTTPException(404, "venue not found")
        return v

@app.post("/ratings")
def create_rating(r: Rating):
    if r.swipe not in ("up", "down"):
        raise HTTPException(400, "swipe must be 'up' or 'down'")
    with Session(engine) as s:
        s.add(r)
        s.commit()
        s.refresh(r)
        return {"ok": True, "id": r.id}

@app.get("/ratings/summary")
def rating_summary():
    with Session(engine) as s:
        ups = s.exec(select(Rating).where(Rating.swipe == "up")).all()
        downs = s.exec(select(Rating).where(Rating.swipe == "down")).all()
        return {"likes": len(ups), "dislikes": len(downs)}

# ---------- Google Places discovery ----------
def upsert_venue_from_google(obj: dict) -> Venue:
    place_id = obj.get("place_id")
    name = obj.get("name", "")
    addr = obj.get("vicinity") or obj.get("formatted_address") or ""
    loc = (obj.get("geometry") or {}).get("location") or {}
    photos = obj.get("photos") or []
    photo_url = None
    if photos:
        pref = photos[0].get("photo_reference")
        api_key = os.getenv("GOOGLE_PLACES_KEY")
        if pref and api_key:
            photo_url = (
                "https://maps.googleapis.com/maps/api/place/photo"
                f"?maxwidth=1200&photo_reference={pref}&key={api_key}"
            )

    v = Venue(
        id=place_id,
        name=name,
        address=addr,
        cover_img_url=photo_url,
        vibes="",
        summary="",
        lat=loc.get("lat"),
        lng=loc.get("lng"),
        source="google",
    )

    with Session(engine) as s:
        existing = s.get(Venue, v.id)
        if existing:
            for k in ["name", "address", "cover_img_url", "lat", "lng", "source"]:
                setattr(existing, k, getattr(v, k))
            s.add(existing)
            s.commit()
            s.refresh(existing)
            return existing

        s.add(v)
        s.commit()
        s.refresh(v)
        return v

@app.get("/discover", response_model=List[Venue])
def discover(
    q: str = "cafe",            # e.g., "cafe" or "restaurant"
    lat: float = 43.659,        # Portland, ME default
    lng: float = -70.256,
    radius_m: int = 2000,
    limit: int = 20
):
    api_key = os.getenv("GOOGLE_PLACES_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="Set GOOGLE_PLACES_KEY in your .env")

    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "key": api_key,
        "location": f"{lat},{lng}",
        "radius": radius_m,
        "keyword": q,
    }

    try:
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        data = r.json()
    except RequestException as e:
        raise HTTPException(status_code=502, detail=f"Places request failed: {e}")

    status = data.get("status")
    if status != "OK":
        # Surface the reason (e.g., REQUEST_DENIED, ZERO_RESULTS, OVER_QUERY_LIMIT)
        raise HTTPException(status_code=400, detail={"status": status, "error": data.get("error_message")})

    results = data.get("results", [])[:limit]

    # Upsert into our DB and return the rows
    out: List[Venue] = []
    for obj in results:
        try:
            out.append(upsert_venue_from_google(obj))
        except Exception as e:
            # Don't crash the whole request if a single row fails
            print("upsert error:", e)

    return out