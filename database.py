from datetime import datetime
from sqlalchemy import create_engine, String, Integer, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from config import DATABASE_URL, RESCUE_ROUNDS

db_url = DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = "postgresql+psycopg://" + db_url[len("postgres://"):]
elif db_url.startswith("postgresql://"):
    db_url = "postgresql+psycopg://" + db_url[len("postgresql://"):]

connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
engine = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Game(Base):
    __tablename__ = "games"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NOTE: intentionally NOT unique. A chat can host many games over time
    # (one finished game must not block a new /zombie in the same chat).
    # "the active game for this chat" is always the most recent row.
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    creator_id: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(30), default="lobby")
    round_no: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    phase_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mode: Mapped[str] = mapped_column(String(20), default="classic")
    winner: Mapped[str | None] = mapped_column(String(30), nullable=True)
    rescue_eta: Mapped[int] = mapped_column(Integer, default=RESCUE_ROUNDS)
    wrong_eliminations: Mapped[int] = mapped_column(Integer, default=0)


class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(40), nullable=True)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    alive: Mapped[bool] = mapped_column(Boolean, default=True)
    infection: Mapped[str] = mapped_column(String(20), default="healthy")
    score: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # zombie-only: round number before which "follow"/"disrupt"/"fake" are on cooldown
    special_cooldown_until: Mapped[int] = mapped_column(Integer, default=0)
    # journalist-only: whether they've used their one-time /publish
    has_published: Mapped[bool] = mapped_column(Boolean, default=False)


class NightAction(Base):
    __tablename__ = "night_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    round_no: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[int] = mapped_column(Integer)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    result: Mapped[str | None] = mapped_column(Text, nullable=True)


class Vote(Base):
    __tablename__ = "votes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    round_no: Mapped[int] = mapped_column(Integer)
    voter_id: Mapped[int] = mapped_column(Integer)
    target_id: Mapped[int] = mapped_column(Integer)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    round_no: Mapped[int] = mapped_column(Integer, default=0)
    owner_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)
    misleading: Mapped[bool] = mapped_column(Boolean, default=False)
    public: Mapped[bool] = mapped_column(Boolean, default=False)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    round_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    effect: Mapped[str] = mapped_column(String(80))


class PersonalGoal(Base):
    __tablename__ = "personal_goals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    player_id: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class GameHistory(Base):
    __tablename__ = "game_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    round_no: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str] = mapped_column(Text)


async def init_db():
    Base.metadata.create_all(engine)
