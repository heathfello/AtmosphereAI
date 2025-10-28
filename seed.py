# seed.py
from sqlmodel import Session
from app import engine, Venue, SQLModel   # NOTE: import SQLModel

#  ensure tables exist
SQLModel.metadata.create_all(engine)

seed = [
    Venue(
        id="tandem",
        name="Tandem Coffee & Bakery",
        address="742 Congress St, Portland, ME",
        cover_img_url="https://images.unsplash.com/photo-1504754524776-8f4f37790ca0?w=1200",
        vibes="quiet,sunny,outlets",
        summary="Sunny bakery, spaced tables, light music — good for light laptop work.",
    ),
    Venue(
        id="lukes",
        name="Luke's Lobster Portland Pier",
        address="60 Portland Pier, Portland, ME",
        cover_img_url="https://images.unsplash.com/photo-1544025162-d76694265947?w=1200",
        vibes="waterfront,seafood,sunny",
        summary="Casual spot on the pier with harbor views; sunny and breezy when the weather is good.",
    ),
    Venue(
        id="bard",
        name="Bard Coffee",
        address="185 Middle St, Portland, ME",
        cover_img_url="https://images.unsplash.com/photo-1498804103079-a6351bb05096?w=1200",
        vibes="bright,downtown,communal",
        summary="Downtown spot with big windows and communal tables; lively at peaks.",
    ),
]

if __name__ == "__main__":
    with Session(engine) as s:
        for v in seed:
            if not s.get(Venue, v.id):
                s.add(v)
        s.commit()
    print("Seeded venues ✓")
