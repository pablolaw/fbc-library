import os
basedir = os.path.abspath(os.path.dirname(__file__))

class Config(object):
	ELASTICSEARCH_URL = os.environ.get('ELASTICSEARCH_URL')
	SECRET_KEY = os.environ.get('SECRET_KEY') or 'dummy-key'
	SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///' + os.path.join(basedir, 'app.db')
	SQLALCHEMY_TRACK_MODIFICATIONS = False
	BOOKS_PER_PAGE = 10
	LOANS_PER_PAGE = 5