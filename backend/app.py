# app.py — FastAPI server for Atmosphere
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel, Field, Session, select, create_engine
from typing import Optional, List
from dotenv import load_dotenv
from requests import RequestException
import requests
import os
from pydantic import BaseModel

 

load_dotenv()  # load .env so GOOGLE_PLACES_KEY/DATABASE_URL are available

# ---------- Atmosphere API models for FE ----------
class AtmoReview(BaseModel):
    author: str | None = None
    rating: float | None = None
    text: str | None = None

class AtmoPlace(BaseModel):
    id: str
    name: str
    address: str | None = None
    google_rating: float | None = None
    photos: list[str] = []
    tags: list[str] = []
    summary: str | None = None
    source_count: int | None = 0
    reviews: list[AtmoReview] = []
    lat: float | None = None
    lng: float | None = None

class AtmoPlacesResp(BaseModel):
    places: list[AtmoPlace]

# ---- DB ----
DB_URL = os.getenv("DATABASE_URL", "sqlite:///atmosphere.db")
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, echo=False, connect_args=connect_args)

# ---- SQLModel tables ----
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

# single CORS is enough
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "*",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)

@app.get("/health")
def health():
    return {"ok": True}

# ------------- existing venue endpoints -------------
@app.get("/venues", response_model=List[Venue])
def list_venues(q: Optional[str] = None, limit: int = 25, offset: int = 0):
    with Session(engine) as s:
        stmt = select(Venue)
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(
                Venue.vibes.ilike(like)
                | Venue.name.ilike(like)
                | Venue.summary.ilike(like)
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

# ---------- Google Places helper ----------
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

# ------------- your original discover -------------
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
        raise HTTPException(status_code=400, detail={"status": status, "error": data.get("error_message")})

    results = data.get("results", [])[:limit]

    out: List[Venue] = []
    for obj in results:
        try:
            out.append(upsert_venue_from_google(obj))
        except Exception as e:
            print("upsert error:", e)

    return out

# Map filter names to Google Places API types
# Using 'type' parameter is more accurate than 'keyword' for finding places by category
FILTER_TYPE_MAP = {
    "coffee": "cafe",
    "pizza": "meal_takeaway",  # Will include pizza places
    "burgers": "restaurant",
    "sushi": "restaurant",
    "mexican": "restaurant",
    "italian": "restaurant",
    "seafood": "restaurant",
    "asian": "restaurant",
}

# ------------- NEW: frontend-friendly /places -------------
@app.get("/places", response_model=AtmoPlacesResp)
def get_places(
    lat: float,
    lng: float,
    radius: int = 5000,
    q: str = "",
    limit: int = 20,
):
    """
    Returns data in the exact shape the Atmosphere frontend expects.
    """
    api_key = os.getenv("GOOGLE_PLACES_KEY")

    # if we don't have a Google key, just send DB rows in that shape
    if not api_key:
        with Session(engine) as s:
            rows = s.exec(select(Venue).limit(limit)).all()
        return AtmoPlacesResp(
            places=[
                AtmoPlace(
                    id=v.id,
                    name=v.name,
                    address=v.address,
                    google_rating=None,
                    photos=[v.cover_img_url] if v.cover_img_url else [],
                    tags=[t.strip() for t in (v.vibes or "").split(",") if t.strip()],
                    summary=v.summary or None,
                    source_count=1,
                    reviews=[],
                    lat=v.lat,
                    lng=v.lng,
                )
                for v in rows
            ]
        )

    # call Google Nearby
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "key": api_key,
        "location": f"{lat},{lng}",
        "radius": radius,
    }
    
    # Use 'type' parameter for specific filters, 'keyword' for general searches
    query_lower = q.lower().strip()
    if query_lower == "coffee":
        # For coffee: search both cafes AND restaurants to find all places that serve coffee
        # We'll make two API calls and combine results
        all_results = []
        
        # First: get all cafes (type: "cafe")
        cafe_params = params.copy()
        cafe_params["type"] = "cafe"
        try:
            r1 = requests.get(url, params=cafe_params, timeout=12)
            r1.raise_for_status()
            cafe_data = r1.json()
            if cafe_data.get("status") == "OK":
                all_results.extend(cafe_data.get("results", []))
        except RequestException:
            pass  # Continue even if one fails
        
        # Second: get restaurants with "coffee" keyword (type: "restaurant" + keyword)
        restaurant_params = params.copy()
        restaurant_params["type"] = "restaurant"
        restaurant_params["keyword"] = "coffee"
        try:
            r2 = requests.get(url, params=restaurant_params, timeout=12)
            r2.raise_for_status()
            restaurant_data = r2.json()
            if restaurant_data.get("status") == "OK":
                all_results.extend(restaurant_data.get("results", []))
        except RequestException:
            pass
        
        # Remove duplicates by place_id
        seen_ids = set()
        unique_results = []
        for result in all_results:
            place_id = result.get("place_id")
            if place_id and place_id not in seen_ids:
                seen_ids.add(place_id)
                unique_results.append(result)
        
        results = unique_results[:limit]
        status = "OK" if results else "ZERO_RESULTS"
        
    elif query_lower in FILTER_TYPE_MAP:
        # For other category filters: use restaurant type with keyword
        params["type"] = "restaurant"
        params["keyword"] = q
        try:
            r = requests.get(url, params=params, timeout=12)
            r.raise_for_status()
            data = r.json()
        except RequestException as e:
            raise HTTPException(status_code=502, detail=f"Places request failed: {e}")
        status = data.get("status")
        if status not in ("OK", "ZERO_RESULTS"):
            raise HTTPException(status_code=400, detail={"status": status, "error": data.get("error_message")})
        results = data.get("results", [])[:limit]
        
    elif query_lower == "all" or not q:
        # Default: show restaurants
        params["type"] = "restaurant"
        try:
            r = requests.get(url, params=params, timeout=12)
            r.raise_for_status()
            data = r.json()
        except RequestException as e:
            raise HTTPException(status_code=502, detail=f"Places request failed: {e}")
        status = data.get("status")
        if status not in ("OK", "ZERO_RESULTS"):
            raise HTTPException(status_code=400, detail={"status": status, "error": data.get("error_message")})
        results = data.get("results", [])[:limit]
    else:
        # General search: use keyword with restaurant type
        params["type"] = "restaurant"
        params["keyword"] = q
        try:
            r = requests.get(url, params=params, timeout=12)
            r.raise_for_status()
            data = r.json()
        except RequestException as e:
            raise HTTPException(status_code=502, detail=f"Places request failed: {e}")
        status = data.get("status")
        if status not in ("OK", "ZERO_RESULTS"):
            raise HTTPException(status_code=400, detail={"status": status, "error": data.get("error_message")})
        results = data.get("results", [])[:limit]

    out_places: list[AtmoPlace] = []
    for obj in results:
        place_id = obj.get("place_id")
        name = obj.get("name")
        address = obj.get("vicinity") or obj.get("formatted_address")
        rating = obj.get("rating")
        types = obj.get("types") or []

        # ✅ MULTI-PHOTO LOGIC
        photos = obj.get("photos") or []
        photo_urls: list[str] = []
        if photos:
            for p in photos[:5]:  # take up to 5 photos
                pref = p.get("photo_reference")
                if pref:
                    photo_urls.append(
                        "https://maps.googleapis.com/maps/api/place/photo"
                        f"?maxwidth=1600&photoreference={pref}&key={api_key}"
                    )

        # optional: keep your DB warm
        try:
            upsert_venue_from_google(obj)
        except Exception:
            pass

        # Get lat/lng from geometry
        geometry = obj.get("geometry") or {}
        location = geometry.get("location") or {}
        lat = location.get("lat")
        lng = location.get("lng")
        
        out_places.append(
            AtmoPlace(
                id=place_id,
                name=name,
                address=address,
                google_rating=rating,
                photos=photo_urls,
                tags=[t.replace("_", " ") for t in types[:3]],
                summary=None,   # fill later
                source_count=len(types),
                reviews=[],     # fill later w/ place details
                lat=lat,
                lng=lng,
            )
        )

    return AtmoPlacesResp(places=out_places)
 
@app.get("/config/maps-key")
def get_maps_key():
    """Return Google Maps API key for frontend use."""
    api_key = os.getenv("GOOGLE_PLACES_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GOOGLE_PLACES_KEY not set")
    return {"maps_key": api_key}

@app.get("/places/{place_id}/photos")
def get_place_photos(place_id: str, max_photos: int = 10):
    """Return multiple photo URLs for a place via Google Place Details."""
    api_key = os.getenv("GOOGLE_PLACES_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="Set GOOGLE_PLACES_KEY in your .env")

    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "key": api_key,
        "place_id": place_id,
        "fields": "photos"
    }
    try:
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        data = r.json()
    except RequestException as e:
        raise HTTPException(status_code=502, detail=f"Place details failed: {e}")

    if data.get("status") != "OK":
        return {"photos": []}

    photos = (data.get("result") or {}).get("photos") or []
    out = []
    for p in photos[:max_photos]:
        pref = p.get("photo_reference") or p.get("photoreference")
        if pref:
            out.append(
                "https://maps.googleapis.com/maps/api/place/photo"
                f"?maxwidth=1600&photoreference={pref}&key={api_key}"
            )
    return {"photos": out}
