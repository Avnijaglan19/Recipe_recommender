import os

from flask import Flask
from dotenv import load_dotenv
load_dotenv()
try:
    from .routes import register_routes
except ImportError:
    from routes import register_routes



BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
app.secret_key = os.getenv("86c80d79917586dac5ffb08d06b02032cc16439595feaf82ef78c96184902d87", "dev-secret-change-me")

register_routes(app)

if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        debug=True,
    )