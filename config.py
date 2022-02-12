import os
from dotenv import load_dotenv
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

class Config(object):
	ELASTICSEARCH_URL = os.environ.get('ELASTICSEARCH_URL') or 'http://localhost:9200'
	SECRET_KEY = os.environ.get('SECRET_KEY')
	LIBRARIAN_PASSWORD = os.environ.get('LIBRARIAN_PASSWORD')
	SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', '').replace(
		'postgres://', 'postgresql://') or \
		'sqlite:///' + os.path.join(basedir, 'app.db')
	LOG_TO_STDOUT = os.environ.get('LOG_TO_STDOUT')
	SQLALCHEMY_TRACK_MODIFICATIONS = False
	BOOKS_PER_PAGE = 10
	LOANS_PER_PAGE = 5
	MAX_NUMBER_OF_COPIES = 10