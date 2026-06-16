from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .config import settings

_IS_SQLITE = settings.DATABASE_URL.startswith("sqlite")

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if _IS_SQLITE else {},
    pool_pre_ping=True,  # aman untuk SQLite & Postgres; cegah "stale connection"
)


# ─── Hardening SQLite untuk akses multi-cabang yang konkuren ──────────────────
# Hanya berlaku saat memakai SQLite; tidak mengganggu PostgreSQL/MySQL.
if _IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")    # baca konkuren + 1 penulis (anti-lock)
        cur.execute("PRAGMA synchronous=NORMAL;")  # seimbang antara aman & cepat
        cur.execute("PRAGMA busy_timeout=5000;")   # tunggu lock s/d 5 dtk, jangan langsung error
        # CATATAN: PRAGMA foreign_keys=ON sengaja BELUM diaktifkan. Beberapa path
        # masih hard-delete master (category/brand/unit) tanpa kebijakan ondelete,
        # sehingga mengaktifkannya sekarang akan memblokir delete yang dipakai.
        # Aktifkan di fase berikutnya setelah ondelete (SET NULL/RESTRICT) ditetapkan.
        cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
