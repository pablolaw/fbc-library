from datetime import datetime, timedelta
from app import db, login, elasticsearch, app
from flask_login import UserMixin
from flask import url_for
from werkzeug.security import generate_password_hash, check_password_hash
import urllib.request, json
from urllib.error import HTTPError
from elasticsearch_dsl import Document, Text, Search, Q
from elasticsearch_dsl.query import MultiMatch, Match
from sqlalchemy import extract
from enum import Enum, auto


class SearchableMixin(object): # COULD MOVE FUNCTIONS DEPENDENT ON ELASTICSEARCH OUTSIDE

	def get_document(self):
		raise NotImplementedError("Need to define document structure for model")

	@classmethod
	def search(cls, query, page, per_page):
		# query is a query object (Q)
		s = Search(index=cls.__tablename__, using=elasticsearch)
		s = s[(page - 1) * per_page: page * per_page]
		s = s.query(query)
		response = s.execute()
		ids = [int(hit.meta.id) for hit in response.hits]
		total = response.hits.total.value
		if total == 0:
			return cls.query.filter_by(id=0), 0
		when = [(_id, i) for (i, _id) in enumerate(ids)]
		return cls.query.filter(cls.id.in_(ids)).order_by(
			db.case(when, value=cls.id)), total

	@classmethod
	def before_commit(cls, session):
		session._changes = {
			'add': list(session.new),
			'update': list(session.dirty),
			'delete': list(session.deleted)
		}

	@classmethod
	def after_commit(cls, session):
		for obj in session._changes['add']:
			if isinstance(obj, SearchableMixin):
				obj.get_document().save(index=obj.__tablename__, using=elasticsearch)
		for obj in session._changes['update']:
			if isinstance(obj, SearchableMixin):
				obj.get_document().save(index=obj.__tablename__, using=elasticsearch)
		for obj in session._changes['delete']:
			if isinstance(obj, SearchableMixin):
				s = Search(using=elasticsearch, index=obj.__tablename__).query("match", _id=obj.id)
				s.delete()
		session._changes = None

	@classmethod
	def reindex(cls):
		# getattr(locals().get(f'{cls.__name__}Index'), 'init')(index=cls.__tablename__) # Create index and populate mappings
		for obj in cls.query:
			obj.get_document().save(index=cls.__tablename__, using=elasticsearch)

db.event.listen(db.session, 'before_commit', SearchableMixin.before_commit)
db.event.listen(db.session, 'after_commit', SearchableMixin.after_commit)


book_authors = db.Table('book_authors',
	db.Column('book_id', db.Integer, db.ForeignKey('book.id'), primary_key=True),
	db.Column('author_id', db.Integer, db.ForeignKey('author.id'), primary_key=True)
	)


class User(UserMixin, db.Model):
	id = db.Column(db.Integer, primary_key=True)
	username = db.Column(db.String(64), index=True, unique=True)
	password_hash = db.Column(db.String(128))

	def __repr__(self):
		return f'<User {self.username}>'

	def set_password(self, password):
		self.password_hash = generate_password_hash(password)

	def check_password(self, password):
		return check_password_hash(self.password_hash, password)

@login.user_loader
def load_user(id):
	return User.query.get(int(id))

class Category(db.Model):
	id = db.Column(db.Integer, primary_key=True)
	name = db.Column(db.String(32), nullable=False, index=True)
	books = db.relationship('Book', backref='book_category', lazy='dynamic')

	def __repr__(self):
		return f'<Category {self.name}>'

	@staticmethod
	def get_category_names():
		return [obj.name for obj in Category.query.all()]


class Author(db.Model):
	id = db.Column(db.Integer, primary_key=True)
	name = db.Column(db.String(64), nullable=False, index=True)

	def __repr__(self):
		return f'<Author {self.name}>'

	def get_works(self):
		return Book.query.join(book_authors, 
			(book_authors.c.book_id == Book.id)).filter(
			book_authors.c.author_id == self.id).order_by(Book.full_title)

class BookStatus(Enum):
	ON_LOAN = auto()
	ON_SHELF = auto()
	MISSING = auto()

