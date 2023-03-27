import sqlalchemy
from sqlalchemy.orm import declarative_base, sessionmaker


__all__ = [
    'create_seen_users_table',
    'insert_seen_user_data',
    'select_seen_user'
]


Base = declarative_base()


class SeenUsers(Base):
    __tablename__ = "seen_users"

    id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
    vk_id = sqlalchemy.Column(sqlalchemy.Integer, unique=False)
    seen_user_id = sqlalchemy.Column(sqlalchemy.Integer, unique=False)


DSN = "sqlite:///seen_users.db"
engine = sqlalchemy.create_engine(DSN)
Session = sessionmaker(bind=engine)
session = Session()


def create_seen_users_table():
    global engine
    Base.metadata.create_all(engine)


def insert_seen_user_data(vk_id, seen_user_id):
    seen_user = SeenUsers(vk_id=vk_id, seen_user_id=seen_user_id)
    session.add(seen_user)
    session.commit()


def select_seen_user(vk_id, seen_user_id):
    result = session.query(SeenUsers).filter(SeenUsers.vk_id == vk_id, SeenUsers.seen_user_id == seen_user_id).first()
    session.close()
    return result
