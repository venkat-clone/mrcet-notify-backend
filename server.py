import datetime
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
import json
import os
import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy import create_engine, Column, Integer, String,DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
import pytz
from sqlalchemy import or_
from datetime import datetime
from typing import Optional
from enum import Enum
from dotenv import load_dotenv
from sqlalchemy.orm.session import Session
load_dotenv()

# Replace the hardcoded DATABASE_URL with environment variable
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///notifications.db')  # Fallback to SQLite if not set
PORT:int = os.getenv('PORT', 8000)  # type: ignore # Fallback to SQLite if not set
FIREBASE_PATH:int = os.getenv('FIREBASE_PATH', 'mrec-notifications-firebase-adminsdk-fbsvc-409ef00a55.json')  # type: ignore # Fallback to SQLite if not set
app = FastAPI()
URL = "https://mrec.ac.in/ExamsDashboard"

# Initialize Firebase Admin SDK
cred = credentials.Certificate(FIREBASE_PATH)
firebase_admin.initialize_app(cred)

# Database setup
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
timezone = pytz.timezone('Asia/Kolkata')
# Model
class Notification(Base):
    __tablename__ = "notifications"
    
    id = Column(Integer, primary_key=True, index=True)
    text = Column(String, nullable=False)
    url = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.now(timezone))
    updated_at = Column(DateTime, default=datetime.now(timezone), onupdate=datetime.now(timezone))


    def to_dict(self):
        return {
            "id": self.id,
            "text": self.text,
            "url": self.url,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def scrape_notifications():
    response = requests.get(URL)
    if response.status_code != 200:
        print(f"Failed to fetch the page. Status code: {response.status_code}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    
    # Find all <li> elements with the class "news-item"
    notifications = soup.select("li.news-item")
    scraped_notifications = []

    for notification in notifications:
        # Extract the text and link from the <a> tag
        link_tag = notification.find("a")
        if link_tag:
            text = link_tag.text.strip()
            href = link_tag.get("href") # type: ignore
            if type(href)==str:
                # Full URL construction if needed
                if href and not href.startswith("http"):
                    # Prepend https://mrec.ac.in if href doesn't start with http or https
                    full_url = f"https://mrec.ac.in{href}"
                    full_url = full_url.replace('http:','https:')
                else:
                    # Use the original href if it already starts with http or https
                    full_url = href
                scraped_notifications.append({"text": text, "url": full_url})
    
    return scraped_notifications

class SortBy(str, Enum):
    NEWEST = "newest"
    OLDEST = "oldest"
    TITLE = "title"

def load_notifications(
    skip: int = 0, 
    limit: int = 10, 
    search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    sort_by: SortBy = SortBy.NEWEST
):
    db = SessionLocal()
    query = db.query(Notification)
    
    if search:
        print(f'searching for notifications {search}')
        query = query.filter(
            or_(
                Notification.text.ilike(f"%{search}%"),
                Notification.url.ilike(f"%{search}%")
            )
        )
    
    if start_date:
        query = query.filter(Notification.created_at >= start_date)
    
    if end_date:
        query = query.filter(Notification.created_at <= end_date)
    
    # Apply sorting
    if sort_by == SortBy.NEWEST:
        query = query.order_by(Notification.created_at.desc())
    elif sort_by == SortBy.OLDEST:
        query = query.order_by(Notification.created_at.asc())
    elif sort_by == SortBy.TITLE:
        query = query.order_by(Notification.text.asc())
    
    total = query.count()
    notifications = query.offset(skip).limit(limit).all()
    
    db.close()
    return {
        "total": total,
        "notifications": [notification.to_dict() for notification in notifications]
    }

def save_notifications(notifications):
    notificationsAdded = []
    db: Session = SessionLocal()
    for notification_data in notifications:
        notification = Notification(
            text=notification_data["text"].strip(),
            url=notification_data["url"].strip()
        )
        try:
            db.add(notification)
            db.commit()
            notificationsAdded.append(notification)
        except IntegrityError:
            db.rollback()
    db.close()
    return notificationsAdded

def delete_notification(notification_id: int):
    db = SessionLocal()
    notification = db.query(Notification).filter(Notification.id == notification_id).first()
    if notification:
        db.delete(notification)
        db.commit()
        db.close()
        return True
    db.close()
    return False

def get_notification_by_id(notification_id: int):
    db = SessionLocal()
    notification = db.query(Notification).filter(Notification.id == notification_id).first()
    db.close()
    return notification.to_dict() if notification else None

def send_firebase_notification(notification):
    print('New Notification sedding to Users')
    message = messaging.Message(
        notification=messaging.Notification(
            title="New Notification",
            body=notification["text"]
        ),
        data={
            "click_action": notification["url"],
            'url':notification["url"]
        },
        topic="all"
    )
    
    try:
        response = messaging.send(message)
        return 200, {"message_id": response}
    except Exception as e:
        return 500, {"error": str(e)}

@app.get("/notifications")
def get_notifications(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    query: str = Query(None, description="Search text in notification content"),
    start_date: Optional[datetime] = Query(None, description="Filter notifications from this date"),
    end_date: Optional[datetime] = Query(None, description="Filter notifications until this date"),
    sort_by: SortBy = Query(SortBy.NEWEST, description="Sort notifications by: newest, oldest, or title")
):
    skip = (page - 1) * limit
    return load_notifications(
        skip=skip,
        limit=limit,
        search=query,
        start_date=start_date,
        end_date=end_date,
        sort_by=sort_by
    )

@app.get("/scrape")
def scrape_and_store_notifications():
    print('notification scraping started')
    notifications = scrape_notifications()
    newNotifications = save_notifications(notifications)
    for notification in newNotifications:
        send_firebase_notification(notification)
    return {"message": "Notifications scraped, saved, and sent successfully"}

@app.delete("/notifications/{notification_id}")
async def delete_notification_endpoint(notification_id: int):
    if delete_notification(notification_id):
        return {"message": f"Notification {notification_id} deleted successfully"}
    raise HTTPException(status_code=404, detail="Notification not found")

@app.get("/notifications/{notification_id}/resend")
async def resend_notification(notification_id: int):
    notification = get_notification_by_id(notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    status_code, response = send_firebase_notification(notification)
    if status_code == 200:
        return {"message": f"Notification {notification_id} resent successfully", "response": response}
    raise HTTPException(status_code=500, detail="Failed to resend notification")

if __name__ == "__main__":
    init_db()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
