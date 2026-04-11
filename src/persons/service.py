from datetime import UTC, datetime

from slugify import slugify
from sqlmodel import Session, select

from src.persons.models import Person
from src.persons.schemas import PersonCreate, PersonUpdate


def create(session: Session, data: PersonCreate) -> Person:
    slug = slugify(data.name)
    person = Person(
        name=data.name,
        slug=slug,
        platform=data.platform,
        platform_handle=data.platform_handle,
        bio=data.bio,
    )
    session.add(person)
    session.commit()
    session.refresh(person)

    # Keep staged compatibility: every person has a linked channel row.
    from src.channels import service as channels_service

    channels_service.ensure_from_person(session, person)
    return person


def get_by_id(session: Session, person_id: int) -> Person | None:
    return session.get(Person, person_id)


def get_by_slug(session: Session, slug: str) -> Person | None:
    return session.exec(select(Person).where(Person.slug == slug)).first()


def get_by_platform_handle(session: Session, platform_handle: str) -> Person | None:
    return session.exec(
        select(Person).where(Person.platform_handle == platform_handle)
    ).first()


def list_all(session: Session) -> list[Person]:
    return list(session.exec(select(Person).order_by(Person.name)).all())


def update(session: Session, person: Person, data: PersonUpdate) -> Person:
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(person, key, value)
    person.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(person)
    session.commit()
    session.refresh(person)

    # Propagate handle/bio updates to linked channel when present.
    from src.channels import service as channels_service

    channels_service.ensure_from_person(session, person)
    return person