class Book(SearchableMixin, db.Model):
	id = db.Column(db.Integer, primary_key=True)
	isbn_10 = db.Column(db.String(10), index=True, nullable=True)
	isbn_13 = db.Column(db.String(13), index=True, unique=True, nullable=False) # Older books dont have ISBN-13
	full_title = db.Column(db.String(100), nullable=False)
	authors = db.relationship('Author', secondary=book_authors, primaryjoin=(book_authors.c.book_id == id), lazy='subquery', backref='works')
	pages = db.Column(db.Integer)
	publish_date = db.Column(db.Date)
	category = db.Column(db.Integer, db.ForeignKey('category.id')) # We assume that each book has at most 1 category
	cover = db.Column(db.Text)
	loans = db.relationship('Loan', backref='loaned_book', lazy='dynamic')

	def __repr__(self):
		return f'<Book {self.id}: {self.full_title}>'

	# def cover_img(self, size):
	# 	if size not in ['S', 'M', 'L']:
	# 		return None
	# 	url = f'https://covers.openlibrary.org/b/isbn/{self.isbn_10}-{size}.jpg?default=False'
	# 	try:
	# 		response = urllib.request.urlopen(url)
	# 		cover_url = f'https://covers.openlibrary.org/b/isbn/{self.isbn_10}-{size}.jpg?default=False'
	# 	except HTTPError:
	# 		cover_url = url_for('static', filename='nocover.jpg')
	# 	return cover_url

	def _author_repr(self):
		# Return a str representation of authors
		return ', '.join([a.name for a in self.authors])

	def get_document(self):
		return BookIndex(
			_id=self.id,
			full_title=self.full_title,
			authors = self._author_repr()
		)

	@staticmethod
	def init_index():
		BookIndex.init(index='book')

	def _get_status(self):
		curr_loan = self.get_current_loan()
		if not curr_loan:
			return BookStatus.ON_SHELF
		if curr_loan.is_overdue():
			return BookStatus.MISSING
		return BookStatus.ON_LOAN

	def is_on_loan(self):
		return self._get_status() == BookStatus.ON_LOAN

	def is_on_shelf(self):
		return self._get_status() == BookStatus.ON_SHELF

	def is_missing(self):
		return self._get_status() == BookStatus.MISSING

	def get_current_loan(self):
		return self.loans.filter_by(returned=False).first()


	@staticmethod
	def search_keyword(query_str, page, per_page):
		q = Q("multi_match", query=query_str, fields=['*'], fuzziness=1)
		return Book.search(q, page, per_page)

	@staticmethod
	def search_title(query_str, page, per_page):
		q = Q("match", full_title={'query':query_str, 'fuzziness': 1})
		return Book.search(q, page, per_page)

	@staticmethod
	def search_author(query_str, page, per_page, exact=False):
		if exact:
			books = Book.query.join(Book.authors).filter_by(name=query_str)
			total = len(books.all())
			return books.paginate(page, app.config['BOOKS_PER_PAGE'], False), total
		q = Q("match", authors={'query': query_str, 'fuzziness': 1})
		return Book.search(q, page, per_page)

	@staticmethod
	def search_advanced(query_dict, page, per_page):
		# Returns a paginated object for books
		if len(query_dict) < 1:
			return None, 0

		# Fuzzy search for book title and authors
		q1, q2 = None, None
		if 'full_title' in query_dict:
			q1 = Q("match", full_title={'query':query_dict['full_title'], 'fuzziness': 1})
			# query_dict.pop('full_title')
		if 'authors' in query_dict:
			q2 = Q("match", authors={'query': query_dict['authors'], 'fuzziness': 1})
			# query_dict.pop('authors')
		q = q1 & q2 if (q1 is not None and q2 is not None) else (q1 or q2)

		if q:
			books, _ = Book.search(q, page, per_page)
		else:
			books = Book.query

		# Exact search for other book properties
		if 'category' in query_dict:
			books = books.join(Book.book_category).filter_by(name=query_dict['category'])
			# query_dict.pop('category')
		if 'publish_date' in query_dict:
			books = books.filter(extract('year', Book.publish_date) == query_dict['publish_date']) # query date must be int
			# query_dict.pop('publish_date')

		# Get the rest
		# if len(query_dict) > 0:
		# 	books = books.filter_by(**query_dict)
		total = len(books.all())
		books = books.paginate(page, app.config['BOOKS_PER_PAGE'], False)
		return books, total


class BookIndex(Document):
	full_title = Text()
	authors = Text()

class Loan(db.Model):
	id = db.Column(db.Integer, primary_key=True)
	book_id = db.Column(db.Integer, db.ForeignKey('book.id'))
	phone_num = db.Column(db.String(14), nullable=False, index=True)
	loanee = db.Column(db.String(32), nullable=False) # No registration required
	out_timestamp = db.Column(db.Date, index=True, default=datetime.today().date) # Date book was taken out
	in_timestamp = db.Column(db.Date, index=True, nullable=False) # Date book must be returned or was returned
	returned = db.Column(db.Boolean, index=True, nullable=False, default=False) # Loan is still open or closed

	def __repr__(self):
		return f'<Loan book={self.book_id} to {self.loanee} at {self.out_timestamp}>'

	def extend_loan_period(self, delta=1, unit='weeks'):
		# timedelta is length of time to extend loan period
		td = timedelta(**{unit: delta})
		if td.days > 0 and not self.returned:
			self.in_timestamp += td
			return True
		return False

	def close(self):
		if not self.returned:
			self.in_timestamp = datetime.now().date()
			self.returned = True
			return True
		return False

	def is_active(self):
		# Return True iff book is still out on loan 
		return not self.returned

	def is_overdue(self):
		return self.is_active() and (self.in_timestamp < datetime.now().date())

	@staticmethod
	def get_loans(phone_num):
		# Returns a query object
		return Loan.query.filter_by(phone_num=phone_num).filter(Loan.book_id.isnot(None))

	@staticmethod
	def get_expiring(delta=1, unit='weeks'):
		# returns a query object
		td = timedelta(**{unit: delta})
		if td.days > 0:
			date_limit = datetime.today().date() + td
			return Loan.query.filter(Loan.returned == False, Loan.in_timestamp <= date_limit).order_by(Loan.phone_num)
		return Loan.query.filter_by(id=0)





