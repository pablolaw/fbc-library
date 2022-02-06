from flask import Flask, session
from config import Config
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_session import Session
from flask_bootstrap import Bootstrap5
from elasticsearch import Elasticsearch
import logging

app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)
migrate = Migrate(app, db, render_as_batch=True)
login = LoginManager(app)
bootstrap = Bootstrap5(app)
login.login_view = 'login'
SESSION_TYPE = 'filesystem'
app.config.from_object(__name__)
elasticsearch = Elasticsearch([app.config['ELASTICSEARCH_URL']]) if app.config['ELASTICSEARCH_URL'] else None
Session(app)

from app import routes, models

if not app.debug:
    if app.config['LOG_TO_STDOUT']:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.INFO)
        app.logger.addHandler(stream_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info('Library application startup')