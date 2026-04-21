from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/api/topics", tags=["topics"])


@router.get("", response_model=List[schemas.TopicOut])
def list_topics(db: Session = Depends(get_db)):
    return db.query(models.Topic).order_by(models.Topic.id).all()


@router.post("", response_model=schemas.TopicOut, status_code=status.HTTP_201_CREATED)
def create_topic(payload: schemas.TopicCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"user_ids"})
    topic = models.Topic(**data)

    if payload.user_ids:
        users = db.query(models.User).filter(models.User.id.in_(payload.user_ids)).all()
        topic.users = users

    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


@router.get("/{topic_id}", response_model=schemas.TopicOut)
def get_topic(topic_id: int, db: Session = Depends(get_db)):
    topic = db.get(models.Topic, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    return topic


@router.put("/{topic_id}", response_model=schemas.TopicOut)
def update_topic(topic_id: int, payload: schemas.TopicUpdate, db: Session = Depends(get_db)):
    topic = db.get(models.Topic, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    data = payload.model_dump(exclude_unset=True, exclude={"user_ids"})
    for field, value in data.items():
        setattr(topic, field, value)

    if payload.user_ids is not None:
        users = db.query(models.User).filter(models.User.id.in_(payload.user_ids)).all()
        topic.users = users

    db.commit()
    db.refresh(topic)
    return topic


@router.delete("/{topic_id}", response_model=schemas.MessageResponse)
def delete_topic(topic_id: int, db: Session = Depends(get_db)):
    topic = db.get(models.Topic, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    db.delete(topic)
    db.commit()
    return {"message": f"Topic {topic_id} deleted"}


@router.post("/{topic_id}/users/{user_id}", response_model=schemas.TopicOut)
def add_user_to_topic(topic_id: int, user_id: int, db: Session = Depends(get_db)):
    topic = db.get(models.Topic, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    user = db.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user not in topic.users:
        topic.users.append(user)
        db.commit()
        db.refresh(topic)
    return topic


@router.delete("/{topic_id}/users/{user_id}", response_model=schemas.TopicOut)
def remove_user_from_topic(topic_id: int, user_id: int, db: Session = Depends(get_db)):
    topic = db.get(models.Topic, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    user = db.get(models.User, user_id)
    if user and user in topic.users:
        topic.users.remove(user)
        db.commit()
        db.refresh(topic)
    return topic
