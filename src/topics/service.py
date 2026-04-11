from slugify import slugify
from sqlmodel import Session, select

from src.topics.models import Topic
from src.topics.schemas import TopicCreate, TopicNode, TopicResponse


def create(session: Session, data: TopicCreate) -> Topic:
    slug = slugify(data.name)
    topic = Topic(
        name=data.name,
        slug=slug,
        parent_id=data.parent_id,
        description=data.description,
    )
    session.add(topic)
    session.commit()
    session.refresh(topic)
    return topic


def get_by_id(session: Session, topic_id: int) -> Topic | None:
    return session.get(Topic, topic_id)


def get_by_slug(session: Session, slug: str) -> Topic | None:
    return session.exec(select(Topic).where(Topic.slug == slug)).first()


def list_all(session: Session) -> list[Topic]:
    return list(session.exec(select(Topic).order_by(Topic.parent_id.asc(), Topic.name)).all())


def list_main_topics(session: Session) -> list[Topic]:
    return list(
        session.exec(
            select(Topic).where(Topic.parent_id == None).order_by(Topic.name)  # noqa: E711
        ).all()
    )


def list_sub_topics(session: Session, parent_id: int) -> list[Topic]:
    return list(
        session.exec(
            select(Topic).where(Topic.parent_id == parent_id).order_by(Topic.name)
        ).all()
    )


def get_topic_tree(session: Session) -> list[TopicNode]:
    main_topics = list_main_topics(session)
    tree = []
    for main in main_topics:
        sub_topics = list_sub_topics(session, main.id)
        node = TopicNode(
            id=main.id,
            name=main.name,
            slug=main.slug,
            description=main.description,
            sub_topics=[
                TopicNode(id=s.id, name=s.name, slug=s.slug, description=s.description)
                for s in sub_topics
            ],
        )
        tree.append(node)
    return tree


def seed_topics(session: Session, seed_data: list[dict]) -> None:
    """Idempotent seed — skips topics that already exist by slug."""
    # First pass: create main topics (parent_slug == None)
    for item in seed_data:
        if item["parent_slug"] is None:
            if not get_by_slug(session, item["slug"]):
                topic = Topic(
                    name=item["name"],
                    slug=item["slug"],
                    parent_id=None,
                    description=item.get("description"),
                )
                session.add(topic)
    session.commit()

    # Second pass: create sub-topics (resolve parent slug → id)
    for item in seed_data:
        if item["parent_slug"] is not None:
            if not get_by_slug(session, item["slug"]):
                parent = get_by_slug(session, item["parent_slug"])
                if parent:
                    topic = Topic(
                        name=item["name"],
                        slug=item["slug"],
                        parent_id=parent.id,
                        description=item.get("description"),
                    )
                    session.add(topic)
    session.commit()
