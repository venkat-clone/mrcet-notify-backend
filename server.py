import datetime
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
import json
import os
import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy import create_engine, Column, Integer, String,DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
import pytz

app = FastAPI()
URL = "https://mrec.ac.in/ExamsDashboard"
DATABASE_URL = "sqlite:///notifications.db"

# Initialize Firebase Admin SDK
cred = credentials.Certificate("mrec-notifications-firebase-adminsdk-fbsvc-409ef00a55.json")
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
    created_at = Column(DateTime, default=datetime.datetime.now(timezone))
    updated_at = Column(DateTime, default=datetime.datetime.now(timezone), onupdate=datetime.datetime.now(timezone))


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

def load_notifications():
    db = SessionLocal()
    notifications = db.query(Notification).all()
    db.close()
    return [notification.to_dict() for notification in notifications]

def save_notifications(notifications):
    db = SessionLocal()
    for notification_data in notifications:
        notification = Notification(
            text=notification_data["text"].strip(),
            url=notification_data["url"].strip()
        )
        try:
            db.add(notification)
            db.commit()
        except IntegrityError:
            db.rollback()
    db.close()

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
def get_notifications():
    return load_notifications()

@app.get("/scrape")
def scrape_and_store_notifications():
    notifications = scrape_notifications()
    save_notifications(notifications)
    for notification in notifications:
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

# Initialize the database and run the server
if __name__ == "__main__":
    init_db()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
